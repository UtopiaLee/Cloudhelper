"""实时账单推算服务。

每 30 分钟跑一次（与 SSH 采集同频），扫描所有 running 实例，按
当前 hourly_usd × 0.5h 累加到当前账户当前 tick，落 billing_ticks 表。

UI 通过 sum(billing_ticks) 拿"实时本月花费推算值"，对比 BQ 账单导出的真实历史值。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal
from app.models import BillingTick, CloudAccount, InstanceState
from app.services.pricing import get_price

log = logging.getLogger(__name__)

TICK_HOURS = 0.5  # 30 分钟


def tick_all() -> dict:
    """主入口：被调度器每 30 分钟调一次。"""
    summary = {"accounts": 0, "running": 0, "cost_usd": 0.0}
    with SessionLocal() as db:
        accounts = db.scalars(select(CloudAccount).where(CloudAccount.enabled.is_(True))).all()
        for acc in accounts:
            try:
                cost, count, detail = _tick_account(db, acc)
                if count > 0 or cost > 0:
                    db.add(BillingTick(
                        account_id=acc.id, cost_usd=cost,
                        running_count=count, detail=detail,
                    ))
                summary["accounts"] += 1
                summary["running"] += count
                summary["cost_usd"] += cost
            except Exception as e:
                log.warning("billing tick failed for %s: %s", acc.name, e)
        db.commit()
    return summary


def _tick_account(db: Session, acc: CloudAccount) -> tuple[float, int, dict]:
    """计算这个账号当前 tick 的成本。"""
    states = db.scalars(select(InstanceState).where(
        InstanceState.account_id == acc.id,
        InstanceState.state == "running",
    )).all()
    total = 0.0
    detail: dict[str, float] = {}
    for st in states:
        if not st.instance_type:
            continue
        try:
            hourly = get_price(db, acc.provider, st.region, st.instance_type, account=acc)
        except Exception:
            hourly = 0.0
        c = round(hourly * TICK_HOURS, 6)
        if c > 0:
            detail[st.instance_id] = c
            total += c
    return round(total, 6), len(states), detail


def cleanup_old_ticks(keep_days: int = 90) -> None:
    """删 90 天前的旧 tick，避免表无限膨胀。"""
    from datetime import timedelta
    cutoff = datetime.utcnow() - timedelta(days=keep_days)
    with SessionLocal() as db:
        from sqlalchemy import delete
        db.execute(delete(BillingTick).where(BillingTick.at < cutoff))
        db.commit()
