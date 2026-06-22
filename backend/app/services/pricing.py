"""实例价格查询。

AWS: pricing.us-east-1.amazonaws.com 的 Pricing API（免费），按 region+instance_type 查 OnDemand。
GCP: 没在线接口免费（Cloud Billing Catalog 需要 SA + 复杂 SKU 匹配），先用 fallback 表。
Oracle/Azure: fallback 表。

价格缓存到 instance_prices 表，7 天有效。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.models import CloudAccount, InstancePrice

log = logging.getLogger(__name__)

CACHE_DAYS = 7

# 兜底价格表（USD/hour, OnDemand, 大致 us-east-1 价格）
# 真实环境会调 Pricing API 覆盖；这里只是 fallback。
FALLBACK_PRICES: dict[str, dict[str, float]] = {
    "aws": {
        "t2.micro": 0.0116, "t2.small": 0.023, "t2.medium": 0.0464,
        "t3.nano": 0.0052, "t3.micro": 0.0104, "t3.small": 0.0208,
        "t3.medium": 0.0416, "t3.large": 0.0832,
        "t4g.nano": 0.0042, "t4g.micro": 0.0084, "t4g.small": 0.0168,
        "t4g.medium": 0.0336, "t4g.large": 0.0672,
    },
    "gcp": {
        "e2-micro": 0.0084,        # 单 region us-central1 标准价；Always Free 区域 = 0
        "e2-small": 0.0168,
        "e2-medium": 0.0335,
        "e2-standard-2": 0.0670,
        "e2-standard-4": 0.1340,
        "n2-standard-2": 0.0971,
    },
    "oracle": {
        "VM.Standard.E2.1.Micro": 0.0,        # Always Free
        "VM.Standard.A1.Flex": 0.0,           # Always Free 范围内
        "VM.Standard.E4.Flex": 0.025,
    },
    "azure": {
        "Standard_B1s": 0.0104,
        "Standard_B2s": 0.0416,
        "Standard_D2s_v3": 0.096,
    },
}

# GCP Always Free 区域，e2-micro 在这里是 $0
GCP_FREE_REGIONS = {"us-west1", "us-central1", "us-east1"}


def _is_free(provider: str, region: str, instance_type: str) -> bool:
    if provider == "gcp" and instance_type == "e2-micro" and region in GCP_FREE_REGIONS:
        return True
    if provider == "oracle" and instance_type in {"VM.Standard.E2.1.Micro", "VM.Standard.A1.Flex"}:
        return True
    return False


def _fallback(provider: str, region: str, instance_type: str) -> float:
    if _is_free(provider, region, instance_type):
        return 0.0
    table = FALLBACK_PRICES.get(provider, {})
    return table.get(instance_type, 0.0)


def get_price(db: Session, provider: str, region: str, instance_type: str,
              account: Optional[CloudAccount] = None) -> float:
    """主入口。优先读缓存，过期再拉，失败用 fallback。"""
    if not instance_type:
        return 0.0

    if _is_free(provider, region, instance_type):
        return 0.0

    cached = db.scalar(select(InstancePrice).where(
        InstancePrice.provider == provider,
        InstancePrice.region == region,
        InstancePrice.instance_type == instance_type,
    ))
    fresh = cached and cached.fetched_at >= datetime.utcnow() - timedelta(days=CACHE_DAYS)
    if fresh:
        return cached.hourly_usd

    price = 0.0
    try:
        if provider == "aws" and account is not None:
            price = _fetch_aws(account, region, instance_type)
        else:
            price = _fallback(provider, region, instance_type)
    except Exception as e:
        log.warning("price fetch failed %s/%s/%s: %s", provider, region, instance_type, e)
        price = _fallback(provider, region, instance_type)

    if cached:
        cached.hourly_usd = price
        cached.fetched_at = datetime.utcnow()
    else:
        db.add(InstancePrice(provider=provider, region=region,
                             instance_type=instance_type, hourly_usd=price))
    db.commit()
    return price


# AWS Pricing API ----------------------------------------------------
_AWS_REGION_NAME = {
    "us-east-1": "US East (N. Virginia)",
    "us-east-2": "US East (Ohio)",
    "us-west-1": "US West (N. California)",
    "us-west-2": "US West (Oregon)",
    "ap-northeast-1": "Asia Pacific (Tokyo)",
    "ap-northeast-2": "Asia Pacific (Seoul)",
    "ap-southeast-1": "Asia Pacific (Singapore)",
    "ap-southeast-2": "Asia Pacific (Sydney)",
    "ap-south-1": "Asia Pacific (Mumbai)",
    "eu-west-1": "EU (Ireland)",
    "eu-central-1": "EU (Frankfurt)",
}


def _fetch_aws(account: CloudAccount, region: str, instance_type: str) -> float:
    import boto3
    creds = json.loads(get_crypto().decrypt(account.credentials_enc))
    if "role_arn" in creds:
        # 子账号情形跳过；让上层用 fallback
        return _fallback("aws", region, instance_type)

    location = _AWS_REGION_NAME.get(region)
    if not location:
        return _fallback("aws", region, instance_type)

    client = boto3.client(
        "pricing",
        region_name="us-east-1",  # Pricing API 仅 us-east-1
        aws_access_key_id=creds.get("access_key_id"),
        aws_secret_access_key=creds.get("secret_access_key"),
    )
    paginator = client.get_paginator("get_products")
    filters = [
        {"Type": "TERM_MATCH", "Field": "ServiceCode", "Value": "AmazonEC2"},
        {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
        {"Type": "TERM_MATCH", "Field": "location", "Value": location},
        {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
        {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
        {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
        {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
    ]
    for page in paginator.paginate(ServiceCode="AmazonEC2", Filters=filters, MaxResults=20):
        for s in page.get("PriceList", []):
            obj = json.loads(s)
            terms = obj.get("terms", {}).get("OnDemand", {})
            for _, term in terms.items():
                for _, dim in term.get("priceDimensions", {}).items():
                    usd = dim.get("pricePerUnit", {}).get("USD")
                    if usd is not None:
                        try:
                            return float(usd)
                        except ValueError:
                            continue
    return _fallback("aws", region, instance_type)
