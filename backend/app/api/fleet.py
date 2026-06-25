"""跨账号视图：dashboard 聚合 + fleet list + 批量操作。"""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import SessionLocal, get_db
from app.models import CloudAccount, InstanceState, MonthlyTraffic
from app.providers import make_provider
from app.schemas import BulkAction, DashboardSummary, InstanceOut
from app.services.audit import audit

router = APIRouter()


def _ym() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _wait_until_stopped(provider, inst_id: str, region: str, zone: str,
                        timeout: float = 180.0, interval: float = 5.0) -> None:
    """轮询直到实例进入 stopped 终态。各 provider 的 get_instance 都把停止终态归一为 "stopped"
    （AWS 返回原始 EC2 state，终态同样是 "stopped"）。用于 restart：先停稳再启动，避免
    stop->start 竞争触发 IncorrectInstanceState。超时则抛错，由调用方记为该项失败。"""
    deadline = time.monotonic() + timeout
    while True:
        state = provider.get_instance(inst_id, region, zone).state
        if state == "stopped":
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"轮询 {timeout:.0f}s 后实例仍未停稳（当前状态: {state}）")
        time.sleep(interval)


@router.get("/dashboard", response_model=DashboardSummary)
def dashboard(db: Session = Depends(get_db)):
    accounts = db.scalars(select(CloudAccount).where(CloudAccount.enabled.is_(True))).all()
    by_provider: dict[str, int] = {}
    for a in accounts:
        by_provider[a.provider] = by_provider.get(a.provider, 0) + 1

    states = db.scalars(select(InstanceState)).all()
    running = sum(1 for s in states if s.state == "running")
    stopped = sum(1 for s in states if s.state.startswith("stop") or s.state == "terminated")

    ym = _ym()
    mt_rows = db.scalars(select(MonthlyTraffic).where(MonthlyTraffic.year_month == ym)).all()
    total_bytes = sum(m.bytes_in + m.bytes_out for m in mt_rows)
    total_gb = total_bytes / (1024 ** 3) if total_bytes else 0.0

    # 计算超限实例数（任意实例本月用量 ≥ 限额）
    state_map = {(s.account_id, s.instance_id): s for s in states}
    acc_map = {a.id: a for a in accounts}
    over = 0
    for m in mt_rows:
        st = state_map.get((m.account_id, m.instance_id))
        acc = acc_map.get(m.account_id)
        if not st or not acc:
            continue
        limit_gb = st.traffic_limit_gb if st.traffic_limit_gb > 0 else acc.monthly_traffic_gb
        if limit_gb <= 0:
            continue
        used = (m.bytes_in + m.bytes_out) / (1024 ** 3)
        if used >= limit_gb:
            over += 1

    last = db.scalar(select(func.max(MonthlyTraffic.last_sampled_at)).where(
        MonthlyTraffic.year_month == ym
    ))

    return DashboardSummary(
        accounts_total=len(accounts),
        accounts_by_provider=by_provider,
        instances_total=len(states),
        instances_running=running,
        instances_stopped=stopped,
        monthly_traffic_gb_total=total_gb,
        over_limit_count=over,
        last_collected_at=last,
    )


@router.get("/instances", response_model=list[InstanceOut])
def fleet_instances(
    refresh: bool = False,
    group: str | None = None,
    provider: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(CloudAccount).where(CloudAccount.enabled.is_(True))
    if group:
        q = q.where(CloudAccount.group_tag == group)
    if provider:
        q = q.where(CloudAccount.provider == provider)
    accounts = db.scalars(q).all()
    if not accounts:
        return []

    if refresh:
        from app.api.instances import _sync_account_instances

        def task(acc_id: int):
            with SessionLocal() as s:
                a = s.get(CloudAccount, acc_id)
                if a:
                    try:
                        _sync_account_instances(s, a)
                    except Exception as e:
                        audit(s, action="fleet.sync", target=str(acc_id), ok=False, error=str(e))

        with ThreadPoolExecutor(max_workers=8) as ex:
            futures = [ex.submit(task, a.id) for a in accounts]
            for f in as_completed(futures):
                f.result()

    ym = _ym()
    states = db.scalars(select(InstanceState).where(
        InstanceState.account_id.in_([a.id for a in accounts]),
        InstanceState.state.not_in(("terminated", "shutting-down")),
    )).all()
    mt_map = {(m.account_id, m.instance_id): m for m in db.scalars(select(MonthlyTraffic).where(
        MonthlyTraffic.account_id.in_([a.id for a in accounts]),
        MonthlyTraffic.year_month == ym,
    )).all()}
    acc_map = {a.id: a for a in accounts}

    out = []
    for s in states:
        acc = acc_map.get(s.account_id)
        if not acc:
            continue
        from app.api.instances import _state_to_out
        out.append(_state_to_out(s, mt_map.get((s.account_id, s.instance_id)), acc, db))
    return out


@router.post("/bulk")
def bulk_action(payload: BulkAction, db: Session = Depends(get_db)):
    def task(target: dict):
        try:
            acc_id = int(target["account_id"])
            inst_id = target["instance_id"]
        except (KeyError, TypeError, ValueError):
            return {"target": target, "ok": False, "error": "target 缺少/非法 account_id 或 instance_id"}
        with SessionLocal() as s:
            acc = s.get(CloudAccount, acc_id)
            if not acc:
                return {"target": target, "ok": False, "error": "account not found"}
            region = target.get("region", "")
            zone = target.get("zone", "")
            try:
                if payload.action == "set-limit":
                    if payload.traffic_limit_gb is None:
                        return {"target": target, "ok": False, "error": "缺 traffic_limit_gb"}
                    st = s.scalar(select(InstanceState).where(
                        InstanceState.account_id == acc_id,
                        InstanceState.instance_id == inst_id,
                    ))
                    if not st:
                        return {"target": target, "ok": False, "error": "实例未在缓存中"}
                    st.traffic_limit_gb = float(payload.traffic_limit_gb)
                    s.commit()
                    audit(s, action="bulk.set_limit", target=inst_id,
                          detail={"account_id": acc_id, "limit": payload.traffic_limit_gb})
                    return {"target": target, "ok": True}

                if payload.action == "set-tag":
                    st = s.scalar(select(InstanceState).where(
                        InstanceState.account_id == acc_id,
                        InstanceState.instance_id == inst_id,
                    ))
                    if not st:
                        return {"target": target, "ok": False, "error": "实例未在缓存中"}
                    tags = dict(st.tags or {})
                    if payload.tag_value:
                        tags["group"] = payload.tag_value
                    else:
                        tags.pop("group", None)
                    st.tags = tags
                    s.commit()
                    audit(s, action="bulk.set_tag", target=inst_id,
                          detail={"account_id": acc_id, "tag": payload.tag_value})
                    return {"target": target, "ok": True}

                provider = make_provider(acc, db=s)
                if payload.action == "start":
                    provider.start_instance(inst_id, region, zone)
                elif payload.action == "stop":
                    provider.stop_instance(inst_id, region, zone)
                elif payload.action == "restart":
                    provider.stop_instance(inst_id, region, zone)
                    _wait_until_stopped(provider, inst_id, region, zone)
                    provider.start_instance(inst_id, region, zone)
                elif payload.action == "terminate":
                    provider.terminate_instance(inst_id, region, zone)
                    st = s.scalar(select(InstanceState).where(
                        InstanceState.account_id == acc_id,
                        InstanceState.instance_id == inst_id,
                    ))
                    if st:
                        s.delete(st)
                        s.commit()
                else:
                    return {"target": target, "ok": False, "error": f"未知动作: {payload.action}"}
                audit(s, action=f"bulk.{payload.action}", target=inst_id, detail={"account_id": acc_id})
                return {"target": target, "ok": True}
            except Exception as e:
                audit(s, action=f"bulk.{payload.action}", target=inst_id,
                      detail={"account_id": acc_id}, ok=False, error=str(e))
                return {"target": target, "ok": False, "error": str(e)}

    with ThreadPoolExecutor(max_workers=10) as ex:
        results = list(ex.map(task, payload.targets))
    ok = sum(1 for r in results if r["ok"])
    return {"total": len(results), "ok": ok, "failed": len(results) - ok, "results": results}


@router.get("/groups")
def list_groups(db: Session = Depends(get_db)):
    rows = db.scalars(select(CloudAccount.group_tag).where(CloudAccount.group_tag != "").distinct()).all()
    return sorted(set(rows))
