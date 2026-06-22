"""实例诊断 + 创建后等待就绪。

诊断检查项：
  - 公网 IP 22 端口 TCP 可达性
  - SSH 认证（用存储的密码 / 默认密钥）
  - 防火墙规则中是否有 0.0.0.0/0:22 入站
  - 实例自身的 cloud-init 状态摘要（如果能 SSH 进去）
"""

from __future__ import annotations

import io
import json
import logging
import socket
import time
from typing import Optional

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.core.db import SessionLocal
from app.models import CloudAccount, InstanceState, SSHKey
from app.providers import make_provider
from app.services.ssh_common import connect_ssh

log = logging.getLogger(__name__)


def _load_default_key() -> Optional[paramiko.PKey]:
    with SessionLocal() as db:
        row = db.scalar(select(SSHKey).where(SSHKey.is_default.is_(True)))
        if not row:
            row = db.scalar(select(SSHKey).order_by(SSHKey.id))
        if not row:
            return None
        pem = get_crypto().decrypt(row.private_key_enc)
        pp = get_crypto().decrypt(row.passphrase_enc) if row.passphrase_enc else None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(pem), password=pp or None)
        except (paramiko.SSHException, ValueError):
            continue
    return None


def check_port(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, "open"
    except socket.timeout:
        return False, "timeout"
    except ConnectionRefusedError:
        return False, "connection refused"
    except OSError as e:
        return False, str(e)


def try_ssh(host: str, port: int, user: str, password: Optional[str],
            pkey: Optional[paramiko.PKey], timeout: float = 8.0) -> dict:
    """尝试 SSH 登录，返回 {ok, method, error}。"""
    try:
        client, method = connect_ssh(host, port, user, password, pkey)
        client.close()
        return {"ok": True, "method": method, "error": ""}
    except paramiko.AuthenticationException as e:
        return {"ok": False, "method": "password" if password else "key", "error": str(e)}
    except Exception as e:
        return {"ok": False, "method": "password" if password else "key", "error": str(e)}


def diagnose(db: Session, account_id: int, instance_id: str) -> dict:
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id,
        InstanceState.instance_id == instance_id,
    ))
    acc = db.get(CloudAccount, account_id)
    if not st or not acc:
        return {"ok": False, "error": "instance not found"}
    if not st.public_ip:
        return {"ok": False, "error": "no public IP"}

    result: dict = {
        "instance_id": instance_id,
        "host": st.public_ip,
        "port": st.ssh_port or 22,
        "ssh_user": st.ssh_user or "root",
        "checks": [],
    }

    # 1) 端口连通性
    ok, msg = check_port(st.public_ip, st.ssh_port or 22)
    result["checks"].append({"name": "TCP 22 端口", "ok": ok, "detail": msg})
    if not ok:
        result["checks"].append({
            "name": "建议", "ok": False,
            "detail": "防火墙没开 22。可在防火墙页面加入站规则 0.0.0.0/0:22"
        })

    # 2) SSH 认证
    password = ""
    if st.ssh_password_enc:
        try:
            password = get_crypto().decrypt(st.ssh_password_enc)
        except Exception:
            password = ""
    pkey = _load_default_key()
    auth = try_ssh(st.public_ip, st.ssh_port or 22, st.ssh_user or "root",
                   password or None, pkey, timeout=6.0)
    result["checks"].append({
        "name": f"SSH 认证（{auth.get('method')}）",
        "ok": auth["ok"],
        "detail": auth.get("error") or "登录成功",
    })

    # 3) cloud-init 状态（如果 SSH 通了）
    if auth["ok"]:
        try:
            ci_status = _ssh_run(st.public_ip, st.ssh_port or 22, st.ssh_user or "root",
                                 password or None, pkey, "cloud-init status 2>/dev/null || echo unavailable")
            result["checks"].append({
                "name": "cloud-init",
                "ok": "done" in ci_status or "running" in ci_status,
                "detail": ci_status.strip()[:200] or "no output",
            })
            sshd_check = _ssh_run(st.public_ip, st.ssh_port or 22, st.ssh_user or "root",
                                  password or None, pkey,
                                  "grep -h '^PasswordAuthentication\\|^PermitRootLogin' /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null | sort -u")
            result["checks"].append({
                "name": "sshd_config",
                "ok": "PasswordAuthentication yes" in sshd_check,
                "detail": sshd_check.strip()[:300] or "未读取",
            })
        except Exception as e:
            result["checks"].append({"name": "cloud-init / sshd", "ok": False, "detail": str(e)})

    # 4) 防火墙规则中是否有 22
    try:
        provider = make_provider(acc, db=db)
        rules = provider.list_firewall_rules(region=st.region)
        has_22 = any(_rule_opens_22_to_world(r) for r in rules)
        result["checks"].append({
            "name": "防火墙含 22 入站规则",
            "ok": has_22,
            "detail": "存在" if has_22 else "未找到 0.0.0.0/0:22 入站规则",
        })
    except Exception as e:
        result["checks"].append({"name": "防火墙", "ok": False, "detail": str(e)})

    result["ok"] = all(c["ok"] for c in result["checks"])
    return result


def _rule_opens_22_to_world(r) -> bool:
    """规则是否真正对外开放 22 端口。

    判断三要素：方向 ingress + 协议 tcp/all + 端口覆盖 22 + CIDR 包含 0.0.0.0/0
    """
    if r.direction != "ingress":
        return False
    if r.protocol not in ("tcp", "all"):
        return False
    if not (r.port_range == "*" or _port_covers_22(r.port_range)):
        return False
    # 必须有对外开放的 CIDR
    cidrs = r.cidrs or []
    return any(c in ("0.0.0.0/0", "::/0") or c.endswith("/0") for c in cidrs)


def _port_covers_22(pr: str) -> bool:
    if pr == "22":
        return True
    if "-" in pr:
        try:
            a, b = map(int, pr.split("-", 1))
            return a <= 22 <= b
        except ValueError:
            return False
    try:
        return int(pr) == 22
    except ValueError:
        return False


def _ssh_run(host: str, port: int, user: str, password: Optional[str],
             pkey: Optional[paramiko.PKey], cmd: str, timeout: float = 6.0) -> str:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        if password:
            client.connect(host, port=port, username=user, password=password,
                           timeout=timeout, look_for_keys=False, allow_agent=False)
        elif pkey:
            client.connect(host, port=port, username=user, pkey=pkey,
                           timeout=timeout, look_for_keys=False, allow_agent=False)
        else:
            return ""
        _, stdout, _ = client.exec_command(cmd, timeout=timeout)
        return stdout.read().decode("utf-8", errors="replace")
    finally:
        try:
            client.close()
        except Exception:
            pass


def wait_ssh_ready(account_id: int, instance_id: str, max_wait_sec: int = 120) -> dict:
    """轮询直到 SSH 可用，返回最终状态。给创建实例后台调用。"""
    start = time.time()
    last: dict = {}
    while time.time() - start < max_wait_sec:
        with SessionLocal() as db:
            last = diagnose(db, account_id, instance_id)
        if last.get("ok"):
            return last
        time.sleep(8)
    return last


def ensure_ssh_firewall(db: Session, account_id: int, instance_id: str) -> dict:
    """确保至少有一条 22 入站规则；若 SG 列表里没有，自动加一条到默认 SG。

    规则不会重复加（先检查）。返回 {added: bool, sg_target: str}。
    """
    from app.providers.base import FirewallRule
    st = db.scalar(select(InstanceState).where(
        InstanceState.account_id == account_id,
        InstanceState.instance_id == instance_id,
    ))
    acc = db.get(CloudAccount, account_id)
    if not st or not acc:
        return {"added": False, "error": "instance not found"}

    provider = make_provider(acc, db=db)
    rules = provider.list_firewall_rules(region=st.region)
    if any(_rule_opens_22_to_world(r) for r in rules):
        return {"added": False, "reason": "already has ssh rule"}

    # 找一个 SG / firewall 来加规则
    target = ""
    for sg in (st.security_groups or []):
        target = sg
        break
    if not target:
        # 取第一个 ingress 规则的 target
        for r in rules:
            if r.direction == "ingress" and r.target:
                target = r.target
                break
    if not target:
        return {"added": False, "error": "无法找到 SG/firewall 目标，请手动添加"}

    rule = FirewallRule(
        id="", direction="ingress", protocol="tcp", port_range="22",
        cidrs=["0.0.0.0/0"], description="CloudHelper auto-added", target=target,
    )
    try:
        provider.add_firewall_rule(rule, st.region)
        return {"added": True, "sg_target": target}
    except Exception as e:
        return {"added": False, "error": str(e), "sg_target": target}
