from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.models import CloudAccount, InstanceState, MonthlyTraffic
from app.providers import make_provider
from app.providers.base import CreateInstanceSpec
from app.schemas import (
    BulkAction, InstanceCreate, InstanceLimitUpdate, InstanceOut,
    InstanceSSHPassword, InstanceSSHUpdate,
)
from app.services.audit import audit
from app.services.pricing import get_price
from app.services.ssh_collector import collect_one
from app.core.crypto import get_crypto

router = APIRouter()
log = logging.getLogger(__name__)


def _ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _get_account(account_id: int, db: Session) -> CloudAccount:
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    return acc


def _state_to_out(st: InstanceState, mt: MonthlyTraffic | None,
                  acc: CloudAccount, db: Optional[Session] = None) -> InstanceOut:
    bytes_out = mt.bytes_out if mt else 0
    bytes_in = mt.bytes_in if mt else 0
    out_gb = bytes_out / (1024 ** 3) if bytes_out else 0.0
    total_gb = (bytes_in + bytes_out) / (1024 ** 3) if mt else 0.0
    limit_gb = st.traffic_limit_gb if st.traffic_limit_gb > 0 else acc.monthly_traffic_gb
    pct = (out_gb / limit_gb * 100) if limit_gb > 0 else 0.0
    hourly = 0.0
    if db is not None and st.instance_type:
        hourly = get_price(db, acc.provider, st.region, st.instance_type, account=acc)
    daily = hourly * 24 if st.state == "running" else 0.0
    return InstanceOut(
        id=st.instance_id, name=st.name, state=st.state,
        region=st.region, zone=st.zone, instance_type=st.instance_type,
        public_ip=st.public_ip, private_ip=st.private_ip, tags=st.tags or {},
        image=st.image, arch=st.arch, vcpus=st.vcpus, memory_mb=st.memory_mb,
        disk_gb=st.disk_gb, launched_at=st.launched_at,
        security_groups=st.security_groups or [],
        traffic_limit_gb=limit_gb,
        monthly_traffic_gb=total_gb,
        monthly_traffic_out_gb=out_gb,
        monthly_traffic_pct=pct,
        auto_stopped_by_traffic=st.auto_stopped_by_traffic,
        last_alive_at=st.last_alive_at,
        last_collect_error=st.last_collect_error,
        ssh_user=st.ssh_user, ssh_port=st.ssh_port, iface=st.iface,
        has_ssh_password=bool(st.ssh_password_enc),
        cpu_pct=st.cpu_pct, mem_pct=st.mem_pct,
        mem_total_mb=st.mem_total_mb, mem_used_mb=st.mem_used_mb,
        load1=st.load1, load5=st.load5, uptime_sec=st.uptime_sec,
        hourly_usd=hourly, daily_usd=daily,
        account_id=acc.id, account_name=acc.name, account_provider=acc.provider,
    )


def _enriched_for_account(db: Session, acc: CloudAccount) -> list[InstanceOut]:
    states = db.scalars(select(InstanceState).where(
        InstanceState.account_id == acc.id,
        InstanceState.state.not_in(("terminated", "shutting-down")),
    )).all()
    if not states:
        return []
    ym = _ym()
    mt_map = {(m.instance_id): m for m in db.scalars(select(MonthlyTraffic).where(
        MonthlyTraffic.account_id == acc.id, MonthlyTraffic.year_month == ym,
    )).all()}
    return [_state_to_out(s, mt_map.get(s.instance_id), acc, db) for s in states]


def _sync_account_instances(db: Session, acc: CloudAccount) -> int:
    provider = make_provider(acc, db=db)
    items = provider.list_instances()
    existing = {s.instance_id: s for s in db.scalars(
        select(InstanceState).where(InstanceState.account_id == acc.id)
    ).all()}
    seen = set()
    for inst in items:
        seen.add(inst.id)
        st = existing.get(inst.id)
        if not st:
            st = InstanceState(account_id=acc.id, instance_id=inst.id)
            db.add(st)
        st.name = inst.name
        st.region = inst.region
        st.zone = inst.zone
        st.instance_type = inst.instance_type
        st.state = inst.state
        st.public_ip = inst.public_ip
        st.private_ip = inst.private_ip
        st.tags = inst.tags
        st.image = inst.image
        st.arch = inst.arch
        st.disk_gb = inst.disk_gb
        st.launched_at = inst.launched_at
        st.security_groups = inst.security_groups
    for inst_id, st in existing.items():
        if inst_id not in seen:
            db.delete(st)
    db.commit()
    return len(items)


@router.get("/staleness")
def get_staleness(account_id: int, db: Session = Depends(get_db)):
    _get_account(account_id, db)
    last = db.scalar(select(func.max(InstanceState.updated_at)).where(
        InstanceState.account_id == account_id
    ))
    total = db.scalar(select(func.count(InstanceState.id)).where(
        InstanceState.account_id == account_id
    ))
    return {"account_id": account_id, "last_synced_at": last, "instance_count": total or 0}


@router.get("", response_model=list[InstanceOut])
def list_instances(
    account_id: int,
    refresh: bool = Query(False),
    db: Session = Depends(get_db),
):
    acc = _get_account(account_id, db)
    if refresh:
        _sync_account_instances(db, acc)
    return _enriched_for_account(db, acc)


@router.get("/options/security-groups")
def list_sgs(account_id: int, region: str, db: Session = Depends(get_db)):
    """前端创建表单用：列出该 region 下的安全组（AWS）/ firewall tag（GCP）。"""
    acc = _get_account(account_id, db)
    provider = make_provider(acc, db=db)
    try:
        sgs = provider.list_security_groups(region)
        return [{"id": sg.id, "name": sg.name, "description": sg.description, "vpc_id": sg.vpc_id} for sg in sgs]
    except Exception as e:
        raise HTTPException(400, f"列举失败: {e}")


@router.post("", response_model=InstanceOut)
def create_instance(account_id: int, payload: InstanceCreate, db: Session = Depends(get_db)):
    acc = _get_account(account_id, db)
    provider = make_provider(acc, db=db)

    # 如果勾选了密码登录，把 cloud-init 脚本拼到 user_data 前面
    user_data = payload.user_data or ""
    if payload.enable_password_login and payload.root_password:
        user_data = _build_password_userdata(
            payload.root_password,
            allow_root=payload.enable_root_login,
        ) + ("\n\n" + user_data if user_data else "")

    spec = CreateInstanceSpec(
        name=payload.name, region=payload.region, zone=payload.zone,
        instance_type=payload.instance_type, image=payload.image,
        network=payload.network, firewall_groups=payload.firewall_groups,
        public_ip=payload.public_ip, tags=payload.tags, user_data=user_data,
        disk_size_gb=payload.disk_size_gb, disk_type=payload.disk_type,
    )
    try:
        inst = provider.create_instance(spec)
    except Exception as e:
        msg = str(e)
        if "InvalidParameterCombination" in msg and "Free Tier" in msg:
            msg += "\n\n💡 提示：当前 region 的此规格不在 Free Tier。换 t3.micro 或换 us-east-1 region 试试。"
        elif "InvalidGroup.NotFound" in msg or "Security group" in msg:
            msg += "\n\n💡 提示：选中的安全组不存在或不属于本 region/VPC，请重新选择"
        elif "VcpuLimitExceeded" in msg:
            msg += "\n\n💡 提示：账户 vCPU 配额已用满，去 AWS Console → Service Quotas 申请提升"
        elif "InvalidSubnet" in msg:
            msg += "\n\n💡 提示：子网 ID 无效，留空使用默认 VPC"
        elif "Oracle 未找到可用子网" in msg:
            msg += "\n\n💡 提示：请在账户凭据里配置 compartment_id，或在创建时指定 network=subnet_ocid。"
        elif "Oracle 未找到镜像" in msg:
            msg += "\n\n💡 提示：Oracle 可直接填 image OCID，或用别名 oracle-8 / oracle-9 / ubuntu-22.04 / ubuntu-24.04。"
        elif "NotAuthorizedOrNotFound" in msg:
            msg += "\n\n💡 提示：请检查 Oracle 凭据（user/fingerprint/tenancy/key）及目标 compartment 的权限。"
        raise HTTPException(400, msg)

    # ssh_user 策略：按镜像猜默认账户（cloud-init 给所有常见账户都设了同密码）
    # 即使用户勾了 "允许 root"，也用镜像默认账户，因为 root 登录在很多镜像里
    # 依赖 cloud-init 改 sshd_config 是否生效，不可靠
    ssh_user = _guess_ssh_user(payload.image)
    ssh_pwd_enc = ""
    if payload.enable_password_login and payload.root_password:
        ssh_pwd_enc = get_crypto().encrypt(payload.root_password)

    st = InstanceState(
        account_id=account_id, instance_id=inst.id, name=inst.name,
        region=inst.region, zone=inst.zone, instance_type=inst.instance_type,
        state=inst.state, public_ip=inst.public_ip, private_ip=inst.private_ip,
        tags=inst.tags, image=inst.image, arch=inst.arch, disk_gb=inst.disk_gb,
        launched_at=inst.launched_at, security_groups=inst.security_groups,
        ssh_user=ssh_user, ssh_password_enc=ssh_pwd_enc,
    )
    db.add(st)
    db.commit()
    db.refresh(st)
    audit(db, action="instance.create", target=inst.id,
          detail={"account_id": account_id,
                  "spec": payload.model_dump(exclude={"root_password"}),
                  "with_password": bool(ssh_pwd_enc)})

    # 如果需要密码登录，自动加 22 防火墙规则（如果还没有）
    if payload.enable_password_login:
        try:
            from app.services.diagnose import ensure_ssh_firewall
            r = ensure_ssh_firewall(db, account_id, inst.id)
            if r.get("added"):
                audit(db, action="instance.auto_open_22", target=inst.id,
                      detail={"account_id": account_id, "sg": r.get("sg_target")})
        except Exception as e:
            log.warning("auto open 22 failed: %s", e)

    return _state_to_out(st, None, acc, db)


def _guess_ssh_user(image: str) -> str:
    img = (image or "").lower()
    if "ubuntu" in img: return "ubuntu"
    if "amzn" in img or "amazon" in img or "al2023" in img: return "ec2-user"
    if "debian" in img: return "admin"
    if "centos" in img or "rhel" in img or "rocky" in img: return "centos"
    if "oracle" in img or "opc" in img: return "opc"
    return "root"


def _build_password_userdata(password: str, allow_root: bool) -> str:
    """生成 cloud-init cloud-config（YAML）。

    关键改进：不再 sed 改 sshd_config（容易改坏导致 sshd 启动失败）。
    改为 write_files 直接放一个完整的 sshd_config 进去，保证语法 100% 正确；
    并清掉 /etc/ssh/sshd_config.d/*.conf （否则 60-cloudimg-settings.conf 这种
    可能覆盖我们的设置 — Ubuntu 22.04 默认就是这样禁的 root）。
    """
    root_directive = "yes" if allow_root else "prohibit-password"

    # 一个最小化、跨发行版能跑的 sshd_config
    # 故意只列我们关心的设置 + 必须的几个；其它走 sshd 默认
    sshd_config = f"""# CloudHelper managed - keep minimal and explicit
Port 22
Protocol 2
HostKey /etc/ssh/ssh_host_rsa_key
HostKey /etc/ssh/ssh_host_ecdsa_key
HostKey /etc/ssh/ssh_host_ed25519_key

# Authentication
PermitRootLogin {root_directive}
PasswordAuthentication yes
KbdInteractiveAuthentication yes
PubkeyAuthentication yes
UsePAM yes
PermitEmptyPasswords no
MaxAuthTries 6
LoginGraceTime 60

# Connection
X11Forwarding yes
PrintMotd no
AcceptEnv LANG LC_*
Subsystem sftp /usr/lib/openssh/sftp-server
ClientAliveInterval 60
ClientAliveCountMax 3
"""

    return f"""#cloud-config
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  list: |
    root:{password}
    ec2-user:{password}
    ubuntu:{password}
    debian:{password}
    admin:{password}
    centos:{password}
    rocky:{password}
    opc:{password}
    fedora:{password}

write_files:
  - path: /etc/ssh/sshd_config
    permissions: '0644'
    owner: root:root
    content: |
{_indent(sshd_config, 6)}

runcmd:
  # 删除任何 sshd_config.d/*.conf（Ubuntu/Debian 经常用它禁掉密码登录）
  - bash -c 'rm -f /etc/ssh/sshd_config.d/*.conf 2>/dev/null || true'
  # 验证 sshd 配置语法
  - bash -c 'sshd -t 2>&1 | tee /var/log/cloudhelper-sshd-check.log'
  # 重启 sshd（兼容多种发行版的 service 名）
  - bash -c 'systemctl restart sshd 2>/dev/null || systemctl restart ssh 2>/dev/null || service ssh restart || service sshd restart || true'
  # 标记完成
  - bash -c 'echo "$(date) cloudhelper userdata done" >> /var/log/cloudhelper-init.log'
"""


def _indent(text: str, spaces: int) -> str:
    """每行前面加 N 个空格（YAML 块标量缩进）。"""
    pad = " " * spaces
    return "\n".join(pad + line for line in text.splitlines())


@router.post("/{instance_id}/force-start")
def force_start_instance(account_id: int, instance_id: str, region: str, zone: str = "", db: Session = Depends(get_db)):
    """强制启动：清除 auto_stopped_by_traffic 标记后启动。

    警告：超流量后云商可能开始计费，调用方需自行承担风险。
    """
    acc = _get_account(account_id, db)
    provider = make_provider(acc, db=db)
    provider.start_instance(instance_id, region, zone)
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id, InstanceState.instance_id == instance_id,
    ))
    if st:
        st.auto_stopped_by_traffic = False
        db.commit()
    audit(db, action="instance.force_start", target=instance_id,
          detail={"account_id": account_id, "warning": "may incur charges"})
    return {"ok": True}


@router.get("/{instance_id}/traffic-history")
def traffic_history(
    account_id: int, instance_id: str,
    days: int = 7,
    db: Session = Depends(get_db),
):
    """返回近 N 天的流量采样点，用于绘曲线。"""
    from datetime import timedelta
    from app.models import TrafficSample
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 90)))
    rows = db.scalars(select(TrafficSample).where(
        TrafficSample.account_id == account_id,
        TrafficSample.instance_id == instance_id,
        TrafficSample.sampled_at >= since,
    ).order_by(TrafficSample.sampled_at)).all()
    return [
        {
            "at": r.sampled_at.isoformat() + "Z",
            "bytes_in": r.bytes_in,
            "bytes_out": r.bytes_out,
            "cpu_pct": r.cpu_pct,
            "mem_pct": r.mem_pct,
        }
        for r in rows
    ]


@router.post("/{instance_id}/rotate-ip")
def rotate_ip(account_id: int, instance_id: str, region: str, zone: str = "", db: Session = Depends(get_db)):
    """切换公网 IP。可能耗时较久（AWS stop/start 等）。"""
    acc = _get_account(account_id, db)
    provider = make_provider(acc, db=db)
    new_ip = provider.rotate_public_ip(instance_id, region, zone)
    # 更新缓存
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id, InstanceState.instance_id == instance_id,
    ))
    if st:
        st.public_ip = new_ip
        db.commit()
    audit(db, action="instance.rotate_ip", target=instance_id,
          detail={"account_id": account_id, "new_ip": new_ip})
    return {"ok": True, "new_ip": new_ip}


@router.post("/{instance_id}/diagnose")
def diagnose_instance(account_id: int, instance_id: str, db: Session = Depends(get_db)):
    from app.services.diagnose import diagnose
    return diagnose(db, account_id, instance_id)


@router.post("/{instance_id}/ensure-ssh-firewall")
def ensure_firewall(account_id: int, instance_id: str, db: Session = Depends(get_db)):
    from app.services.diagnose import ensure_ssh_firewall
    return ensure_ssh_firewall(db, account_id, instance_id)


@router.post("/{instance_id}/start")
def start_instance(account_id: int, instance_id: str, region: str, zone: str = "", db: Session = Depends(get_db)):
    provider = make_provider(_get_account(account_id, db), db=db)
    provider.start_instance(instance_id, region, zone)
    audit(db, action="instance.start", target=instance_id, detail={"account_id": account_id})
    return {"ok": True}


@router.post("/{instance_id}/stop")
def stop_instance(account_id: int, instance_id: str, region: str, zone: str = "", db: Session = Depends(get_db)):
    provider = make_provider(_get_account(account_id, db), db=db)
    provider.stop_instance(instance_id, region, zone)
    audit(db, action="instance.stop", target=instance_id, detail={"account_id": account_id})
    return {"ok": True}


@router.delete("/{instance_id}")
def terminate_instance(account_id: int, instance_id: str, region: str, zone: str = "", db: Session = Depends(get_db)):
    provider = make_provider(_get_account(account_id, db), db=db)
    provider.terminate_instance(instance_id, region, zone)
    # 立刻从本地缓存中删除，前端不再显示
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id,
        InstanceState.instance_id == instance_id,
    ))
    if st:
        db.delete(st)
        db.commit()
    audit(db, action="instance.terminate", target=instance_id, detail={"account_id": account_id})
    return {"ok": True}


@router.put("/{instance_id}/traffic-limit")
def set_traffic_limit(account_id: int, instance_id: str, payload: InstanceLimitUpdate, db: Session = Depends(get_db)):
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id, InstanceState.instance_id == instance_id,
    ))
    if not st:
        raise HTTPException(404, "实例缓存不存在，先刷新")
    st.traffic_limit_gb = payload.traffic_limit_gb
    db.commit()
    audit(db, action="instance.set_traffic_limit", target=instance_id,
          detail={"account_id": account_id, "limit_gb": payload.traffic_limit_gb})
    return {"ok": True}


@router.put("/{instance_id}/ssh")
def set_ssh(account_id: int, instance_id: str, payload: InstanceSSHUpdate, db: Session = Depends(get_db)):
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id, InstanceState.instance_id == instance_id,
    ))
    if not st:
        raise HTTPException(404, "实例缓存不存在")
    st.ssh_user = payload.ssh_user
    st.ssh_port = payload.ssh_port
    db.commit()
    return {"ok": True}


@router.put("/{instance_id}/ssh-password")
def set_ssh_password(account_id: int, instance_id: str, payload: InstanceSSHPassword, db: Session = Depends(get_db)):
    """设置/清除实例的 SSH 密码。空字符串 = 清除。"""
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id, InstanceState.instance_id == instance_id,
    ))
    if not st:
        raise HTTPException(404, "实例缓存不存在")
    if payload.password:
        st.ssh_password_enc = get_crypto().encrypt(payload.password)
    else:
        st.ssh_password_enc = ""
    db.commit()
    audit(db, action="instance.set_ssh_password", target=instance_id,
          detail={"account_id": account_id, "set": bool(payload.password)})
    return {"ok": True}


@router.post("/{instance_id}/collect")
def trigger_collect(account_id: int, instance_id: str):
    """手动触发一次 SSH 采集（调试 / 立即看到效果用）。"""
    return collect_one(account_id, instance_id)
