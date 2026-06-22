"""APScheduler 调用入口（顶层函数，便于 jobstore 序列化引用）。"""

from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from sqlalchemy import select

from app.core.db import SessionLocal
from app.models import AuditLog, CloudAccount, InstanceState, MonthlyTraffic, Schedule
from app.providers import make_provider
from app.services.audit import audit, notify

log = logging.getLogger(__name__)
EXPIRY_REMIND_DAYS = {30, 14, 7, 3, 1, 0}


def _parse_note_expiry(note: str) -> Optional[date]:
    if not note:
        return None
    m = re.search(r"(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", note)
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def _already_reminded(db, account_id: int, day_left: int) -> bool:
    action = f"account.expiry_remind.{day_left}d"
    row = db.scalar(select(AuditLog.id).where(
        AuditLog.action == action,
        AuditLog.target == str(account_id),
    ).limit(1))
    return row is not None


def remind_account_expiry() -> dict:
    today = date.today()
    summary = {"checked": 0, "notified": 0}
    with SessionLocal() as db:
        accounts = db.scalars(select(CloudAccount).where(CloudAccount.enabled.is_(True))).all()
        for acc in accounts:
            summary["checked"] += 1
            expiry = acc.credit_expires_at or _parse_note_expiry(acc.note or "")
            if not expiry:
                continue
            days_left = (expiry - today).days
            if days_left not in EXPIRY_REMIND_DAYS:
                continue
            if _already_reminded(db, acc.id, days_left):
                continue

            msg = (
                f"[expiry] ⚠️ {acc.name} 免费额度到期日 {expiry.isoformat()}，"
                f"剩余 {max(days_left, 0)} 天。"
            )
            notify(msg, account_id=acc.id, provider=acc.provider, expiry=expiry.isoformat(), days_left=days_left)
            audit(
                db,
                action=f"account.expiry_remind.{days_left}d",
                target=str(acc.id),
                detail={"account": acc.name, "provider": acc.provider, "expiry": expiry.isoformat(), "days_left": days_left},
                ok=True,
            )
            summary["notified"] += 1
    return summary


def run_instance_action(account_id: int, instance_id: str, action: str,
                        schedule_id: Optional[int] = None) -> None:
    with SessionLocal() as db:
        account = db.get(CloudAccount, account_id)
        if not account or not account.enabled:
            log.warning("schedule skipped: account %s missing/disabled", account_id)
            return
        provider = make_provider(account, db=db)
        state = db.scalar(select(InstanceState).where(
            InstanceState.account_id == account_id,
            InstanceState.instance_id == instance_id,
        ))
        region = state.region if state else account.default_region
        zone = state.zone if state else ""
        try:
            if action == "start":
                provider.start_instance(instance_id, region, zone)
            elif action == "stop":
                provider.stop_instance(instance_id, region, zone)
            elif action == "restart":
                provider.stop_instance(instance_id, region, zone)
                provider.start_instance(instance_id, region, zone)
            elif action == "destroy":
                provider.terminate_instance(instance_id, region, zone)
            else:
                raise ValueError(f"unknown action: {action}")
            audit(db, action=f"schedule.{action}", target=instance_id,
                  detail={"account_id": account_id, "schedule_id": schedule_id})
            notify(f"[schedule] {action} {instance_id} 成功")
        except Exception as e:
            audit(db, action=f"schedule.{action}", target=instance_id,
                  detail={"account_id": account_id, "schedule_id": schedule_id},
                  ok=False, error=str(e))
            notify(f"[schedule] {action} {instance_id} 失败: {e}")
            raise
        finally:
            # 单次任务执行完自动标记 enabled=False
            if schedule_id:
                sch = db.get(Schedule, schedule_id)
                if sch and sch.trigger_type == "date":
                    sch.enabled = False
                    db.commit()


def monthly_reset() -> None:
    """每月 1 号：清零月累计计数器（新月新表行），把流量自动停机的实例拉起来。"""
    with SessionLocal() as db:
        rows = db.scalars(select(InstanceState).where(InstanceState.auto_stopped_by_traffic.is_(True))).all()
        for s in rows:
            try:
                acc = db.get(CloudAccount, s.account_id)
                if not acc or not acc.enabled:
                    continue
                provider = make_provider(acc, db=db)
                provider.start_instance(s.instance_id, s.region, s.zone)
                s.auto_stopped_by_traffic = False
                db.commit()
                audit(db, action="monthly_restart", target=s.instance_id,
                      detail={"account_id": s.account_id})
                notify(f"[monthly] 已重启 {s.instance_id}")
            except Exception as e:
                audit(db, action="monthly_restart", target=s.instance_id,
                      detail={"account_id": s.account_id}, ok=False, error=str(e))
                notify(f"[monthly] 重启 {s.instance_id} 失败: {e}")
