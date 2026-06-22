"""根据账户类型获取对应 Provider。无主从关系，直接用账号自身凭据。"""

from __future__ import annotations

import json
from typing import Optional

from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.models import CloudAccount
from app.providers.aws import AWSProvider
from app.providers.azure import AzureProvider
from app.providers.base import CloudProvider
from app.providers.gcp import GCPProvider
from app.providers.oracle import OracleProvider


def make_provider(account: CloudAccount, db: Optional[Session] = None) -> CloudProvider:
    creds = json.loads(get_crypto().decrypt(account.credentials_enc))
    if account.provider == "aws":
        return AWSProvider(creds, default_region=account.default_region or "us-east-1")
    if account.provider == "gcp":
        return GCPProvider(creds, default_region=account.default_region or "us-central1")
    if account.provider == "oracle":
        return OracleProvider(creds, default_region=account.default_region or creds.get("region") or "ap-singapore-1")
    if account.provider == "azure":
        return AzureProvider(creds, default_region=account.default_region or creds.get("region") or "eastus")
    raise ValueError(f"未知 provider: {account.provider}")
