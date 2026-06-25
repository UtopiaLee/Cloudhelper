from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import oci

from app.providers.base import (
    CreateInstanceSpec,
    FirewallRule,
    Instance,
    SecurityGroupBrief,
    TrafficStat,
)

ORACLE_IMAGE_ALIASES = {
    "oracle-8": "Oracle-Linux-8",
    "oracle-9": "Oracle-Linux-9",
    "ubuntu-22.04": "Canonical-Ubuntu-22.04",
    "ubuntu-24.04": "Canonical-Ubuntu-24.04",
}


class OracleProvider:
    name = "oracle"

    def __init__(self, creds: dict, default_region: str = "ap-singapore-1"):
        self._creds = creds
        self._region = (default_region or creds.get("region") or "ap-singapore-1").strip()
        self._tenancy = creds.get("tenancy", "")
        self._default_compartment = creds.get("compartment_id", "") or self._tenancy
        cfg = {
            "user": creds.get("user", ""),
            "fingerprint": creds.get("fingerprint", ""),
            "tenancy": self._tenancy,
            "region": self._region,
            "key_content": creds.get("key_pem", ""),
            "pass_phrase": creds.get("passphrase") or None,
        }
        self._config = cfg
        self._compute = oci.core.ComputeClient(cfg)
        self._network = oci.core.VirtualNetworkClient(cfg)
        self._identity = oci.identity.IdentityClient(cfg)

    def list_regions(self) -> list[str]:
        regions = oci.pagination.list_call_get_all_results(
            self._identity.list_region_subscriptions,
            tenancy_id=self._tenancy,
        ).data
        return [r.region_name for r in regions]

    def list_instances(self, region: Optional[str] = None) -> list[Instance]:
        region = region or self._region
        if region != self._region:
            self._switch_region(region)
        out: list[Instance] = []
        for cid in self._list_compartments():
            rows = oci.pagination.list_call_get_all_results(
                self._compute.list_instances,
                compartment_id=cid,
            ).data
            for row in rows:
                state = (row.lifecycle_state or "").lower()
                if state in ("terminated", "terminating"):
                    continue
                out.append(self._to_instance(row, cid))
        return out

    def get_instance(self, instance_id: str, region: str, zone: str = "") -> Instance:
        if region != self._region:
            self._switch_region(region)
        inst = self._compute.get_instance(instance_id).data
        return self._to_instance(inst, inst.compartment_id)

    def create_instance(self, spec: CreateInstanceSpec) -> Instance:
        if spec.region and spec.region != self._region:
            self._switch_region(spec.region)

        compartment_id = self._default_compartment
        subnet_id = self._resolve_subnet_id(compartment_id, spec.network)
        if not subnet_id:
            raise ValueError("Oracle 未找到可用子网，请在创建时指定 network=subnet_ocid")

        ad = spec.zone or self._first_availability_domain(compartment_id)
        if not ad:
            raise ValueError("Oracle 未找到可用可用区（Availability Domain）")

        nsg_ids = spec.firewall_groups or []
        create_vnic = oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=bool(spec.public_ip),
            nsg_ids=nsg_ids if nsg_ids else None,
        )

        image_id = self._resolve_image_id(compartment_id, ad, spec.image)
        source = oci.core.models.InstanceSourceViaImageDetails(
            source_type="image",
            image_id=image_id,
            boot_volume_size_in_gbs=int(spec.disk_size_gb or 50),
        )

        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=compartment_id,
            availability_domain=ad,
            shape=spec.instance_type,
            display_name=spec.name,
            create_vnic_details=create_vnic,
            source_details=source,
            freeform_tags=spec.tags or {},
            metadata={"user_data": spec.user_data} if spec.user_data else None,
        )

        if spec.instance_type == "VM.Standard.A1.Flex":
            details.shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=1.0,
                memory_in_gbs=6.0,
            )

        created = self._compute.launch_instance(details).data
        fresh = self._compute.get_instance(created.id).data
        return self._to_instance(fresh, fresh.compartment_id)

    def start_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        if region != self._region:
            self._switch_region(region)
        self._compute.instance_action(instance_id=instance_id, action="START")

    def stop_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        if region != self._region:
            self._switch_region(region)
        self._compute.instance_action(instance_id=instance_id, action="SOFTSTOP")

    def reboot_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        if region != self._region:
            self._switch_region(region)
        self._compute.instance_action(instance_id=instance_id, action="SOFTRESET")

    def terminate_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        if region != self._region:
            self._switch_region(region)
        self._compute.terminate_instance(instance_id=instance_id)

    def list_firewall_rules(self, region: Optional[str] = None) -> list[FirewallRule]:
        if region and region != self._region:
            self._switch_region(region)
        rules: list[FirewallRule] = []
        for cid in self._list_compartments():
            nsgs = oci.pagination.list_call_get_all_results(
                self._network.list_network_security_groups,
                compartment_id=cid,
            ).data
            for nsg in nsgs:
                data = self._network.list_network_security_group_security_rules(
                    network_security_group_id=nsg.id
                ).data
                for r in data:
                    direction = "ingress" if r.direction == "INGRESS" else "egress"
                    proto = _oci_proto_to_app(r.protocol)
                    cidrs = []
                    if direction == "ingress":
                        cidrs = [r.source] if getattr(r, "source", None) else []
                    else:
                        cidrs = [r.destination] if getattr(r, "destination", None) else []
                    port_range = _oci_rule_port_range(r, proto)
                    rules.append(FirewallRule(
                        id=f"{nsg.id}:{r.id}",
                        direction=direction,
                        protocol=proto,
                        port_range=port_range,
                        cidrs=cidrs,
                        description=r.description or "",
                        target=nsg.id,
                    ))
        return rules

    def add_firewall_rule(self, rule: FirewallRule, region: str) -> str:
        if region != self._region:
            self._switch_region(region)

        kwargs: dict[str, Any] = {
            "description": rule.description or "",
            "direction": "INGRESS" if rule.direction == "ingress" else "EGRESS",
            "is_stateless": False,
            "protocol": _app_proto_to_oci(rule.protocol),
        }
        if rule.direction == "ingress":
            kwargs["source"] = (rule.cidrs or ["0.0.0.0/0"])[0]
            kwargs["source_type"] = "CIDR_BLOCK"
        else:
            kwargs["destination"] = (rule.cidrs or ["0.0.0.0/0"])[0]
            kwargs["destination_type"] = "CIDR_BLOCK"

        pmin, pmax = _parse_port_range(rule.port_range)
        if rule.protocol == "tcp":
            kwargs["tcp_options"] = oci.core.models.TcpOptions(
                destination_port_range=oci.core.models.PortRange(min=pmin, max=pmax)
            )
        elif rule.protocol == "udp":
            kwargs["udp_options"] = oci.core.models.UdpOptions(
                destination_port_range=oci.core.models.PortRange(min=pmin, max=pmax)
            )

        sec_rule = oci.core.models.AddSecurityRuleDetails(**kwargs)
        self._network.add_network_security_group_security_rules(
            network_security_group_id=rule.target,
            add_network_security_group_security_rules_details=
            oci.core.models.AddNetworkSecurityGroupSecurityRulesDetails(
                security_rules=[sec_rule]
            ),
        )

        refreshed = self._network.list_network_security_group_security_rules(
            network_security_group_id=rule.target
        ).data
        matched = next((r for r in refreshed if (r.description or "") == (rule.description or "")), None)
        rule_id = matched.id if matched else "new"
        return f"{rule.target}:{rule_id}"

    def remove_firewall_rule(self, rule_id: str, region: str) -> None:
        if region != self._region:
            self._switch_region(region)
        nsg_id, sec_rule_id = rule_id.split(":", 1)
        self._network.remove_network_security_group_security_rules(
            network_security_group_id=nsg_id,
            remove_network_security_group_security_rules_details=
            oci.core.models.RemoveNetworkSecurityGroupSecurityRulesDetails(
                security_rule_ids=[sec_rule_id]
            ),
        )

    def list_security_groups(self, region: str) -> list[SecurityGroupBrief]:
        if region != self._region:
            self._switch_region(region)
        out: list[SecurityGroupBrief] = []
        for cid in self._list_compartments():
            nsgs = oci.pagination.list_call_get_all_results(
                self._network.list_network_security_groups,
                compartment_id=cid,
            ).data
            for n in nsgs:
                out.append(SecurityGroupBrief(
                    id=n.id,
                    name=n.display_name or n.id,
                    description=n.lifecycle_state or "",
                    vpc_id=n.vcn_id or "",
                ))
        return out

    def get_traffic(self, instance_id: str, region: str, zone: str, start: datetime, end: datetime) -> TrafficStat:
        return TrafficStat(instance_id=instance_id, bytes_in=0, bytes_out=0, window_start=start, window_end=end)

    def rotate_public_ip(self, instance_id: str, region: str, zone: str = "") -> str:
        if region != self._region:
            self._switch_region(region)
        inst = self._compute.get_instance(instance_id).data
        vnic = self._primary_vnic(instance_id, inst.compartment_id)
        private_ip_id = vnic.get("private_ip_id", "")
        if not private_ip_id:
            raise ValueError("Oracle 实例未找到 primary private IP")

        existing = self._network.get_public_ip_by_private_ip_id(private_ip_id=private_ip_id).data
        if existing and existing.id:
            self._network.delete_public_ip(public_ip_id=existing.id)

        created = self._network.create_public_ip(
            create_public_ip_details=oci.core.models.CreatePublicIpDetails(
                compartment_id=inst.compartment_id,
                lifetime="EPHEMERAL",
                private_ip_id=private_ip_id,
            )
        ).data
        return created.ip_address or ""

    def _switch_region(self, region: str) -> None:
        self._region = region
        self._config["region"] = region
        self._compute = oci.core.ComputeClient(self._config)
        self._network = oci.core.VirtualNetworkClient(self._config)

    def _list_compartments(self) -> list[str]:
        out = {self._tenancy}
        items = oci.pagination.list_call_get_all_results(
            self._identity.list_compartments,
            compartment_id=self._tenancy,
            compartment_id_in_subtree=True,
            access_level="ACCESSIBLE",
        ).data
        for c in items:
            if (c.lifecycle_state or "") == "ACTIVE":
                out.add(c.id)
        return list(out)

    def _first_availability_domain(self, compartment_id: str) -> str:
        ads = oci.pagination.list_call_get_all_results(
            self._identity.list_availability_domains,
            compartment_id=compartment_id,
        ).data
        return ads[0].name if ads else ""

    def _resolve_subnet_id(self, compartment_id: str, network: str) -> str:
        if network:
            return network

        vcns = oci.pagination.list_call_get_all_results(
            self._network.list_vcns,
            compartment_id=compartment_id,
        ).data
        active_vcns = [v for v in vcns if (v.lifecycle_state or "") == "AVAILABLE"]
        if not active_vcns:
            return ""

        default_vcn = next((v for v in active_vcns if "default" in (v.display_name or "").lower()), active_vcns[0])
        subnets = oci.pagination.list_call_get_all_results(
            self._network.list_subnets,
            compartment_id=compartment_id,
            vcn_id=default_vcn.id,
        ).data
        active_subnets = [s for s in subnets if (s.lifecycle_state or "") == "AVAILABLE"]
        return active_subnets[0].id if active_subnets else ""

    def _resolve_image_id(self, compartment_id: str, availability_domain: str, image: str) -> str:
        if image.startswith("ocid1.image"):
            return image
        target = ORACLE_IMAGE_ALIASES.get(image, image).lower()
        images = oci.pagination.list_call_get_all_results(
            self._compute.list_images,
            compartment_id=compartment_id,
            operating_system=None,
            sort_by="TIMECREATED",
            sort_order="DESC",
        ).data
        for img in images:
            if (img.lifecycle_state or "") != "AVAILABLE":
                continue
            name = (img.display_name or "").lower()
            if target in name:
                return img.id
        raise ValueError(f"Oracle 未找到镜像: {image}（可用别名：{', '.join(ORACLE_IMAGE_ALIASES.keys())}）")

    def _to_instance(self, row: Any, compartment_id: str) -> Instance:
        vnic = self._primary_vnic(row.id, compartment_id)
        state = (row.lifecycle_state or "").lower()
        launched = None
        if getattr(row, "time_created", None):
            launched = row.time_created
            if launched.tzinfo is None:
                launched = launched.replace(tzinfo=timezone.utc)

        tags = dict(getattr(row, "freeform_tags", {}) or {})
        defined = getattr(row, "defined_tags", {}) or {}
        for ns, kv in defined.items():
            if isinstance(kv, dict):
                for k, v in kv.items():
                    tags[f"{ns}.{k}"] = str(v)

        return Instance(
            id=row.id,
            name=row.display_name or "",
            state=_map_state(state),
            region=self._region,
            zone=getattr(row, "availability_domain", "") or "",
            instance_type=getattr(row, "shape", "") or "",
            public_ip=vnic.get("public_ip", ""),
            private_ip=vnic.get("private_ip", ""),
            tags=tags,
            image=getattr(row, "image_id", "") or "",
            arch="arm64" if "A1" in (getattr(row, "shape", "") or "") else "x86_64",
            disk_gb=0,
            launched_at=launched,
            security_groups=vnic.get("nsg_ids", []),
        )

    def _primary_vnic(self, instance_id: str, compartment_id: str) -> dict[str, Any]:
        atts = oci.pagination.list_call_get_all_results(
            self._compute.list_vnic_attachments,
            compartment_id=compartment_id,
            instance_id=instance_id,
        ).data
        if not atts:
            return {"public_ip": "", "private_ip": "", "private_ip_id": "", "nsg_ids": []}
        primary = next((a for a in atts if getattr(a, "is_primary", False)), atts[0])
        vnic = self._network.get_vnic(primary.vnic_id).data
        return {
            "public_ip": getattr(vnic, "public_ip", "") or "",
            "private_ip": getattr(vnic, "private_ip", "") or "",
            "private_ip_id": getattr(vnic, "private_ip_id", "") or "",
            "nsg_ids": list(getattr(vnic, "nsg_ids", []) or []),
        }

    def close(self) -> None:
        # OCI 客户端各自持有一个 requests.Session（长连接池），逐个关闭释放 socket。
        for client in (self._compute, self._network, self._identity):
            session = getattr(getattr(client, "base_client", None), "session", None)
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass

    def __enter__(self) -> "OracleProvider":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _map_state(state: str) -> str:
    s = (state or "").lower()
    if s in {"running", "starting"}:
        return "running"
    if s in {"stopped", "stopping"}:
        return "stopped"
    if s in {"provisioning", "creating", "terminating"}:
        return "pending"
    if s in {"terminated"}:
        return "terminated"
    return s or "unknown"


def _app_proto_to_oci(proto: str) -> str:
    if proto == "tcp":
        return "6"
    if proto == "udp":
        return "17"
    if proto == "icmp":
        return "1"
    return "all"


def _oci_proto_to_app(proto: str) -> str:
    if proto == "6":
        return "tcp"
    if proto == "17":
        return "udp"
    if proto == "1":
        return "icmp"
    return "all"


def _parse_port_range(port_range: str) -> tuple[int, int]:
    if port_range == "*":
        return 0, 65535
    if "-" in port_range:
        a, b = port_range.split("-", 1)
        return int(a), int(b)
    p = int(port_range)
    return p, p


def _oci_rule_port_range(rule: Any, proto: str) -> str:
    if proto == "tcp" and getattr(rule, "tcp_options", None) and rule.tcp_options.destination_port_range:
        r = rule.tcp_options.destination_port_range
        return str(r.min) if r.min == r.max else f"{r.min}-{r.max}"
    if proto == "udp" and getattr(rule, "udp_options", None) and rule.udp_options.destination_port_range:
        r = rule.udp_options.destination_port_range
        return str(r.min) if r.min == r.max else f"{r.min}-{r.max}"
    return "*"
