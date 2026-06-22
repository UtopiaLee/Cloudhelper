"""共享的 SSH 连接工具：兼容性强的 paramiko 包装。

针对 Ubuntu 22.04 / OpenSSH 9.x：
- client.connect() 默认会尝试 keyboard-interactive 然后失败抛 "No existing session"
- 改用 Transport + auth_password 显式控制顺序
- banner/auth timeout 加长，给 cloud-init 还在 restart sshd 留余地
"""

from __future__ import annotations

import logging
import socket
from typing import Optional, Tuple

import paramiko

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15
BANNER_TIMEOUT = 30
AUTH_TIMEOUT = 20


def connect_ssh(host: str, port: int, user: str,
                password: Optional[str] = None,
                pkey: Optional[paramiko.PKey] = None) -> Tuple[paramiko.SSHClient, str]:
    """统一 SSH 连接。返回 (client, method)。method ∈ {"password", "key"}。

    认证顺序：password → pkey。任何 SSH 协议层异常都会被包装为可读消息。
    """
    if not password and pkey is None:
        raise paramiko.AuthenticationException("无可用凭据：未设密码且未配 SSH 密钥")

    last_password_err: Optional[Exception] = None

    if password:
        try:
            client = _connect_with_password(host, port, user, password)
            return client, "password"
        except paramiko.AuthenticationException as e:
            last_password_err = e
            log.info("password auth rejected for %s@%s: %s", user, host, e)
        except (paramiko.SSHException, EOFError, OSError) as e:
            last_password_err = e
            log.info("password ssh handshake failed for %s@%s: %s", user, host, e)

    if pkey is not None:
        try:
            client = _connect_with_pkey(host, port, user, pkey)
            return client, "key"
        except paramiko.AuthenticationException as e:
            msg = f"密钥认证失败：{e}"
            if last_password_err:
                msg += f"（密码也失败：{last_password_err}）"
            raise paramiko.AuthenticationException(msg)
        except (paramiko.SSHException, EOFError, OSError) as e:
            raise paramiko.SSHException(
                f"SSH 握手失败：{e}\n"
                f"常见原因：sshd 还没起完 / 防火墙拦截 / fail2ban 暂禁 IP / 算法不兼容"
            )

    if last_password_err and isinstance(last_password_err, paramiko.AuthenticationException):
        raise paramiko.AuthenticationException(
            f"密码错误（没有密钥可回退）：{last_password_err}"
        )
    if last_password_err:
        raise paramiko.SSHException(
            f"SSH 连接失败：{last_password_err}\n"
            f"建议等 1 分钟后重试，或用 🩺 诊断查具体环节"
        )
    raise paramiko.AuthenticationException("无可用凭据")


def _connect_with_password(host: str, port: int, user: str, password: str) -> paramiko.SSHClient:
    """密码认证。

    优先用 SSHClient.connect（更兼容）。如果遇到 "No existing session" 等
    OpenSSH 9.x 接连 auth method 失败的协商问题，再 fallback 到底层 Transport。
    """
    # 方案 A：SSHClient.connect（标准方式，能 handle 大多数情况）
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=host, port=port, username=user, password=password,
            timeout=CONNECT_TIMEOUT, banner_timeout=BANNER_TIMEOUT, auth_timeout=AUTH_TIMEOUT,
            look_for_keys=False, allow_agent=False,
            # paramiko 3.x：不传 disabled_algorithms 让它自己谈
        )
        return client
    except paramiko.AuthenticationException:
        # 真的是密码错，不用回退
        try: client.close()
        except Exception: pass
        raise
    except (paramiko.SSHException, EOFError, OSError) as e:
        # 可能是 "No existing session" 这类，尝试 fallback
        try: client.close()
        except Exception: pass
        log.info("SSHClient.connect 失败，尝试 Transport fallback: %s", e)

    # 方案 B：底层 Transport 直接做（绕过 client.connect 的 auth-method 顺序）
    sock = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT)
    transport = paramiko.Transport(sock)
    transport.banner_timeout = BANNER_TIMEOUT
    transport.auth_timeout = AUTH_TIMEOUT
    try:
        transport.start_client(timeout=BANNER_TIMEOUT)
        # 关键：start_client 后必须等到 session 真正可用，否则 auth_password 报 No existing session
        # paramiko 实际上是同步的，但有时需要再走一次握手确认
        if not transport.is_active():
            raise paramiko.SSHException("transport 未激活")
        transport.auth_password(username=user, password=password)
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("密码认证未通过（transport 未标记为 authenticated）")
    except paramiko.AuthenticationException:
        try: transport.close()
        except Exception: pass
        raise
    except Exception as e:
        try: transport.close()
        except Exception: pass
        raise paramiko.SSHException(f"密码认证 fallback 也失败：{e}")

    client = paramiko.SSHClient()
    client._transport = transport
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    return client


def _connect_with_pkey(host: str, port: int, user: str, pkey: paramiko.PKey) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, port=port, username=user, pkey=pkey,
        timeout=CONNECT_TIMEOUT, banner_timeout=BANNER_TIMEOUT, auth_timeout=AUTH_TIMEOUT,
        look_for_keys=False, allow_agent=False,
    )
    return client
