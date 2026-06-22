"""SSH 流量采集 + 保活探测。

每 10 分钟跑一次：
  - 对每个 state=running 的实例，SSH 进去：
    1) 识别主出口接口（默认路由的 dev）
    2) 只读该接口的 TX 字节（出站）+ RX（仅记录展示，不计入阈值）
  - 与上次读数算 diff（处理重启/回绕），累加到本月 MonthlyTraffic
  - 阈值：90% 关机；80% 发 webhook（每月每实例只发一次）
  - 入站流量不计入阈值
"""

from __future__ import annotations

import io
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

import paramiko
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.core.db import SessionLocal
from app.models import (
    CloudAccount, InstanceState, MonthlyTraffic, SSHKey, TrafficSample,
)
from app.providers import make_provider
from app.services.audit import audit, notify
from app.services.ssh_common import connect_ssh

log = logging.getLogger(__name__)

SSH_TIMEOUT = 8
SSH_BANNER_TIMEOUT = 20
MAX_WORKERS = 10
STOP_THRESHOLD_PCT = 90  # 出站超过限额的 90% 就关机
WARN_THRESHOLD_PCT = 80  # 80% 发 webhook 警告


def _default_ssh_user(image: str = "", tags: dict | None = None) -> str:
    if tags and tags.get("ssh_user"):
        return tags["ssh_user"]
    img = (image or "").lower()
    if "ubuntu" in img:
        return "ubuntu"
    if "amzn" in img or "amazon" in img:
        return "ec2-user"
    if "debian" in img:
        return "admin"
    if "centos" in img or "rhel" in img:
        return "centos"
    if "oracle" in img:
        return "opc"
    return "root"


def _load_default_key(db: Session) -> Optional[paramiko.PKey]:
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


def _exec(client: paramiko.SSHClient, cmd: str) -> str:
    _, stdout, _ = client.exec_command(cmd, timeout=SSH_TIMEOUT)
    return stdout.read().decode("utf-8", errors="replace")


def _detect_main_iface(client: paramiko.SSHClient) -> str:
    """识别主出口接口名。

    优先 ip route get 1.1.1.1（找到默认外网出口）。
    fallback：从 /proc/net/dev 找第一个非 lo/docker/veth/br/cni 的接口。
    """
    out = _exec(client, "ip route get 1.1.1.1 2>/dev/null").split()
    if "dev" in out:
        idx = out.index("dev")
        if idx + 1 < len(out):
            return out[idx + 1]
    data = _exec(client, "cat /proc/net/dev")
    for line in data.splitlines():
        if ":" not in line:
            continue
        iface = line.split(":", 1)[0].strip()
        if not iface or iface in ("lo", "Inter-", "face"):
            continue
        if iface.startswith(("docker", "veth", "br-", "cni", "virbr", "tun", "tap")):
            continue
        return iface
    return ""


def _read_iface_counters(client: paramiko.SSHClient, iface: str) -> tuple[int, int]:
    """读指定接口的 (rx_bytes, tx_bytes)。"""
    data = _exec(client, "cat /proc/net/dev")
    for line in data.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        if name.strip() != iface:
            continue
        cols = rest.split()
        if len(cols) < 16:
            continue
        return int(cols[0]), int(cols[8])
    return 0, 0


def _read_resources(client: paramiko.SSHClient) -> dict:
    """一次性读 CPU/内存/负载/uptime。"""
    out = _exec(
        client,
        "cat /proc/stat | head -1 && echo --- && cat /proc/meminfo | head -5 "
        "&& echo --- && cat /proc/loadavg && echo --- && cat /proc/uptime"
    )
    parts = out.split("---")
    res = {"cpu_total": 0, "cpu_idle": 0, "mem_total": 0, "mem_avail": 0,
           "load1": 0.0, "load5": 0.0, "uptime": 0}
    if len(parts) >= 1:
        # cpu  user nice system idle iowait irq softirq steal guest guest_nice
        cols = parts[0].split()
        if len(cols) >= 5 and cols[0] == "cpu":
            try:
                nums = [int(x) for x in cols[1:11]]
                res["cpu_total"] = sum(nums)
                res["cpu_idle"] = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
            except ValueError:
                pass
    if len(parts) >= 2:
        for line in parts[1].splitlines():
            if line.startswith("MemTotal:"):
                try: res["mem_total"] = int(line.split()[1])
                except (ValueError, IndexError): pass
            elif line.startswith("MemAvailable:"):
                try: res["mem_avail"] = int(line.split()[1])
                except (ValueError, IndexError): pass
    if len(parts) >= 3:
        cols = parts[2].split()
        if len(cols) >= 2:
            try:
                res["load1"] = float(cols[0])
                res["load5"] = float(cols[1])
            except ValueError:
                pass
    if len(parts) >= 4:
        cols = parts[3].split()
        if cols:
            try: res["uptime"] = int(float(cols[0]))
            except ValueError: pass
    return res


def _diff_counter(prev: int, curr: int) -> int:
    if curr >= prev:
        return curr - prev
    return curr  # 重启或回绕


def _ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def collect_one(account_id: int, instance_id: str) -> dict:
    """对单个实例做一次采集。"""
    with SessionLocal() as db:
        st = db.scalar(select(InstanceState).where(
            InstanceState.account_id == account_id,
            InstanceState.instance_id == instance_id,
        ))
        if not st or not st.public_ip:
            return {"ok": False, "error": "no public ip"}
        if st.state != "running":
            return {"ok": False, "error": "not running"}

        # 凭据：密码 优先 + 密钥 fallback（顺序跟 Shell 一致）
        password = ""
        if st.ssh_password_enc:
            try:
                password = get_crypto().decrypt(st.ssh_password_enc)
            except Exception:
                password = ""
        key = _load_default_key(db)
        if not password and not key:
            return {"ok": False, "error": "no ssh credentials: 请在实例上设置密码（创建时勾选密码登录，或 Shell 弹窗时勾选保存）或在 SSH 密钥页配密钥"}

        user = st.ssh_user or _default_ssh_user(image=st.image, tags=st.tags or {})
        port = st.ssh_port or 22

        try:
            client, method = connect_ssh(st.public_ip, port, user, password or None, key)
        except Exception as e:
            st.last_collect_error = str(e)[:500]
            db.commit()
            return {"ok": False, "error": str(e)}

        try:
            iface = st.iface or _detect_main_iface(client)
            if not iface:
                return {"ok": False, "error": "cannot detect main interface"}
            rx, tx = _read_iface_counters(client, iface)
            resources = _read_resources(client)
        except Exception as e:
            st.last_collect_error = str(e)[:500]
            db.commit()
            return {"ok": False, "error": str(e)}
        finally:
            client.close()

        if iface != st.iface:
            st.iface = iface

        ym = _ym()
        mt = db.scalar(select(MonthlyTraffic).where(
            MonthlyTraffic.account_id == account_id,
            MonthlyTraffic.instance_id == instance_id,
            MonthlyTraffic.year_month == ym,
        ))
        if not mt:
            mt = MonthlyTraffic(
                account_id=account_id, instance_id=instance_id, year_month=ym,
                last_counter_in=rx, last_counter_out=tx,
            )
            db.add(mt)
        else:
            d_in = _diff_counter(mt.last_counter_in, rx)
            d_out = _diff_counter(mt.last_counter_out, tx)
            mt.bytes_in += d_in
            mt.bytes_out += d_out
            mt.last_counter_in = rx
            mt.last_counter_out = tx
        mt.last_sampled_at = datetime.utcnow()

        # CPU 利用率：用 /proc/stat 两次差值
        cpu_total = resources.get("cpu_total", 0)
        cpu_idle = resources.get("cpu_idle", 0)
        if st.last_cpu_total > 0 and cpu_total > st.last_cpu_total:
            d_total = cpu_total - st.last_cpu_total
            d_idle = cpu_idle - st.last_cpu_idle
            st.cpu_pct = max(0.0, min(100.0, (d_total - d_idle) / d_total * 100)) if d_total else 0.0
        st.last_cpu_total = cpu_total
        st.last_cpu_idle = cpu_idle

        # 内存
        mem_total_kb = resources.get("mem_total", 0)
        mem_avail_kb = resources.get("mem_avail", 0)
        if mem_total_kb > 0:
            st.mem_total_mb = mem_total_kb // 1024
            st.mem_used_mb = max(0, (mem_total_kb - mem_avail_kb) // 1024)
            st.mem_pct = (mem_total_kb - mem_avail_kb) / mem_total_kb * 100

        st.load1 = resources.get("load1", 0.0)
        st.load5 = resources.get("load5", 0.0)
        st.uptime_sec = resources.get("uptime", 0)

        st.last_alive_at = datetime.utcnow()
        st.last_collect_error = ""

        # 落一份采样（用于历史曲线）
        db.add(TrafficSample(
            account_id=account_id, instance_id=instance_id,
            bytes_in=mt.bytes_in, bytes_out=mt.bytes_out,
            cpu_pct=st.cpu_pct, mem_pct=st.mem_pct,
        ))
        db.commit()

        return {
            "ok": True, "instance_id": instance_id, "year_month": ym,
            "iface": iface,
            "bytes_in": mt.bytes_in, "bytes_out": mt.bytes_out,
            "cpu_pct": st.cpu_pct, "mem_pct": st.mem_pct,
        }


def collect_all() -> dict:
    with SessionLocal() as db:
        targets = db.execute(select(InstanceState.account_id, InstanceState.instance_id).where(
            InstanceState.state == "running",
            InstanceState.public_ip != "",
        )).all()

    summary = {"total": len(targets), "ok": 0, "failed": 0}
    if not targets:
        return summary

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(collect_one, a, i) for a, i in targets]
        for f in as_completed(futures):
            try:
                r = f.result()
                if r.get("ok"):
                    summary["ok"] += 1
                else:
                    summary["failed"] += 1
            except Exception:
                summary["failed"] += 1

    _enforce_limits()
    return summary


def _is_free_instance(provider: str, region: str, instance_type: str, hourly_usd: float) -> bool:
    """判断该实例是否为免费规格。

    依据：
      1. pricing 计算出 hourly_usd == 0（Always Free 区域 e2-micro / Oracle A1.Flex 等）
      2. instance_type 在已知免费规格列表里（即使 pricing 失败用 fallback）
    """
    if hourly_usd <= 0:
        return True
    free_types = {
        "aws": {"t2.micro", "t3.micro"},                          # 12 个月免费
        "gcp": {"e2-micro"},                                       # 仅 us-west1/central1/east1 免费
        "oracle": {"VM.Standard.E2.1.Micro", "VM.Standard.A1.Flex"},  # Always Free
        "azure": {"Standard_B1s"},                                 # 12 个月免费
    }
    return instance_type in free_types.get(provider, set())


def _enforce_limits() -> None:
    """对超过出站阈值的实例：仅对免费 VPS 在 90% 关机；付费实例只 80% 警告。"""
    from app.services.pricing import get_price

    with SessionLocal() as db:
        ym = _ym()
        rows = db.execute(select(InstanceState, MonthlyTraffic, CloudAccount).join(
            MonthlyTraffic,
            (MonthlyTraffic.account_id == InstanceState.account_id)
            & (MonthlyTraffic.instance_id == InstanceState.instance_id)
            & (MonthlyTraffic.year_month == ym),
        ).join(CloudAccount, CloudAccount.id == InstanceState.account_id).where(
            InstanceState.state == "running",
        )).all()

        for st, mt, acc in rows:
            limit_gb = st.traffic_limit_gb if st.traffic_limit_gb > 0 else acc.monthly_traffic_gb
            if limit_gb <= 0:
                continue
            limit_bytes = int(limit_gb * (1024 ** 3))
            out = mt.bytes_out
            if out <= 0:
                continue
            pct = out / limit_bytes * 100

            # 80% webhook（每月每实例只发一次，免费/付费都警告）
            if pct >= WARN_THRESHOLD_PCT and not mt.warned_80:
                mt.warned_80 = True
                db.commit()
                notify(f"[traffic] ⚠️ {acc.name}/{st.instance_id} 出站已用 {pct:.1f}% ({out/1e9:.2f}/{limit_gb} GB)")

            # 90% 关机：只针对免费实例
            if pct >= STOP_THRESHOLD_PCT and not st.auto_stopped_by_traffic:
                # 计算价格判断是否免费
                try:
                    hourly = get_price(db, acc.provider, st.region, st.instance_type, account=acc) if st.instance_type else 0.0
                except Exception as e:
                    log.warning("pricing failed for %s, assume free: %s", st.instance_id, e)
                    hourly = 0.0
                is_free = _is_free_instance(acc.provider, st.region, st.instance_type, hourly)

                if not is_free:
                    # 付费实例只通知不关机（用户花钱跑的不该自动停）
                    notify(
                        f"[traffic] 💸 {acc.name}/{st.instance_id} ({st.instance_type} ${hourly:.4f}/h) "
                        f"出站达 {pct:.1f}% 但是付费实例，不会自动关机。请手动处理或降低流量。"
                    )
                    audit(db, action="auto_stop_skipped_paid", target=st.instance_id,
                          detail={"account_id": acc.id, "bytes_out": out, "limit_gb": limit_gb,
                                  "instance_type": st.instance_type, "hourly_usd": hourly})
                    continue

                # 免费实例 → 自动停
                try:
                    provider = make_provider(acc, db=db)
                    provider.stop_instance(st.instance_id, st.region, st.zone)
                    st.auto_stopped_by_traffic = True
                    st.state = "stopping"
                    db.commit()
                    audit(db, action="auto_stop_by_traffic", target=st.instance_id,
                          detail={"account_id": acc.id, "bytes_out": out,
                                  "limit_gb": limit_gb, "threshold_pct": STOP_THRESHOLD_PCT,
                                  "instance_type": st.instance_type, "hourly_usd": hourly,
                                  "is_free": True})
                    notify(f"[traffic] 🛑 {acc.name}/{st.instance_id} (免费 {st.instance_type}) "
                           f"出站达 {pct:.1f}% 自动关机 ({out/1e9:.2f}/{limit_gb} GB)")
                except Exception as e:
                    audit(db, action="auto_stop_by_traffic", target=st.instance_id,
                          detail={"account_id": acc.id, "bytes_out": out}, ok=False, error=str(e))
