from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from azure.identity import ClientSecretCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient
from azure.mgmt.subscription import SubscriptionClient

from app.providers.base import (
    CreateInstanceSpec,
    FirewallRule,
    Instance,
    SecurityGroupBrief,
    TrafficStat,
)


AZURE_IMAGE_ALIASES = {
    "ubuntu-22.04": ("Canonical", "0001-com-ubuntu-server-jammy", "22_04-lts-gen2"),
    "ubuntu-24.04": ("Canonical", "ubuntu-24_04-lts", "server"),
    "debian-12": ("Debian", "debian-12", "12"),
    "rocky-9": ("erockyenterprisesoftwarefoundationinc1653071250513", "rockylinux-9", "rockylinux-9"),
}


class AzureProvider:
    name = "azure"

    def __init__(self, creds: dict, default_region: str = "eastus"):
        self._creds = creds
        self._region = (default_region or creds.get("region") or "eastus").strip()
        self._subscription_id = creds.get("subscription_id", "")
        self._resource_group = creds.get("resource_group", "") or "CloudHelper"
        self._credential = ClientSecretCredential(
            tenant_id=creds.get("tenant_id", ""),
            client_id=creds.get("client_id", ""),
            client_secret=creds.get("client_secret", ""),
        )
        self._compute = ComputeManagementClient(self._credential, self._subscription_id)
        self._network = NetworkManagementClient(self._credential, self._subscription_id)
        self._resource = ResourceManagementClient(self._credential, self._subscription_id)
        self._subscription = SubscriptionClient(self._credential)

    def list_regions(self) -> list[str]:
        items = self._subscription.subscriptions.list_locations(subscription_id=self._subscription_id)
        return [loc.name for loc in items]

    def list_instances(self, region: Optional[str] = None) -> list[Instance]:
        target = (region or "").strip()
        out: list[Instance] = []
        for vm in self._compute.virtual_machines.list_all():
            rg = _resource_group_from_id(vm.id)
            loc = (vm.location or "").lower()
            if target and loc != target.lower():
                continue
            inst = self._compute.virtual_machines.get(
                resource_group_name=rg, vm_name=vm.name, expand="instanceView"
            )
            out.append(self._to_instance(inst, rg))
        return out

    def get_instance(self, instance_id: str, region: str, zone: str = "") -> Instance:
        rg, name = _split_vm_id(instance_id)
        inst = self._compute.virtual_machines.get(
            resource_group_name=rg, vm_name=name, expand="instanceView"
        )
        return self._to_instance(inst, rg)

    def create_instance(self, spec: CreateInstanceSpec) -> Instance:
        rg = self._resource_group
        self._ensure_resource_group(rg, spec.region or self._region)

        publisher, offer, sku = _resolve_image(spec.image)
        admin_username = "azureuser"
        admin_password = (spec.tags or {}).get("admin_password", "")
        ssh_public_key = (spec.tags or {}).get("ssh_public_key", "")

        nic_id = self._ensure_nic(rg, spec, ssh_open=True)

        os_profile: dict[str, Any] = {
            "computer_name": spec.name,
            "admin_username": admin_username,
        }
        if ssh_public_key:
            os_profile["linux_configuration"] = {
                "disable_password_authentication": True,
                "ssh": {
                    "public_keys": [{
                        "path": f"/home/{admin_username}/.ssh/authorized_keys",
                        "key_data": ssh_public_key,
                    }],
                },
            }
        elif admin_password:
            os_profile["admin_password"] = admin_password
            os_profile["linux_configuration"] = {"disable_password_authentication": False}
        else:
            raise ValueError("Azure 创建实例需要 ssh_public_key 或 admin_password（通过 tags 透传）")

        if spec.user_data:
            os_profile["custom_data"] = spec.user_data

        parameters: dict[str, Any] = {
            "location": spec.region or self._region,
            "hardware_profile": {"vm_size": spec.instance_type},
            "storage_profile": {
                "image_reference": {
                    "publisher": publisher, "offer": offer,
                    "sku": sku, "version": "latest",
                },
                "os_disk": {
                    "create_option": "FromImage",
                    "disk_size_gb": int(spec.disk_size_gb or 30),
                    "managed_disk": {"storage_account_type": spec.disk_type or "Standard_LRS"},
                },
            },
            "os_profile": os_profile,
            "network_profile": {"network_interfaces": [{"id": nic_id, "primary": True}]},
            "tags": spec.tags or {},
        }

        poller = self._compute.virtual_machines.begin_create_or_update(
            resource_group_name=rg, vm_name=spec.name, parameters=parameters
        )
        created = poller.result()
        return self._to_instance(created, rg)

    def start_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        rg, name = _split_vm_id(instance_id)
        self._compute.virtual_machines.begin_start(resource_group_name=rg, vm_name=name).result()

    def stop_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        rg, name = _split_vm_id(instance_id)
        self._compute.virtual_machines.begin_deallocate(resource_group_name=rg, vm_name=name).result()

    def terminate_instance(self, instance_id: str, region: str, zone: str = "") -> None:
        rg, name = _split_vm_id(instance_id)
        self._compute.virtual_machines.begin_delete(resource_group_name=rg, vm_name=name).result()

    def list_firewall_rules(self, region: Optional[str] = None) -> list[FirewallRule]:
        rules: list[FirewallRule] = []
        for nsg in self._network.network_security_groups.list_all():
            rg = _resource_group_from_id(nsg.id)
            for rule in nsg.security_rules or []:
                direction = "ingress" if (rule.direction or "").lower() == "inbound" else "egress"
                proto = (rule.protocol or "").lower()
                if proto == "*":
                    proto = "all"
                if proto not in ("tcp", "udp", "icmp", "all"):
                    proto = "all"
                cidrs: list[str] = []
                if direction == "ingress":
                    cidrs = _flat_cidrs(rule.source_address_prefix, rule.source_address_prefixes)
                else:
                    cidrs = _flat_cidrs(rule.destination_address_prefix, rule.destination_address_prefixes)
                port_range = (rule.destination_port_range or "").replace("*", "*")
                if rule.destination_port_ranges:
                    port_range = ",".join(rule.destination_port_ranges)
                rules.append(FirewallRule(
                    id=f"{rg}:{nsg.name}:{rule.name}",
                    direction=direction,
                    protocol=proto,
                    port_range=port_range or "*",
                    cidrs=cidrs,
                    description=rule.description or "",
                    target=f"{rg}:{nsg.name}",
                ))
        return rules

    def add_firewall_rule(self, rule: FirewallRule, region: str) -> str:
        rg, nsg_name = _split_nsg_target(rule.target)
        rule_name = (rule.description or f"ch-{rule.direction}-{rule.protocol}-{rule.port_range}")[:80]

        existing = list(self._network.security_rules.list(resource_group_name=rg, network_security_group_name=nsg_name))
        priority = 200 + len([r for r in existing if (r.direction or "").lower() == ("inbound" if rule.direction == "ingress" else "outbound")])

        params: dict[str, Any] = {
            "protocol": _proto_to_azure(rule.protocol),
            "access": "Allow",
            "direction": "Inbound" if rule.direction == "ingress" else "Outbound",
            "priority": priority,
            "destination_port_range": rule.port_range if rule.port_range != "*" else "*",
            "source_port_range": "*",
        }
        if rule.direction == "ingress":
            params["source_address_prefix"] = (rule.cidrs or ["0.0.0.0/0"])[0]
            params["destination_address_prefix"] = "*"
        else:
            params["source_address_prefix"] = "*"
            params["destination_address_prefix"] = (rule.cidrs or ["0.0.0.0/0"])[0]

        self._network.security_rules.begin_create_or_update(
            resource_group_name=rg,
            network_security_group_name=nsg_name,
            security_rule_name=rule_name,
            security_rule_parameters=params,
        ).result()
        return f"{rg}:{nsg_name}:{rule_name}"

    def remove_firewall_rule(self, rule_id: str, region: str) -> None:
        rg, nsg_name, rule_name = rule_id.split(":", 2)
        self._network.security_rules.begin_delete(
            resource_group_name=rg,
            network_security_group_name=nsg_name,
            security_rule_name=rule_name,
        ).result()

    def list_security_groups(self, region: str) -> list[SecurityGroupBrief]:
        out: list[SecurityGroupBrief] = []
        target = (region or "").lower()
        for nsg in self._network.network_security_groups.list_all():
            if target and (nsg.location or "").lower() != target:
                continue
            rg = _resource_group_from_id(nsg.id)
            out.append(SecurityGroupBrief(
                id=f"{rg}:{nsg.name}",
                name=nsg.name,
                description=nsg.location or "",
                vpc_id="",
            ))
        return out

    def get_traffic(self, instance_id: str, region: str, zone: str, start: datetime, end: datetime) -> TrafficStat:
        return TrafficStat(instance_id=instance_id, bytes_in=0, bytes_out=0, window_start=start, window_end=end)

    def rotate_public_ip(self, instance_id: str, region: str, zone: str = "") -> str:
        rg, name = _split_vm_id(instance_id)
        vm = self._compute.virtual_machines.get(resource_group_name=rg, vm_name=name)
        if not vm.network_profile or not vm.network_profile.network_interfaces:
            raise ValueError("Azure 实例未关联 NIC")
        nic_id = vm.network_profile.network_interfaces[0].id
        nic_rg, nic_name = _split_nic_id(nic_id)
        nic = self._network.network_interfaces.get(resource_group_name=nic_rg, network_interface_name=nic_name)
        if not nic.ip_configurations:
            raise ValueError("Azure NIC 未配置 IP")

        ip_cfg = nic.ip_configurations[0]
        old_pip_id = ip_cfg.public_ip_address.id if ip_cfg.public_ip_address else ""

        new_pip_name = f"{name}-pip-{int(datetime.utcnow().timestamp())}"
        new_pip = self._network.public_ip_addresses.begin_create_or_update(
            resource_group_name=nic_rg,
            public_ip_address_name=new_pip_name,
            parameters={"location": nic.location, "sku": {"name": "Basic"}, "public_ip_allocation_method": "Dynamic"},
        ).result()

        ip_cfg.public_ip_address = new_pip
        self._network.network_interfaces.begin_create_or_update(
            resource_group_name=nic_rg, network_interface_name=nic_name, parameters=nic
        ).result()

        if old_pip_id:
            old_rg, old_name = _split_pip_id(old_pip_id)
            try:
                self._network.public_ip_addresses.begin_delete(resource_group_name=old_rg, public_ip_address_name=old_name).result()
            except Exception:
                pass

        refreshed = self._network.public_ip_addresses.get(resource_group_name=nic_rg, public_ip_address_name=new_pip_name)
        return refreshed.ip_address or ""

    def _ensure_resource_group(self, rg: str, location: str) -> None:
        if not self._resource.resource_groups.check_existence(resource_group_name=rg):
            self._resource.resource_groups.create_or_update(
                resource_group_name=rg,
                parameters={"location": location},
            )

    def _ensure_nic(self, rg: str, spec: CreateInstanceSpec, ssh_open: bool) -> str:
        vnet_name = f"{spec.name}-vnet"
        subnet_name = "default"
        nic_name = f"{spec.name}-nic"
        pip_name = f"{spec.name}-pip"
        nsg_name = (spec.firewall_groups or [""])[0] or f"{spec.name}-nsg"

        self._network.virtual_networks.begin_create_or_update(
            resource_group_name=rg, virtual_network_name=vnet_name,
            parameters={
                "location": spec.region or self._region,
                "address_space": {"address_prefixes": ["10.0.0.0/16"]},
                "subnets": [{"name": subnet_name, "address_prefix": "10.0.0.0/24"}],
            },
        ).result()
        subnet = self._network.subnets.get(resource_group_name=rg, virtual_network_name=vnet_name, subnet_name=subnet_name)

        self._network.network_security_groups.begin_create_or_update(
            resource_group_name=rg, network_security_group_name=nsg_name,
            parameters={"location": spec.region or self._region},
        ).result()

        if ssh_open:
            try:
                self._network.security_rules.begin_create_or_update(
                    resource_group_name=rg,
                    network_security_group_name=nsg_name,
                    security_rule_name="ch-allow-ssh",
                    security_rule_parameters={
                        "protocol": "Tcp",
                        "source_address_prefix": "0.0.0.0/0",
                        "source_port_range": "*",
                        "destination_address_prefix": "*",
                        "destination_port_range": "22",
                        "access": "Allow",
                        "direction": "Inbound",
                        "priority": 300,
                    },
                ).result()
            except Exception:
                pass

        public_ip_id = ""
        if spec.public_ip:
            pip = self._network.public_ip_addresses.begin_create_or_update(
                resource_group_name=rg,
                public_ip_address_name=pip_name,
                parameters={
                    "location": spec.region or self._region,
                    "sku": {"name": "Basic"},
                    "public_ip_allocation_method": "Dynamic",
                },
            ).result()
            public_ip_id = pip.id

        ip_cfg: dict[str, Any] = {
            "name": "ipconfig1",
            "subnet": {"id": subnet.id},
            "private_ip_allocation_method": "Dynamic",
        }
        if public_ip_id:
            ip_cfg["public_ip_address"] = {"id": public_ip_id}

        nsg = self._network.network_security_groups.get(resource_group_name=rg, network_security_group_name=nsg_name)
        nic = self._network.network_interfaces.begin_create_or_update(
            resource_group_name=rg, network_interface_name=nic_name,
            parameters={
                "location": spec.region or self._region,
                "ip_configurations": [ip_cfg],
                "network_security_group": {"id": nsg.id},
            },
        ).result()
        return nic.id

    def _to_instance(self, vm: Any, resource_group: str) -> Instance:
        public_ip = ""
        private_ip = ""
        nic_id = ""
        if vm.network_profile and vm.network_profile.network_interfaces:
            nic_id = vm.network_profile.network_interfaces[0].id
            try:
                nic_rg, nic_name = _split_nic_id(nic_id)
                nic = self._network.network_interfaces.get(resource_group_name=nic_rg, network_interface_name=nic_name)
                if nic.ip_configurations:
                    ip_cfg = nic.ip_configurations[0]
                    private_ip = ip_cfg.private_ip_address or ""
                    if ip_cfg.public_ip_address and ip_cfg.public_ip_address.id:
                        pip_rg, pip_name = _split_pip_id(ip_cfg.public_ip_address.id)
                        pip = self._network.public_ip_addresses.get(resource_group_name=pip_rg, public_ip_address_name=pip_name)
                        public_ip = pip.ip_address or ""
            except Exception:
                pass

        state = "unknown"
        if getattr(vm, "instance_view", None) and vm.instance_view.statuses:
            for status in vm.instance_view.statuses:
                code = status.code or ""
                if code.startswith("PowerState/"):
                    state = _map_state(code.split("/", 1)[1])
                    break

        launched = None
        if getattr(vm, "time_created", None):
            launched = vm.time_created
            if launched.tzinfo is None:
                launched = launched.replace(tzinfo=timezone.utc)

        instance_type = ""
        if vm.hardware_profile and vm.hardware_profile.vm_size:
            instance_type = vm.hardware_profile.vm_size

        image_ref = ""
        if vm.storage_profile and vm.storage_profile.image_reference:
            ref = vm.storage_profile.image_reference
            image_ref = ":".join(filter(None, [ref.publisher, ref.offer, ref.sku, ref.version]))

        return Instance(
            id=f"{resource_group}:{vm.name}",
            name=vm.name,
            state=state,
            region=vm.location or "",
            zone=",".join(vm.zones or []) if getattr(vm, "zones", None) else "",
            instance_type=instance_type,
            public_ip=public_ip,
            private_ip=private_ip,
            tags=dict(vm.tags or {}),
            image=image_ref,
            arch="arm64" if "p" in instance_type.lower() else "x86_64",
            disk_gb=int(vm.storage_profile.os_disk.disk_size_gb) if (vm.storage_profile and vm.storage_profile.os_disk and vm.storage_profile.os_disk.disk_size_gb) else 0,
            launched_at=launched,
            security_groups=[],
        )


def _map_state(state: str) -> str:
    s = (state or "").lower()
    if s in {"running", "starting"}:
        return "running"
    if s in {"stopped", "stopping", "deallocated", "deallocating"}:
        return "stopped"
    if s in {"creating", "updating"}:
        return "pending"
    return s or "unknown"


def _proto_to_azure(proto: str) -> str:
    if proto == "tcp":
        return "Tcp"
    if proto == "udp":
        return "Udp"
    if proto == "icmp":
        return "Icmp"
    return "*"


def _flat_cidrs(prefix: Optional[str], prefixes: Optional[list[str]]) -> list[str]:
    out: list[str] = []
    if prefix:
        out.append(prefix)
    if prefixes:
        out.extend(prefixes)
    return out


def _resource_group_from_id(resource_id: Optional[str]) -> str:
    if not resource_id:
        return ""
    parts = resource_id.split("/")
    if "resourceGroups" in parts:
        idx = parts.index("resourceGroups")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def _split_vm_id(instance_id: str) -> tuple[str, str]:
    if ":" in instance_id:
        rg, name = instance_id.split(":", 1)
        return rg, name
    raise ValueError(f"Azure 实例 id 格式应为 'resource_group:vm_name'，收到: {instance_id}")


def _split_nic_id(nic_id: str) -> tuple[str, str]:
    parts = nic_id.split("/")
    rg = _resource_group_from_id(nic_id)
    name = parts[-1] if parts else ""
    return rg, name


def _split_pip_id(pip_id: str) -> tuple[str, str]:
    parts = pip_id.split("/")
    rg = _resource_group_from_id(pip_id)
    name = parts[-1] if parts else ""
    return rg, name


def _split_nsg_target(target: str) -> tuple[str, str]:
    if ":" in target:
        rg, name = target.split(":", 1)
        return rg, name
    raise ValueError(f"Azure NSG target 格式应为 'resource_group:nsg_name'，收到: {target}")


def _resolve_image(image: str) -> tuple[str, str, str]:
    if image in AZURE_IMAGE_ALIASES:
        return AZURE_IMAGE_ALIASES[image]
    parts = image.split(":")
    if len(parts) >= 3:
        return parts[0], parts[1], parts[2]
    raise ValueError(
        f"Azure 未识别的镜像: {image}（可用别名：{', '.join(AZURE_IMAGE_ALIASES.keys())} 或直接传 publisher:offer:sku）"
    )
