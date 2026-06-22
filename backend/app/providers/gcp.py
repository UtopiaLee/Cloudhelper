"""GCP provider 实现。

凭据 dict: 完整 ServiceAccount JSON（包含 project_id, client_email, private_key 等）
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from google.cloud import compute_v1
from google.oauth2 import service_account

from app.providers.base import (
    CreateInstanceSpec,
    FirewallRule,
    FreeTierItem,
    Instance,
    SecurityGroupBrief,
    TrafficStat,
)


class GCPProvider:
    name = "gcp"

    def __init__(self, creds: dict, default_region: str = "us-central1"):
        self._creds_dict = creds
        self._project = creds.get("project_id", "")
        self._default_region = default_region
        self._sa_creds = service_account.Credentials.from_service_account_info(creds)

    def _instances_client(self) -> compute_v1.InstancesClient:
        return compute_v1.InstancesClient(credentials=self._sa_creds)

    def _firewalls_client(self) -> compute_v1.FirewallsClient:
        return compute_v1.FirewallsClient(credentials=self._sa_creds)

    def _zones_client(self) -> compute_v1.ZonesClient:
        return compute_v1.ZonesClient(credentials=self._sa_creds)

    def list_regions(self) -> list[str]:
        client = compute_v1.RegionsClient(credentials=self._sa_creds)
        return [r.name for r in client.list(project=self._project)]

    def list_instances(self, region: str | None = None) -> list[Instance]:
        client = self._instances_client()
        agg = client.aggregated_list(project=self._project)
        result: list[Instance] = []
        for zone, scoped in agg:
            if not scoped.instances:
                continue
            zone_name = zone.replace("zones/", "")
            if region and not zone_name.startswith(region + "-"):
                continue
            for inst in scoped.instances:
                result.append(_to_instance(inst, zone_name))
        return result

    def get_instance(self, instance_id: str, region: str, zone: str = "") -> Instance:
        client = self._instances_client()
        inst = client.get(project=self._project, zone=zone, instance=instance_id)
        return _to_instance(inst, zone)

    def create_instance(self, spec: CreateInstanceSpec) -> Instance:
        client = self._instances_client()
        machine_type = f"zones/{spec.zone}/machineTypes/{spec.instance_type}"
        access_configs = [{"name": "External NAT", "type_": "ONE_TO_ONE_NAT"}] if spec.public_ip else []
        disk_type_url = (
            f"zones/{spec.zone}/diskTypes/{spec.disk_type}"
            if spec.disk_type else f"zones/{spec.zone}/diskTypes/pd-balanced"
        )
        instance = compute_v1.Instance(
            name=spec.name,
            machine_type=machine_type,
            disks=[compute_v1.AttachedDisk(
                boot=True,
                auto_delete=True,
                initialize_params=compute_v1.AttachedDiskInitializeParams(
                    source_image=spec.image,
                    disk_size_gb=int(spec.disk_size_gb or 30),
                    disk_type=disk_type_url,
                ),
            )],
            network_interfaces=[compute_v1.NetworkInterface(
                network=spec.network or "global/networks/default",
                access_configs=access_configs,
            )],
            tags=compute_v1.Tags(items=spec.firewall_groups),
            labels=spec.tags,
        )
        op = client.insert(project=self._project, zone=spec.zone, instance_resource=instance)
        op.result(timeout=120)
        return self.get_instance(spec.name, spec.region, spec.zone)

    def list_security_groups(self, region: str) -> list[SecurityGroupBrief]:
        # GCP 没有 SG 概念，用 firewall + tag 实现。这里返回所有 firewall 当作 "可关联标签"
        client = self._firewalls_client()
        out: list[SecurityGroupBrief] = []
        for fw in client.list(project=self._project):
            tags = list(fw.target_tags) if fw.target_tags else []
            for t in tags or [fw.name]:
                out.append(SecurityGroupBrief(
                    id=t, name=t,
                    description=f"firewall: {fw.name} ({fw.direction})",
                    vpc_id=fw.network.rsplit('/', 1)[-1] if fw.network else "",
                ))
        seen = set()
        uniq = []
        for s in out:
            if s.id in seen:
                continue
            seen.add(s.id)
            uniq.append(s)
        return uniq

    def list_free_tier_usage(self) -> list[FreeTierItem]:
        """GCP 没有 Free Tier API；用 BigQuery 账单导出查"已花费"代替。

        探测策略：
          1. 在 SA 所在 project 里找 dataset 名称含 "billing" 的
          2. 在该 dataset 里找表名以 "gcp_billing_export_v1_" 开头的
          3. 查当月每个 service 的总开销
          4. 用 FreeTierItem 复用前端 UI（service / actual_usage(USD) / limit=0 / unit=USD）
        """
        from google.cloud import bigquery
        from google.api_core import exceptions as gax_exc

        bq = bigquery.Client(project=self._project, credentials=self._sa_creds)

        # 1. 找 billing 相关 dataset
        billing_table: Optional[str] = None
        try:
            for ds in bq.list_datasets():
                if "billing" not in ds.dataset_id.lower():
                    continue
                # 2. 找表
                for tbl in bq.list_tables(ds.reference):
                    if tbl.table_id.startswith("gcp_billing_export_v1_") or tbl.table_id.startswith("gcp_billing_export_resource_v1_"):
                        billing_table = f"{ds.project}.{ds.dataset_id}.{tbl.table_id}"
                        break
                if billing_table:
                    break
        except gax_exc.PermissionDenied as e:
            raise RuntimeError(f"权限不足，SA 需要 BigQuery Data Viewer 权限：{e}")
        except Exception as e:
            raise RuntimeError(f"探测 BQ 账单表失败：{e}")

        if not billing_table:
            raise RuntimeError(
                f"在项目 {self._project} 没找到账单导出表。"
                f"请去 GCP Console → Billing → Billing export → BigQuery export 启用。"
            )

        # 3. 查当月按服务聚合
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        sql = f"""
            SELECT
              service.description AS service,
              SUM(cost) AS cost_usd,
              SUM(IFNULL((SELECT SUM(cd.amount) FROM UNNEST(credits) cd), 0)) AS credit_usd
            FROM `{billing_table}`
            WHERE
              EXTRACT(YEAR FROM usage_start_time) = {now.year}
              AND EXTRACT(MONTH FROM usage_start_time) = {now.month}
            GROUP BY service
            ORDER BY cost_usd DESC
        """
        try:
            rows = list(bq.query(sql).result())
        except gax_exc.NotFound as e:
            raise RuntimeError(f"账单表 {billing_table} 不存在：{e}")
        except Exception as e:
            raise RuntimeError(f"查询账单失败：{e}")

        items: list[FreeTierItem] = []
        for r in rows:
            cost = float(r.get("cost_usd") or 0)
            credit = float(r.get("credit_usd") or 0)  # 通常是负数（抵扣）
            net = cost + credit  # 实际付费 = 列表价 - 抵扣
            if abs(cost) < 0.001 and abs(credit) < 0.001:
                continue
            items.append(FreeTierItem(
                service=r.get("service") or "unknown",
                description=f"{r.get('service') or 'unknown'} (净 ${net:.4f}, 抵扣 ${-credit:.4f})",
                actual_usage=round(cost, 4),
                forecasted_usage=round(cost / max(now.day, 1) * 30, 4),  # 简单按天平均推月底
                limit=0.0,  # GCP 没法拿 free tier limit，用 0 表示
                unit="USD",
                actual_pct=0.0,
                forecasted_pct=0.0,
            ))
        return items

    def start_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._instances_client().start(project=self._project, zone=zone, instance=instance_id).result(timeout=60)

    def stop_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._instances_client().stop(project=self._project, zone=zone, instance=instance_id).result(timeout=60)

    def terminate_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        self._instances_client().delete(project=self._project, zone=zone, instance=instance_id).result(timeout=120)

    def list_firewall_rules(self, region: str | None = None) -> list[FirewallRule]:
        client = self._firewalls_client()
        rules: list[FirewallRule] = []
        for fw in client.list(project=self._project):
            direction = "ingress" if fw.direction == "INGRESS" else "egress"
            for allowed in fw.allowed:
                proto = allowed.I_p_protocol if hasattr(allowed, "I_p_protocol") else getattr(allowed, "ip_protocol", "all")
                ports = list(allowed.ports) if allowed.ports else ["*"]
                for p in ports:
                    rules.append(FirewallRule(
                        id=f"{fw.name}:{direction}:{proto}:{p}",
                        direction=direction,
                        protocol=proto,
                        port_range=p,
                        cidrs=list(fw.source_ranges) if direction == "ingress" else list(fw.destination_ranges),
                        description=fw.description or "",
                        target=fw.name,
                    ))
        return rules

    def add_firewall_rule(self, rule: FirewallRule, region: str) -> str:
        client = self._firewalls_client()
        allowed = compute_v1.Allowed()
        allowed.I_p_protocol = rule.protocol if rule.protocol != "all" else "all"
        if rule.port_range and rule.port_range != "*":
            allowed.ports = [rule.port_range]
        fw = compute_v1.Firewall(
            name=rule.target,
            direction=rule.direction.upper(),
            allowed=[allowed],
            source_ranges=rule.cidrs if rule.direction == "ingress" else [],
            destination_ranges=rule.cidrs if rule.direction == "egress" else [],
            description=rule.description,
        )
        client.insert(project=self._project, firewall_resource=fw).result(timeout=60)
        return f"{rule.target}:{rule.direction}:{rule.protocol}:{rule.port_range}"

    def remove_firewall_rule(self, rule_id: str, region: str) -> None:
        name = rule_id.split(":")[0]
        self._firewalls_client().delete(project=self._project, firewall=name).result(timeout=60)

    def get_traffic(
        self, instance_id: str, region: str, zone: str, start: datetime, end: datetime
    ) -> TrafficStat:
        # Monitoring API 按调用计费；流量改走 SSH。
        return TrafficStat(instance_id, bytes_in=0, bytes_out=0, window_start=start, window_end=end)

    def rotate_public_ip(self, instance_id: str, region: str, zone: str = "") -> str:
        """删除当前 access_config 并新加一个临时 IP。"""
        client = self._instances_client()
        inst = client.get(project=self._project, zone=zone, instance=instance_id)
        if not inst.network_interfaces:
            raise ValueError("实例没有 network interface")
        nic = inst.network_interfaces[0]
        nic_name = nic.name
        if nic.access_configs:
            ac_name = nic.access_configs[0].name or "External NAT"
            client.delete_access_config(
                project=self._project, zone=zone, instance=instance_id,
                network_interface=nic_name, access_config=ac_name,
            ).result(timeout=60)
        client.add_access_config(
            project=self._project, zone=zone, instance=instance_id,
            network_interface=nic_name,
            access_config_resource=compute_v1.AccessConfig(name="External NAT", type_="ONE_TO_ONE_NAT"),
        ).result(timeout=60)
        inst2 = client.get(project=self._project, zone=zone, instance=instance_id)
        if inst2.network_interfaces and inst2.network_interfaces[0].access_configs:
            return inst2.network_interfaces[0].access_configs[0].nat_i_p
        return ""


def _to_instance(inst: Any, zone: str) -> Instance:
    public_ip = ""
    private_ip = ""
    if inst.network_interfaces:
        nic = inst.network_interfaces[0]
        private_ip = nic.network_i_p
        if nic.access_configs:
            public_ip = nic.access_configs[0].nat_i_p
    region = "-".join(zone.split("-")[:-1])

    image = ""
    disk = 0
    if inst.disks:
        boot = next((d for d in inst.disks if d.boot), inst.disks[0])
        disk = int(getattr(boot, "disk_size_gb", 0) or 0)
        # initialize_params 在 list 响应里通常没有；image 字段需要单独 get_disk
        # 简化：用 source 名称当 image 标识
        image = (boot.source or "").rsplit("/", 1)[-1] if boot.source else ""

    sgs = list(inst.tags.items) if inst.tags else []
    arch = ""
    machine_type = inst.machine_type.rsplit("/", 1)[-1] if inst.machine_type else ""
    if machine_type.startswith("t2a-") or machine_type.startswith("c4a-"):
        arch = "arm64"
    else:
        arch = "x86_64"

    launched_at = None
    if getattr(inst, "creation_timestamp", ""):
        try:
            from datetime import datetime as _dt
            launched_at = _dt.fromisoformat(inst.creation_timestamp.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            launched_at = None

    return Instance(
        id=inst.name, name=inst.name,
        state=_map_state(inst.status), region=region, zone=zone,
        instance_type=machine_type,
        public_ip=public_ip, private_ip=private_ip,
        tags=dict(inst.labels) if inst.labels else {},
        image=image, arch=arch,
        disk_gb=disk, launched_at=launched_at,
        security_groups=sgs,
    )


_GCP_STATE_MAP = {
    "RUNNING": "running",
    "TERMINATED": "stopped",
    "STOPPING": "stopping",
    "PROVISIONING": "pending",
    "STAGING": "pending",
    "REPAIRING": "pending",
    "SUSPENDED": "stopped",
}


def _map_state(s: str) -> str:
    return _GCP_STATE_MAP.get(s, "unknown")
