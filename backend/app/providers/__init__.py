"""根据账户类型获取对应 Provider。无主从关系，直接用账号自身凭据。

Provider 会被缓存复用：Oracle/Azure 在 __init__ 里构建重量级 SDK 客户端和
Azure 凭据（持有连接池 / 后台刷新 token），100+ 账号每隔几分钟轮询一次，
每次都新建会疯狂 churn socket。这里按 (账号, 凭据, region) 缓存实例，带 TTL
和数量上限，过期 / 淘汰时调用 close() 释放底层连接。

凭据轮换会改变 credentials_enc 密文，从而改变缓存键，自动重建——无需手动失效。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Optional

from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.models import CloudAccount
from app.providers.aws import AWSProvider
from app.providers.azure import AzureProvider
from app.providers.base import CloudProvider
from app.providers.gcp import GCPProvider
from app.providers.oracle import OracleProvider

# 缓存上限与存活时间。账号数量级 100+，留出余量；TTL 控制凭据/配置漂移的最大滞后。
_CACHE_MAX = 256
_CACHE_TTL = 300.0  # 秒

_cache: "OrderedDict[tuple, tuple[CloudProvider, float]]" = OrderedDict()
_cache_lock = threading.Lock()


def _build_provider(account: CloudAccount, creds: dict) -> CloudProvider:
    if account.provider == "aws":
        return AWSProvider(creds, default_region=account.default_region or "us-east-1")
    if account.provider == "gcp":
        return GCPProvider(creds, default_region=account.default_region or "us-central1")
    if account.provider == "oracle":
        return OracleProvider(creds, default_region=account.default_region or creds.get("region") or "ap-singapore-1")
    if account.provider == "azure":
        return AzureProvider(creds, default_region=account.default_region or creds.get("region") or "eastus")
    raise ValueError(f"未知 provider: {account.provider}")


def _close_quietly(provider: CloudProvider) -> None:
    close = getattr(provider, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


def _cache_key(account: CloudAccount) -> tuple:
    # 用密文哈希而非明文，避免把凭据放进进程内长期可见的 key；密文变即视为换凭据。
    cred_sig = hashlib.sha256((account.credentials_enc or "").encode("utf-8")).hexdigest()
    return (account.id, account.provider, account.default_region or "", cred_sig)


def make_provider(account: CloudAccount, db: Optional[Session] = None) -> CloudProvider:
    key = _cache_key(account)
    now = time.monotonic()

    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            provider, created_at = hit
            if now - created_at <= _CACHE_TTL:
                _cache.move_to_end(key)
                return provider
            # 过期：从缓存移除并在锁外关闭。
            del _cache[key]
            stale = provider
        else:
            stale = None

    if stale is not None:
        _close_quietly(stale)

    creds = json.loads(get_crypto().decrypt(account.credentials_enc))
    provider = _build_provider(account, creds)

    evicted: list[CloudProvider] = []
    with _cache_lock:
        # 并发下可能已有别的线程建好同 key 的实例；用已在缓存里的，丢弃自己刚建的。
        existing = _cache.get(key)
        if existing is not None and now - existing[1] <= _CACHE_TTL:
            _cache.move_to_end(key)
            evicted.append(provider)
            provider = existing[0]
        else:
            _cache[key] = (provider, now)
            _cache.move_to_end(key)
            while len(_cache) > _CACHE_MAX:
                _, (old_provider, _) = _cache.popitem(last=False)
                evicted.append(old_provider)

    for ev in evicted:
        _close_quietly(ev)
    return provider
