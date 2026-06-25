"""AWS provider — 平级账号版本（无 Org/AssumeRole）。

凭据 dict: {"access_key_id", "secret_access_key", "session_token"?}
流量统计走 SSH，不再调 CloudWatch。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import boto3
from botocore.config import Config

from app.providers.base import (
    CreateInstanceSpec,
    FirewallRule,
    FreeTierItem,
    Instance,
    SecurityGroupBrief,
    TrafficStat,
)

log = logging.getLogger(__name__)

_cfg = Config(retries={"max_attempts": 3, "mode": "standard"})


# 系统别名 → SSM Parameter 路径（AWS 官方维护，按 region 自动给最新 AMI）
# 文档: https://docs.aws.amazon.com/systems-manager/latest/userguide/parameter-store-public-parameters-ami.html
SSM_AMI_ALIASES = {
    "al2023": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64",
    "al2023-arm64": "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64",
    "ubuntu-24.04": "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id",
    "ubuntu-24.04-arm64": "/aws/service/canonical/ubuntu/server/24.04/stable/current/arm64/hvm/ebs-gp3/ami-id",
    "ubuntu-22.04": "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id",
    "ubuntu-22.04-arm64": "/aws/service/canonical/ubuntu/server/22.04/stable/current/arm64/hvm/ebs-gp2/ami-id",
    "debian-12": "/aws/service/debian/release/12/latest/amd64",
    "debian-12-arm64": "/aws/service/debian/release/12/latest/arm64",
}


class AWSProvider:
    name = "aws"

    def __init__(self, creds: dict, default_region: str = "us-east-1"):
        self._creds = creds
        self._default_region = default_region

    def _client(self, service: str, region: Optional[str] = None):
        return boto3.client(
            service,
            region_name=region or self._default_region,
            aws_access_key_id=self._creds.get("access_key_id"),
            aws_secret_access_key=self._creds.get("secret_access_key"),
            aws_session_token=self._creds.get("session_token") or None,
            config=_cfg,
        )

    def list_regions(self) -> list[str]:
        ec2 = self._client("ec2")
        return [r["RegionName"] for r in ec2.describe_regions()["Regions"]]

    def list_instances(self, region: Optional[str] = None) -> list[Instance]:
        ec2 = self._client("ec2", region)
        result: list[Instance] = []
        # 先收集所有 volume id，再一次性批量查 size
        volume_ids: set[str] = set()
        raw_instances: list[tuple[dict, str]] = []
        for page in ec2.get_paginator("describe_instances").paginate():
            for res in page["Reservations"]:
                for i in res["Instances"]:
                    state = (i.get("State") or {}).get("Name", "")
                    if state in ("terminated", "shutting-down"):
                        continue
                    raw_instances.append((i, region or self._default_region))
                    for bdm in i.get("BlockDeviceMappings", []):
                        vid = (bdm.get("Ebs") or {}).get("VolumeId")
                        if vid:
                            volume_ids.add(vid)
        # 批量查 volumes
        vol_size_map: dict[str, int] = {}
        if volume_ids:
            try:
                # describe_volumes 一次最多 200 个，分批
                ids = list(volume_ids)
                for i in range(0, len(ids), 200):
                    chunk = ids[i:i + 200]
                    resp = ec2.describe_volumes(VolumeIds=chunk)
                    for v in resp.get("Volumes", []):
                        vol_size_map[v["VolumeId"]] = int(v.get("Size") or 0)
            except Exception as e:
                log.warning("describe_volumes failed: %s", e)

        for i, reg in raw_instances:
            result.append(_to_instance(i, reg, vol_size_map))
        return result

    def get_instance(self, instance_id: str, region: str, zone: str = "") -> Instance:
        ec2 = self._client("ec2", region)
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        i = resp["Reservations"][0]["Instances"][0]
        # 单实例查 volumes
        vol_size_map: dict[str, int] = {}
        vids = [(b.get("Ebs") or {}).get("VolumeId") for b in i.get("BlockDeviceMappings", [])]
        vids = [v for v in vids if v]
        if vids:
            try:
                resp2 = ec2.describe_volumes(VolumeIds=vids)
                for v in resp2.get("Volumes", []):
                    vol_size_map[v["VolumeId"]] = int(v.get("Size") or 0)
            except Exception as e:
                log.warning("describe_volumes failed: %s", e)
        return _to_instance(i, region, vol_size_map)

    def _resolve_image(self, image: str, region: str) -> str:
        """如果 image 是系统别名（如 ubuntu-22.04），通过 SSM 解析为该 region 的 AMI ID。"""
        if not image:
            raise ValueError("镜像不能为空")
        if image.startswith("ami-"):
            return image
        path = SSM_AMI_ALIASES.get(image)
        if not path:
            # 不是已知别名，但也不是 ami-xxx 格式 → 让 boto3 报错给用户
            return image
        ssm = self._client("ssm", region)
        resp = ssm.get_parameter(Name=path)
        return resp["Parameter"]["Value"]

    def create_instance(self, spec: CreateInstanceSpec) -> Instance:
        ec2 = self._client("ec2", spec.region)
        ami = self._resolve_image(spec.image, spec.region)
        # 查 AMI 的根设备名（不同 AMI 可能是 /dev/xvda 或 /dev/sda1）
        root_device = "/dev/xvda"
        try:
            img = ec2.describe_images(ImageIds=[ami])["Images"][0]
            root_device = img.get("RootDeviceName", root_device)
        except Exception:
            pass
        params: dict[str, Any] = {
            "ImageId": ami,
            "InstanceType": spec.instance_type,
            "MinCount": 1, "MaxCount": 1,
            "TagSpecifications": [{
                "ResourceType": "instance",
                "Tags": [{"Key": "Name", "Value": spec.name}]
                + [{"Key": k, "Value": v} for k, v in spec.tags.items()],
            }],
            "BlockDeviceMappings": [{
                "DeviceName": root_device,
                "Ebs": {
                    "VolumeSize": int(spec.disk_size_gb or 30),
                    "VolumeType": spec.disk_type or "gp3",
                    "DeleteOnTermination": True,
                },
            }],
        }
        if spec.network:
            params["SubnetId"] = spec.network
        if spec.firewall_groups:
            params["SecurityGroupIds"] = spec.firewall_groups
        if spec.user_data:
            params["UserData"] = spec.user_data
        resp = ec2.run_instances(**params)
        return _to_instance(resp["Instances"][0], spec.region)

    def list_security_groups(self, region: str) -> list[SecurityGroupBrief]:
        ec2 = self._client("ec2", region)
        out: list[SecurityGroupBrief] = []
        for sg in ec2.describe_security_groups()["SecurityGroups"]:
            out.append(SecurityGroupBrief(
                id=sg["GroupId"], name=sg.get("GroupName", ""),
                description=sg.get("Description", ""), vpc_id=sg.get("VpcId", ""),
            ))
        return out

    def list_free_tier_usage(self) -> list[FreeTierItem]:
        """AWS Free Tier 用量。仅 us-east-1 提供。免费 API 调用。"""
        client = self._client("freetier", "us-east-1")
        items: list[FreeTierItem] = []
        try:
            paginator = client.get_paginator("get_free_tier_usage")
            for page in paginator.paginate():
                for u in page.get("freeTierUsages", []):
                    actual = float(u.get("actualUsageAmount") or 0)
                    fc = float(u.get("forecastedUsageAmount") or 0)
                    limit = float(u.get("limit") or 0)
                    actual_pct = (actual / limit * 100) if limit > 0 else 0
                    fc_pct = (fc / limit * 100) if limit > 0 else 0
                    items.append(FreeTierItem(
                        service=u.get("service", ""),
                        description=u.get("description", ""),
                        actual_usage=actual,
                        forecasted_usage=fc,
                        limit=limit,
                        unit=u.get("unit", ""),
                        actual_pct=round(actual_pct, 2),
                        forecasted_pct=round(fc_pct, 2),
                    ))
        except Exception as e:
            log.warning("freetier API 调用失败: %s", e)
            raise
        return items

    def start_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._client("ec2", region).start_instances(InstanceIds=[instance_id])

    def stop_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._client("ec2", region).stop_instances(InstanceIds=[instance_id])

    def terminate_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._client("ec2", region).terminate_instances(InstanceIds=[instance_id])

    def list_firewall_rules(self, region: Optional[str] = None) -> list[FirewallRule]:
        ec2 = self._client("ec2", region)
        rules: list[FirewallRule] = []
        for sg in ec2.describe_security_groups()["SecurityGroups"]:
            for perm in sg.get("IpPermissions", []):
                rules.append(_to_firewall_rule(sg, perm, "ingress"))
            for perm in sg.get("IpPermissionsEgress", []):
                rules.append(_to_firewall_rule(sg, perm, "egress"))
        return rules

    def add_firewall_rule(self, rule: FirewallRule, region: str) -> str:
        ec2 = self._client("ec2", region)
        from_port, to_port = _parse_port_range(rule.port_range)
        perms = [{
            "IpProtocol": rule.protocol if rule.protocol != "all" else "-1",
            "FromPort": from_port, "ToPort": to_port,
            "IpRanges": [{"CidrIp": c, "Description": rule.description} for c in rule.cidrs],
        }]
        if rule.direction == "ingress":
            ec2.authorize_security_group_ingress(GroupId=rule.target, IpPermissions=perms)
        else:
            ec2.authorize_security_group_egress(GroupId=rule.target, IpPermissions=perms)
        return f"{rule.target}:{rule.direction}:{rule.protocol}:{rule.port_range}"

    def remove_firewall_rule(self, rule_id: str, region: str) -> None:
        sg, direction, proto, port_range = rule_id.split(":")
        ec2 = self._client("ec2", region)
        from_port, to_port = _parse_port_range(port_range)
        perms = [{
            "IpProtocol": proto if proto != "all" else "-1",
            "FromPort": from_port, "ToPort": to_port,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
        }]
        if direction == "ingress":
            ec2.revoke_security_group_ingress(GroupId=sg, IpPermissions=perms)
        else:
            ec2.revoke_security_group_egress(GroupId=sg, IpPermissions=perms)

    def get_traffic(self, instance_id: str, region: str, zone: str,
                    start: datetime, end: datetime) -> TrafficStat:
        # CloudWatch 按调用收费；流量统计走 SSH，这里仅满足 Protocol。
        return TrafficStat(instance_id, bytes_in=0, bytes_out=0, window_start=start, window_end=end)

    def rotate_public_ip(self, instance_id: str, region: str, zone: str = "") -> str:
        """切换公网 IP。

        EIP：先 allocate + associate(AllowReassociation) 拿到新 IP，确认后再 release 旧 EIP
        无 EIP 自动分配的：stop → start，AWS 会重新分配公网 IP
        """
        ec2 = self._client("ec2", region)
        resp = ec2.describe_instances(InstanceIds=[instance_id])
        inst = resp["Reservations"][0]["Instances"][0]
        nis = inst.get("NetworkInterfaces") or []
        eip_assoc_id = None
        eip_alloc_id = None
        for ni in nis:
            assoc = ni.get("Association") or {}
            if assoc.get("AssociationId") and assoc.get("AllocationId"):
                eip_assoc_id = assoc["AssociationId"]
                eip_alloc_id = assoc["AllocationId"]
                break

        if eip_assoc_id:
            # 先分配并关联新 EIP（AllowReassociation 直接替换同一实例上的旧关联），
            # 确认成功后再释放旧 EIP；任一步失败都回滚，绝不让实例丢失公网 IP。
            new = ec2.allocate_address(Domain="vpc")
            new_alloc_id = new["AllocationId"]
            try:
                ec2.associate_address(
                    InstanceId=instance_id,
                    AllocationId=new_alloc_id,
                    AllowReassociation=True,
                )
            except Exception:
                # 关联失败：新分配的 EIP 成了孤儿，释放掉；旧 EIP 仍在实例上，无需回滚。
                try:
                    ec2.release_address(AllocationId=new_alloc_id)
                except Exception as rel_err:
                    log.warning("rollback release new EIP failed: %s", rel_err)
                raise
            # 新 IP 已关联，旧 EIP 已被自动解绑，安全释放。
            try:
                ec2.release_address(AllocationId=eip_alloc_id)
            except Exception as rel_err:
                # 旧 EIP 释放失败不致命（实例已有新 IP），仅记录，避免泄漏一个 EIP。
                log.warning("release old EIP %s failed: %s", eip_alloc_id, rel_err)
            return new["PublicIp"]

        was_running = inst["State"]["Name"] == "running"
        if was_running:
            ec2.stop_instances(InstanceIds=[instance_id])
            ec2.get_waiter("instance_stopped").wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
            ec2.start_instances(InstanceIds=[instance_id])
            ec2.get_waiter("instance_running").wait(
                InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        resp2 = ec2.describe_instances(InstanceIds=[instance_id])
        return resp2["Reservations"][0]["Instances"][0].get("PublicIpAddress", "")

    def reboot_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._client("ec2", region).reboot_instances(InstanceIds=[instance_id])

    def wait_stopped(self, instance_id: str, region: str, zone: str = "") -> None:
        self._client("ec2", region).get_waiter("instance_stopped").wait(
            InstanceIds=[instance_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})

    def close(self) -> None:
        # boto3 客户端按调用临时创建、不持有，无需显式关闭。
        return None

    def __enter__(self) -> "AWSProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _to_instance(i: dict, region: str, vol_size_map: Optional[dict] = None) -> Instance:
    tags = {t["Key"]: t["Value"] for t in i.get("Tags", [])}
    bdms = i.get("BlockDeviceMappings", [])
    disk = 0
    for bdm in bdms:
        ebs = bdm.get("Ebs", {})
        if ebs:
            # 优先用查到的 volume size；fallback 到 BDM 自带（创建时模板里有）
            vid = ebs.get("VolumeId", "")
            if vol_size_map and vid in vol_size_map:
                disk += vol_size_map[vid]
            elif ebs.get("VolumeSize"):
                disk += int(ebs["VolumeSize"])
    sgs = [g.get("GroupId", "") for g in i.get("SecurityGroups", [])]
    return Instance(
        id=i["InstanceId"], name=tags.get("Name", ""),
        state=i["State"]["Name"], region=region,
        zone=i.get("Placement", {}).get("AvailabilityZone", ""),
        instance_type=i.get("InstanceType", ""),
        public_ip=i.get("PublicIpAddress", ""),
        private_ip=i.get("PrivateIpAddress", ""),
        tags=tags,
        image=i.get("ImageId", ""),
        arch=i.get("Architecture", ""),
        launched_at=i.get("LaunchTime"),
        disk_gb=disk,
        security_groups=sgs,
    )


def _to_firewall_rule(sg: dict, perm: dict, direction: str) -> FirewallRule:
    cidrs = [r["CidrIp"] for r in perm.get("IpRanges", [])]
    proto = perm.get("IpProtocol", "all")
    if proto == "-1":
        proto = "all"
    fp, tp = perm.get("FromPort"), perm.get("ToPort")
    if fp is None and tp is None:
        port_range = "*"
    elif fp == tp:
        port_range = str(fp)
    else:
        port_range = f"{fp}-{tp}"
    return FirewallRule(
        id=f"{sg['GroupId']}:{direction}:{proto}:{port_range}",
        direction=direction, protocol=proto, port_range=port_range, cidrs=cidrs,
        description=(perm.get("IpRanges") or [{}])[0].get("Description", ""),
        target=sg["GroupId"],
    )


def _parse_port_range(pr: str) -> tuple[int, int]:
    if pr == "*":
        return 0, 65535
    if "-" in pr:
        a, b = pr.split("-", 1)
        return int(a), int(b)
    p = int(pr)
    return p, p
