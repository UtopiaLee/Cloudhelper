"""共享的 SSH 连接工具：兼容性强的 paramiko 包装。

针对 Ubuntu 22.04 / OpenSSH 9.x：
- client.connect() 默认会尝试 keyboard-interactive 然后失败抛 "No existing session"
- 改用 Transport + auth_password 显式控制顺序
- banner/auth timeout 加长，给 cloud-init 还在 restart sshd 留余地
"""

from __future__ import annotations

import logging
import os
import socket
import threading
from pathlib import Path
from typing import Optional, Tuple

import paramiko

log = logging.getLogger(__name__)

CONNECT_TIMEOUT = 15
BANNER_TIMEOUT = 30
AUTH_TIMEOUT = 20

KNOWN_HOSTS_FILENAME = "known_hosts"

# 串行化 known_hosts 的读改写，避免并发连接相互覆盖（webshell/采集/诊断可能同时连）。
_known_hosts_lock = threading.Lock()


def _known_hosts_path() -> Path:
    """TOFU 主机密钥存储路径（data_dir/known_hosts）。

    延迟 import settings，避免在模块加载期触发配置/循环依赖。
    """
    from app.core.config import get_settings

    return get_settings().data_dir / KNOWN_HOSTS_FILENAME


def _load_known_hosts(client: paramiko.SSHClient) -> None:
    """把已记录的主机密钥载入 client，使 SSHClient.connect 在密钥变更时
    自动抛 BadHostKeyException（已知主机 + 密钥不符 → 拒绝）。"""
    path = _known_hosts_path()
    if path.exists():
        try:
            client.load_host_keys(str(path))
        except (OSError, paramiko.SSHException) as e:
            log.warning("加载 known_hosts 失败（按未知主机处理）：%s", e)


def _hostkey_entry_name(host: str, port: int) -> str:
    """known_hosts 中的主机名形式：非标准端口用 [host]:port。"""
    return host if port == 22 else f"[{host}]:{port}"


def _lookup_known_key(host: str, port: int, keytype: str) -> Optional[paramiko.PKey]:
    """返回 known_hosts 中该 host 对应 keytype 的已存密钥；无则 None。"""
    hk = paramiko.HostKeys()
    path = _known_hosts_path()
    if path.exists():
        try:
            hk.load(str(path))
        except (OSError, paramiko.SSHException) as e:
            log.warning("读取 known_hosts 失败：%s", e)
            return None
    entry = hk.lookup(_hostkey_entry_name(host, port))
    if not entry:
        return None
    # SubDict 以 keytype 字符串为键。
    return entry.get(keytype)


def _persist_host_key(host: str, port: int, key: paramiko.PKey) -> None:
    """首见即记录（TOFU）。带文件锁防并发覆盖，权限收紧到 0600。"""
    name = _hostkey_entry_name(host, port)
    path = _known_hosts_path()
    with _known_hosts_lock:
        hk = paramiko.HostKeys()
        if path.exists():
            try:
                hk.load(str(path))
            except (OSError, paramiko.SSHException):
                hk = paramiko.HostKeys()
        # 二次确认：拿锁后别人可能已写入。
        existing = hk.lookup(name)
        if existing and existing.get(key.get_name()) is not None:
            return
        hk.add(name, key.get_name(), key)
        path.parent.mkdir(parents=True, exist_ok=True)
        hk.save(str(path))
        try:
            os.chmod(str(path), 0o600)
        except OSError:
            pass
    log.info("已记录新主机密钥 (TOFU): %s %s", name, key.get_name())


def _verify_or_pin_transport_key(transport: paramiko.Transport, host: str, port: int) -> None:
    """Transport fallback 专用：手动 TOFU 校验。

    - 已知且匹配 → 放行
    - 已知但不符 / 已知主机却没有该 keytype 的 pin → 抛 BadHostKeyException（绝不发送密码）
    - 完全未知 → 记录后放行（首见）
    """
    remote_key = transport.get_remote_server_key()
    keytype = remote_key.get_name()
    known = _lookup_known_key(host, port, keytype)
    if known is not None:
        if known.asbytes() != remote_key.asbytes():
            raise paramiko.BadHostKeyException(host, remote_key, known)
        return
    # 主机此前从未记录过 → 首见，TOFU 记录。
    # 但若主机已知（存在其它 keytype 的 pin）却拿不到当前 keytype，视为不可信，拒绝。
    if _host_is_known(host, port):
        raise paramiko.BadHostKeyException(host, remote_key, remote_key)
    _persist_host_key(host, port, remote_key)


def _host_is_known(host: str, port: int) -> bool:
    hk = paramiko.HostKeys()
    path = _known_hosts_path()
    if not path.exists():
        return False
    try:
        hk.load(str(path))
    except (OSError, paramiko.SSHException):
        return False
    return hk.lookup(_hostkey_entry_name(host, port)) is not None


class _TOFUAddPolicy(paramiko.MissingHostKeyPolicy):
    """首见主机：记录密钥并放行；密钥变更由 paramiko 在调用本策略前
    以 BadHostKeyException 拦截（因为已 load_host_keys）。"""

    def missing_host_key(self, client, hostname, key):
        # hostname 由 paramiko 传入，可能已是 [host]:port 形式；解析回 host/port。
        host, port = hostname, 22
        if hostname.startswith("[") and "]:" in hostname:
            try:
                host, port_s = hostname[1:].split("]:", 1)
                port = int(port_s)
            except ValueError:
                host, port = hostname, 22
        _persist_host_key(host, port, key)


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
        except paramiko.BadHostKeyException as e:
            # 主机密钥变更：可能是 MITM / IP 被接管。绝不静默回退，立即报错。
            raise paramiko.BadHostKeyException(e.hostname, e.key, e.expected_key)
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
        except paramiko.BadHostKeyException as e:
            raise paramiko.BadHostKeyException(e.hostname, e.key, e.expected_key)
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
        _load_known_hosts(client)
        client.set_missing_host_key_policy(_TOFUAddPolicy())
        client.connect(
            hostname=host, port=port, username=user, password=password,
            timeout=CONNECT_TIMEOUT, banner_timeout=BANNER_TIMEOUT, auth_timeout=AUTH_TIMEOUT,
            look_for_keys=False, allow_agent=False,
            # paramiko 3.x：不传 disabled_algorithms 让它自己谈
        )
        return client
    except paramiko.BadHostKeyException:
        # 主机密钥变更，不做 Transport 回退（回退也会再次检测到并拒绝）。
        try: client.close()
        except Exception: pass
        raise
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
        # TOFU 主机密钥校验：必须在发送密码之前，密钥变更绝不泄露明文密码。
        _verify_or_pin_transport_key(transport, host, port)
        transport.auth_password(username=user, password=password)
        if not transport.is_authenticated():
            raise paramiko.AuthenticationException("密码认证未通过（transport 未标记为 authenticated）")
    except paramiko.BadHostKeyException:
        try: transport.close()
        except Exception: pass
        raise
    except paramiko.AuthenticationException:
        try: transport.close()
        except Exception: pass
        raise
    except Exception as e:
        try: transport.close()
        except Exception: pass
        raise paramiko.SSHException(f"密码认证 fallback 也失败：{e}")

    client = paramiko.SSHClient()
    _load_known_hosts(client)
    client._transport = transport
    return client


def _connect_with_pkey(host: str, port: int, user: str, pkey: paramiko.PKey) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    _load_known_hosts(client)
    client.set_missing_host_key_policy(_TOFUAddPolicy())
    client.connect(
        hostname=host, port=port, username=user, pkey=pkey,
        timeout=CONNECT_TIMEOUT, banner_timeout=BANNER_TIMEOUT, auth_timeout=AUTH_TIMEOUT,
        look_for_keys=False, allow_agent=False,
    )
    return client
