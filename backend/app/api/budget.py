from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import CloudAccount, InstanceState
from app.schemas import BudgetSummary
from app.services.pricing import get_price

router = APIRouter()


def _today() -> date:
    return datetime.now(timezone.utc).date()


@router.get("/realtime")
def realtime_billing(account_id: int, db: Session = Depends(get_db)) -> dict:
    """实时账单推算：基于 30 分钟 tick 累加（无云商账单延迟）。

    返回：
      - month_to_date_usd: 本月累计推算
      - last_tick_at: 最近一次 tick 时间
      - last_tick_cost: 最近一次 tick 成本
      - hourly_avg_usd: 按最近 24h tick 平均推算每小时
      - tick_count: 本月已发生 tick 数
    """
    from datetime import timezone
    from sqlalchemy import func
    from app.models import BillingTick

    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")

    now = datetime.now(timezone.utc)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)

    # 本月累加
    total = db.scalar(select(func.coalesce(func.sum(BillingTick.cost_usd), 0.0)).where(
        BillingTick.account_id == account_id,
        BillingTick.at >= month_start,
    )) or 0.0

    # 本月 tick 数
    tick_count = db.scalar(select(func.count(BillingTick.id)).where(
        BillingTick.account_id == account_id,
        BillingTick.at >= month_start,
    )) or 0

    # 最近一次 tick
    last = db.scalar(select(BillingTick).where(
        BillingTick.account_id == account_id,
    ).order_by(BillingTick.id.desc()))

    # 最近 24h 平均每小时
    from datetime import timedelta
    last_24h = now.replace(tzinfo=None) - timedelta(hours=24)
    last_24h_total = db.scalar(select(func.coalesce(func.sum(BillingTick.cost_usd), 0.0)).where(
        BillingTick.account_id == account_id,
        BillingTick.at >= last_24h,
    )) or 0.0
    hourly_avg = round(float(last_24h_total) / 24, 6) if last_24h_total else 0.0

    return {
        "account_id": account_id,
        "month_to_date_usd": round(float(total), 4),
        "tick_count": int(tick_count),
        "last_tick_at": last.at.isoformat() + "Z" if last and last.at else None,
        "last_tick_cost": float(last.cost_usd) if last else 0.0,
        "last_tick_running": last.running_count if last else 0,
        "hourly_avg_usd": hourly_avg,
    }


@router.post("/tick-now")
def trigger_tick(account_id: int, db: Session = Depends(get_db)) -> dict:
    """立即跑一次 tick（调试 / 想看到立即变化时用）。"""
    from app.services.billing_tick import _tick_account
    from app.models import BillingTick
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    cost, count, detail = _tick_account(db, acc)
    if count > 0 or cost > 0:
        db.add(BillingTick(account_id=acc.id, cost_usd=cost, running_count=count, detail=detail))
        db.commit()
    return {"cost_usd": cost, "running": count, "detail": detail}


@router.get("", response_model=BudgetSummary)
def budget_summary(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")

    states = db.scalars(select(InstanceState).where(InstanceState.account_id == account_id)).all()
    instances_out = []
    daily_burn = 0.0
    for st in states:
        hourly = get_price(db, acc.provider, st.region, st.instance_type, account=acc) if st.instance_type else 0.0
        running = st.state == "running"
        per_day = hourly * 24 if running else 0.0
        if running:
            daily_burn += per_day
        instances_out.append({
            "id": st.instance_id, "name": st.name or st.instance_id,
            "instance_type": st.instance_type, "region": st.region,
            "state": st.state,
            "hourly_usd": round(hourly, 6),
            "daily_usd": round(per_day, 4),
        })

    remaining = max(0.0, acc.credit_total_usd - acc.credit_used_usd)
    days_to_expiry: Optional[int] = None
    if acc.credit_expires_at:
        days_to_expiry = (acc.credit_expires_at - _today()).days

    days_until_runs_out: Optional[float] = None
    will_outlast: Optional[bool] = None
    if daily_burn > 0:
        days_until_runs_out = round(remaining / daily_burn, 1)
        if days_to_expiry is not None:
            will_outlast = days_until_runs_out >= days_to_expiry
    else:
        if remaining > 0 and days_to_expiry is not None:
            will_outlast = True

    return BudgetSummary(
        account_id=acc.id, account_name=acc.name, provider=acc.provider,
        credit_total_usd=acc.credit_total_usd,
        credit_used_usd=acc.credit_used_usd,
        credit_remaining_usd=remaining,
        credit_expires_at=acc.credit_expires_at,
        days_to_expiry=days_to_expiry,
        daily_burn_usd=round(daily_burn, 4),
        monthly_burn_usd=round(daily_burn * 30, 4),
        days_until_credit_runs_out=days_until_runs_out,
        will_outlast_expiry=will_outlast,
        instances=instances_out,
    )


@router.get("/free-tier")
def free_tier_usage(account_id: int, db: Session = Depends(get_db)) -> dict:
    """从云商 API 拉 Free Tier 真实用量。

    AWS：调 freetier:GetFreeTierUsage（免费 API）
    其他云：暂不支持
    """
    from app.providers import make_provider
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")

    provider = make_provider(acc, db=db)
    try:
        items = provider.list_free_tier_usage()
    except NotImplementedError:
        return {"supported": False, "items": [],
                "message": f"{acc.provider.upper()} 没有免费的 Free Tier 用量 API"}
    except Exception as e:
        msg = str(e)
        if "AccessDeniedException" in msg or "UnauthorizedOperation" in msg:
            raise HTTPException(401, "AWS IAM 缺少 freetier:GetFreeTierUsage 权限")
        raise HTTPException(400, f"获取失败: {msg}")

    return {
        "supported": True,
        "items": [
            {
                "service": i.service,
                "description": i.description,
                "actual_usage": i.actual_usage,
                "forecasted_usage": i.forecasted_usage,
                "limit": i.limit,
                "unit": i.unit,
                "actual_pct": i.actual_pct,
                "forecasted_pct": i.forecasted_pct,
            }
            for i in items
        ],
    }
