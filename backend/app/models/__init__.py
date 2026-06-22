from datetime import date, datetime
from typing import Literal, Optional

from sqlalchemy import (
    JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey,
    Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

Provider = Literal["aws", "gcp", "oracle", "azure"]


class CloudAccount(Base):
    """云账户。每个白嫖账号独立平级，没有主从关系。

    凭据 (credentials_enc 解密后):
      AWS:    {access_key_id, secret_access_key}
      GCP:    完整 SA JSON
      Oracle: {tenancy, user, fingerprint, key_pem, region}
      Azure:  {tenant_id, client_id, client_secret, subscription_id}
    """

    __tablename__ = "cloud_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    credentials_enc: Mapped[str] = mapped_column(Text)
    default_region: Mapped[str] = mapped_column(String(64), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    group_tag: Mapped[str] = mapped_column(String(64), default="", index=True)
    note: Mapped[str] = mapped_column(String(255), default="")

    # 免费额度配置（每月）
    monthly_traffic_gb: Mapped[float] = mapped_column(Float, default=1.0)  # 免费出站 GB

    # 赠金 / 试用余额（手动维护）
    credit_total_usd: Mapped[float] = mapped_column(Float, default=0.0)
    credit_used_usd: Mapped[float] = mapped_column(Float, default=0.0)
    credit_expires_at: Mapped[Optional[date]] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    instances: Mapped[list["InstanceState"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    schedules: Mapped[list["Schedule"]] = relationship(back_populates="account", cascade="all, delete-orphan")
    monthly_traffic: Mapped[list["MonthlyTraffic"]] = relationship(cascade="all, delete-orphan")


class InstanceState(Base):
    __tablename__ = "instance_states"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str] = mapped_column(String(128), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    region: Mapped[str] = mapped_column(String(64), default="")
    zone: Mapped[str] = mapped_column(String(64), default="")
    instance_type: Mapped[str] = mapped_column(String(64), default="")
    state: Mapped[str] = mapped_column(String(32), default="unknown")
    public_ip: Mapped[str] = mapped_column(String(64), default="")
    private_ip: Mapped[str] = mapped_column(String(64), default="")
    tags: Mapped[dict] = mapped_column(JSON, default=dict)

    # 富信息（list 时一起从云拉，显示在卡片上）
    image: Mapped[str] = mapped_column(String(255), default="")
    arch: Mapped[str] = mapped_column(String(32), default="")
    vcpus: Mapped[int] = mapped_column(Integer, default=0)
    memory_mb: Mapped[int] = mapped_column(Integer, default=0)
    disk_gb: Mapped[int] = mapped_column(Integer, default=0)
    launched_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    security_groups: Mapped[list] = mapped_column(JSON, default=list)

    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 流量自动管理
    traffic_limit_gb: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = 用账号默认
    auto_stopped_by_traffic: Mapped[bool] = mapped_column(Boolean, default=False)

    # SSH 采集相关
    ssh_user: Mapped[str] = mapped_column(String(64), default="")
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_password_enc: Mapped[str] = mapped_column(Text, default="")  # Fernet 加密的 SSH 密码（可选）
    iface: Mapped[str] = mapped_column(String(32), default="")  # 主出口接口名，自动识别后缓存
    last_alive_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_collect_error: Mapped[str] = mapped_column(Text, default="")

    # 实时资源监控（每次 SSH 采集时更新）
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mem_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mem_total_mb: Mapped[int] = mapped_column(Integer, default=0)
    mem_used_mb: Mapped[int] = mapped_column(Integer, default=0)
    load1: Mapped[float] = mapped_column(Float, default=0.0)
    load5: Mapped[float] = mapped_column(Float, default=0.0)
    uptime_sec: Mapped[int] = mapped_column(Integer, default=0)
    last_cpu_total: Mapped[int] = mapped_column(BigInteger, default=0)
    last_cpu_idle: Mapped[int] = mapped_column(BigInteger, default=0)

    account: Mapped[CloudAccount] = relationship(back_populates="instances")

    __table_args__ = (UniqueConstraint("account_id", "instance_id", name="uq_account_instance"),)


class MonthlyTraffic(Base):
    """每月每实例流量累计。

    /proc/net/dev 是开机以来的累计计数器，重启会清零。
    我们存上次读数 last_counter_*，下次读时算 diff（处理回绕）累加到 bytes_*。
    """

    __tablename__ = "monthly_traffic"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str] = mapped_column(String(128), index=True)
    year_month: Mapped[str] = mapped_column(String(7), index=True)  # "2026-06"

    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0)

    last_counter_in: Mapped[int] = mapped_column(BigInteger, default=0)
    last_counter_out: Mapped[int] = mapped_column(BigInteger, default=0)
    last_sampled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    warned_80: Mapped[bool] = mapped_column(Boolean, default=False)  # 80% 通知去重

    __table_args__ = (UniqueConstraint("account_id", "instance_id", "year_month", name="uq_acc_inst_ym"),)


class TrafficSample(Base):
    """每次 SSH 采集时落一行，用于画历史曲线。

    存的是"采集时本月累计字节数"，前端可以用相邻两个采样点求 diff 看变化率。
    保留期 60 天（cron 任务清理），避免无限膨胀。
    """

    __tablename__ = "traffic_samples"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str] = mapped_column(String(128), index=True)
    sampled_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    bytes_in: Mapped[int] = mapped_column(BigInteger, default=0)
    bytes_out: Mapped[int] = mapped_column(BigInteger, default=0)
    cpu_pct: Mapped[float] = mapped_column(Float, default=0.0)
    mem_pct: Mapped[float] = mapped_column(Float, default=0.0)


class BillingTick(Base):
    """估算账单 tick：每 30 分钟一行（per account）。

    cost_usd = sum(running 实例的 hourly_usd) × 0.5（30 分钟）
    用于"实时"看本月推算花费，无视云商 6-24h 账单延迟。
    保留 90 天后清理。
    """

    __tablename__ = "billing_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    running_count: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)  # {instance_id: per_tick_cost}


class SSHKey(Base):
    """统一 SSH 私钥。CloudHelper 用它登所有实例采流量/保活。

    单用户场景一般只有一把，但允许多把（按 group_tag 分配）。
    """

    __tablename__ = "ssh_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    private_key_enc: Mapped[str] = mapped_column(Text)  # Fernet 加密后的 OpenSSH 私钥
    public_key: Mapped[str] = mapped_column(Text)  # 公钥明文，方便用户拷贝去填到云
    passphrase_enc: Mapped[str] = mapped_column(Text, default="")  # 可选
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("cloud_accounts.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(16))
    trigger_type: Mapped[str] = mapped_column(String(8), default="cron")  # cron | date
    cron: Mapped[str] = mapped_column(String(64), default="")  # trigger_type=cron 时使用
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)  # trigger_type=date 时使用 (UTC)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped[CloudAccount] = relationship(back_populates="schedules")


class InstancePrice(Base):
    """实例价格缓存。AWS 走免费 Pricing API；GCP/Oracle 用 fallback。"""

    __tablename__ = "instance_prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(16), index=True)
    region: Mapped[str] = mapped_column(String(64), index=True)
    instance_type: Mapped[str] = mapped_column(String(64), index=True)
    hourly_usd: Mapped[float] = mapped_column(Float, default=0.0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint("provider", "region", "instance_type", name="uq_price"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(255), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
