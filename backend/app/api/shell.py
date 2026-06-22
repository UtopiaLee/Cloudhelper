"""WebSocket SSH 终端：浏览器 ↔ 后端 ↔ paramiko invoke_shell。

协议：JSON 消息（避免分隔符冲突）
  前端 → 后端：
    {"type":"input","data":"..."}     键盘输入（任意 utf-8 字符串）
    {"type":"resize","cols":80,"rows":24}
    {"type":"password","data":"xxx"}  连接前一次性密码

  后端 → 前端：
    {"type":"data","data":"..."}      stdout / stderr
    {"type":"status","data":"connected via password"}
    {"type":"error","data":"xxx"}     致命错误并关闭

认证：
  1. 优先 实例存的密码 (ssh_password_enc)
  2. 失败 / 没存 → 一次性密码（前端发的 password 消息）
  3. 失败 / 没发 → 默认 SSH 密钥
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from typing import Optional

import paramiko
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.crypto import get_crypto
from app.core.db import SessionLocal
from app.models import CloudAccount, InstanceState, SSHKey
from app.services.aws_instance_connect import is_eic_available, push_ephemeral_key
from app.services.ssh_common import connect_ssh

log = logging.getLogger(__name__)

router = APIRouter()


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


def _try_connect(client: paramiko.SSHClient, *, host: str, port: int, user: str,
                 password: Optional[str], pkey: Optional[paramiko.PKey]) -> str:
    """废弃：保留签名只是为了少改下游；实际改用 connect_ssh 在 ws_shell 内调用。"""
    raise NotImplementedError


async def _send_json(ws: WebSocket, obj: dict) -> None:
    await ws.send_text(json.dumps(obj, ensure_ascii=False))


@router.websocket("/ws/instances/{account_id}/{instance_id}/shell")
async def ws_shell(ws: WebSocket, account_id: int, instance_id: str, token: str = "", knock: str = ""):
    from app.core.auth import ws_check_token
    from app.core.knock import check_knock
    if not check_knock(knock):
        await ws.close(code=1008, reason="not found")
        return
    if not ws_check_token(token):
        await ws.close(code=1008, reason="unauthorized")
        return
    await ws.accept()

    with SessionLocal() as db:
        st = db.scalar(select(InstanceState).where(
            InstanceState.account_id == account_id,
            InstanceState.instance_id == instance_id,
        ))
        acc = db.get(CloudAccount, account_id)
        if not st or not acc:
            await _send_json(ws, {"type": "error", "data": "实例不存在"})
            await ws.close()
            return
        if not st.public_ip:
            await _send_json(ws, {"type": "error", "data": "实例没有公网 IP"})
            await ws.close()
            return
        host = st.public_ip
        port = st.ssh_port or 22
        user = st.ssh_user or "root"
        az = st.zone or ""
        provider = acc.provider
        stored_password = ""
        if st.ssh_password_enc:
            try:
                stored_password = get_crypto().decrypt(st.ssh_password_enc)
            except Exception:
                stored_password = ""

    runtime_password: Optional[str] = None
    cols = 80
    rows = 24
    # 等前端的初始消息（password / resize），最多 2 秒
    try:
        for _ in range(3):
            raw = await asyncio.wait_for(ws.receive_text(), timeout=2.0)
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("type")
            if t == "password":
                runtime_password = msg.get("data") or None
            elif t == "resize":
                cols = int(msg.get("cols") or 80)
                rows = int(msg.get("rows") or 24)
            else:
                # 已经是 input，停止预收
                break
    except (asyncio.TimeoutError, WebSocketDisconnect):
        pass

    password = stored_password or runtime_password
    pkey = _load_default_key()

    # AWS 实例：优先尝试 EC2 Instance Connect（推临时公钥，60 秒窗口）
    eic_pkey: Optional[paramiko.PKey] = None
    eic_attempted = False
    if provider == "aws" and az:
        try:
            # 在线程里调，避免阻塞 event loop
            with SessionLocal() as db2:
                acc2 = db2.get(CloudAccount, account_id)
                eic_pkey = await asyncio.to_thread(
                    push_ephemeral_key, acc2, instance_id, acc2.default_region or "us-east-1", az, user,
                )
                eic_attempted = True
                log.info("EIC key pushed for %s, will try first", instance_id)
        except Exception as e:
            log.info("EIC 推送失败（继续 fallback 到密码/密钥）：%s", e)

    # 认证顺序：EIC 临时密钥 → 用户存的密码 → 系统默认 SSH 密钥
    try:
        if eic_pkey is not None:
            try:
                client, method = await asyncio.to_thread(
                    connect_ssh, host, port, user, None, eic_pkey,
                )
                method = "ec2-instance-connect"
            except Exception as e:
                log.info("EIC ssh 失败，回退到密码/密钥：%s", e)
                client, method = await asyncio.to_thread(
                    connect_ssh, host, port, user, password, pkey,
                )
        else:
            client, method = await asyncio.to_thread(
                connect_ssh, host, port, user, password, pkey,
            )
    except Exception as e:
        await _send_json(ws, {"type": "error", "data": f"SSH 连接失败：{e}"})
        await ws.close()
        return

    chan = client.invoke_shell(term="xterm-256color", width=cols, height=rows)
    await _send_json(ws, {"type": "status", "data": f"connected via {method}"})

    loop = asyncio.get_event_loop()
    closed = False

    def _send_all(data: bytes) -> None:
        view = memoryview(data)
        while view:
            try:
                n = chan.send(view)
            except OSError:
                return
            if n <= 0:
                return
            view = view[n:]

    async def pump_ssh_to_ws() -> None:
        nonlocal closed
        try:
            while not closed:
                def _recv() -> bytes:
                    chan.settimeout(0.05)
                    try:
                        return chan.recv(65536)
                    except Exception:
                        return b""
                data = await loop.run_in_executor(None, _recv)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    await _send_json(ws, {"type": "data", "data": text})
                if chan.exit_status_ready() and not chan.recv_ready():
                    break
        except Exception as e:
            log.debug("pump_ssh_to_ws stopped: %s", e)
        finally:
            closed = True

    async def pump_ws_to_ssh() -> None:
        nonlocal closed
        try:
            while not closed:
                raw = await ws.receive_text()
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                t = msg.get("type")
                if t == "input":
                    data = msg.get("data") or ""
                    if data:
                        await loop.run_in_executor(None, _send_all, data.encode("utf-8"))
                elif t == "resize":
                    try:
                        chan.resize_pty(width=int(msg.get("cols") or 80),
                                        height=int(msg.get("rows") or 24))
                    except Exception:
                        pass
        except WebSocketDisconnect:
            pass
        except Exception as e:
            log.debug("pump_ws_to_ssh stopped: %s", e)
        finally:
            closed = True

    try:
        await asyncio.gather(pump_ssh_to_ws(), pump_ws_to_ssh())
    finally:
        try: chan.close()
        except Exception: pass
        try: client.close()
        except Exception: pass
        try: await ws.close()
        except Exception: pass
