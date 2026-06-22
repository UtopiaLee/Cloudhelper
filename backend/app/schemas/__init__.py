from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_serializer

Provider = Literal["aws", "gcp", "oracle", "azure"]


# ---------- 账户 ----------
class AccountCreate(BaseModel):
    name: str
    provider: Provider
    default_region: str = ""
    group_tag: str = ""
    note: str = ""
    monthly_traffic_gb: float = 1.0
    credit_total_usd: float = 0.0
    credit_used_usd: float = 0.0
    credit_expires_at: Optional[date] = None
    credentials: dict[str, Any]


class AccountOut(BaseModel):
    id: int
    name: str
    provider: Provider
    default_region: str
    enabled: bool
    group_tag: str
    note: str
    monthly_traffic_gb: float
    credit_total_usd: float
    credit_used_usd: float
    credit_expires_at: Optional[date]
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- SSH 密钥 ----------
class SSHKeyCreate(BaseModel):
    name: str
    private_key: str
    passphrase: str = ""
    is_default: bool = False


class SSHKeyOut(BaseModel):
    id: int
    name: str
    public_key: str
    is_default: bool
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- 实例 ----------
class InstanceOut(BaseModel):
    id: str
    name: str
    state: str
    region: str
    zone: str
    instance_type: str
    public_ip: str = ""
    private_ip: str = ""
    tags: dict[str, str] = Field(default_factory=dict)
    image: str = ""
    arch: str = ""
    vcpus: int = 0
    memory_mb: int = 0
    disk_gb: int = 0
    launched_at: Optional[datetime] = None
    security_groups: list[str] = Field(default_factory=list)
    traffic_limit_gb: float = 0.0
    monthly_traffic_gb: float = 0.0
    monthly_traffic_out_gb: float = 0.0
    monthly_traffic_pct: float = 0.0
    auto_stopped_by_traffic: bool = False
    last_alive_at: Optional[datetime] = None
    last_collect_error: str = ""
    ssh_user: str = ""
    ssh_port: int = 22
    iface: str = ""
    has_ssh_password: bool = False
    cpu_pct: float = 0.0
    mem_pct: float = 0.0
    mem_total_mb: int = 0
    mem_used_mb: int = 0
    load1: float = 0.0
    load5: float = 0.0
    uptime_sec: int = 0
    hourly_usd: float = 0.0
    daily_usd: float = 0.0
    account_id: int = 0
    account_name: str = ""
    account_provider: str = ""


class InstanceCreate(BaseModel):
    name: str
    region: str
    zone: str = ""
    instance_type: str
    image: str
    network: str = ""
    firewall_groups: list[str] = Field(default_factory=list)
    public_ip: bool = True
    tags: dict[str, str] = Field(default_factory=dict)
    user_data: str = ""
    disk_size_gb: int = 30
    disk_type: str = ""
    enable_password_login: bool = False
    enable_root_login: bool = False
    root_password: str = ""


class InstanceLimitUpdate(BaseModel):
    traffic_limit_gb: float = Field(ge=0)


class InstanceSSHUpdate(BaseModel):
    ssh_user: str = ""
    ssh_port: int = 22


class InstanceSSHPassword(BaseModel):
    password: str   # 空字符串 = 清除密码


class BulkAction(BaseModel):
    action: Literal["start", "stop", "restart", "terminate", "set-limit", "set-tag"]
    targets: list[dict[str, str]]
    # set-limit 时必填
    traffic_limit_gb: Optional[float] = None
    # set-tag 时必填（修改实例的 group_tag —— 实际是改账号 group？这里走 instance state 自带 tags）
    tag_value: Optional[str] = None


# ---------- 防火墙 ----------
class FirewallRuleIn(BaseModel):
    direction: Literal["ingress", "egress"]
    protocol: Literal["tcp", "udp", "icmp", "all"]
    port_range: str
    cidrs: list[str]
    description: str = ""
    target: str


class FirewallRuleOut(FirewallRuleIn):
    id: str


# ---------- 调度 ----------
class ScheduleIn(BaseModel):
    instance_id: str
    action: Literal["start", "stop", "restart", "destroy"]
    trigger_type: Literal["cron", "date"] = "cron"
    cron: str = ""           # trigger_type=cron 时必填
    run_at: Optional[datetime] = None  # trigger_type=date 时必填，UTC
    enabled: bool = True
    note: str = ""


class ScheduleOut(BaseModel):
    id: int
    account_id: int
    instance_id: str
    action: Literal["start", "stop", "restart", "destroy"]
    trigger_type: str
    cron: str
    run_at: Optional[datetime]
    enabled: bool
    note: str
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_serializer("run_at", "created_at", when_used="json")
    def _ser_utc(self, v: Optional[datetime]) -> Optional[str]:
        if v is None:
            return None
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.isoformat()


# ---------- Dashboard ----------
class DashboardSummary(BaseModel):
    accounts_total: int
    accounts_by_provider: dict[str, int]
    instances_total: int
    instances_running: int
    instances_stopped: int
    monthly_traffic_gb_total: float
    over_limit_count: int
    last_collected_at: Optional[datetime] = None


class BudgetSummary(BaseModel):
    account_id: int
    account_name: str
    provider: str
    credit_total_usd: float
    credit_used_usd: float
    credit_remaining_usd: float
    credit_expires_at: Optional[date]
    days_to_expiry: Optional[int]
    daily_burn_usd: float          # 当前所有 running 实例总日消耗
    monthly_burn_usd: float        # × 30
    days_until_credit_runs_out: Optional[float]  # remaining / daily_burn
    will_outlast_expiry: Optional[bool]
    instances: list[dict[str, Any]]  # [{id, name, hourly_usd, daily_usd, state}]
