"""云提供商抽象接口。AWS / GCP 各自实现。流量统计走 SSH 不在此接口。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Instance:
    id: str
    name: str
    state: str
    region: str
    zone: str
    instance_type: str
    public_ip: str = ""
    private_ip: str = ""
    tags: dict = field(default_factory=dict)
    image: str = ""
    arch: str = ""
    vcpus: int = 0
    memory_mb: int = 0
    disk_gb: int = 0
    launched_at: datetime | None = None
    security_groups: list = field(default_factory=list)


@dataclass
class FirewallRule:
    id: str
    direction: str  # ingress / egress
    protocol: str  # tcp / udp / icmp / all
    port_range: str
    cidrs: list[str]
    description: str = ""
    target: str = ""


@dataclass
class TrafficStat:
    instance_id: str
    bytes_in: int
    bytes_out: int
    window_start: datetime
    window_end: datetime


@dataclass
class CreateInstanceSpec:
    name: str
    region: str
    zone: str
    instance_type: str
    image: str
    network: str = ""
    firewall_groups: list[str] = field(default_factory=list)
    public_ip: bool = True
    tags: dict = field(default_factory=dict)
    user_data: str = ""
    disk_size_gb: int = 30
    disk_type: str = ""    # AWS: gp3/gp2/...; GCP: pd-balanced/pd-ssd/...


@dataclass
class SecurityGroupBrief:
    id: str
    name: str
    description: str = ""
    vpc_id: str = ""


@dataclass
class FreeTierItem:
    """Free Tier 单项用量（AWS）。"""
    service: str          # 例如 "Amazon Elastic Compute Cloud"
    description: str      # 例如 "750 Hrs of Linux t2.micro and t3.micro instances"
    actual_usage: float   # 实际用量
    forecasted_usage: float  # 月底预测
    limit: float          # 免费额度上限
    unit: str             # "Hrs" / "GB" / "Requests" 等
    actual_pct: float     # 已用百分比 0-100
    forecasted_pct: float


class CloudProvider(Protocol):
    name: str

    def list_regions(self) -> list[str]: ...
    def list_instances(self, region: str | None = None) -> list[Instance]: ...
    def get_instance(self, instance_id: str, region: str, zone: str = "") -> Instance: ...
    def create_instance(self, spec: CreateInstanceSpec) -> Instance: ...
    def start_instance(self, instance_id: str, region: str, zone: str = "") -> None: ...
    def stop_instance(self, instance_id: str, region: str, zone: str = "") -> None: ...
    def terminate_instance(self, instance_id: str, region: str, zone: str = "") -> None: ...
    def reboot_instance(self, instance_id: str, region: str, zone: str = "") -> None: ...

    def list_firewall_rules(self, region: str | None = None) -> list[FirewallRule]: ...
    def add_firewall_rule(self, rule: FirewallRule, region: str) -> str: ...
    def remove_firewall_rule(self, rule_id: str, region: str) -> None: ...
    def list_security_groups(self, region: str) -> list[SecurityGroupBrief]: ...

    def list_free_tier_usage(self) -> list[FreeTierItem]:
        """免费额度用量。仅 AWS 实现（GCP / Oracle / Azure 没有公开 API）。"""
        return []

    def get_traffic(
        self, instance_id: str, region: str, zone: str, start: datetime, end: datetime
    ) -> TrafficStat: ...

    def rotate_public_ip(self, instance_id: str, region: str, zone: str = "") -> str: ...

    def close(self) -> None:
        """释放底层 SDK 客户端 / 凭据持有的连接。默认无操作。"""
        ...
