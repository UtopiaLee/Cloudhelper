from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.scheduler import remove_user_schedule, upsert_user_schedule
from app.models import Schedule
from app.schemas import ScheduleIn, ScheduleOut
from app.services.audit import audit

router = APIRouter()


def _validate_payload(payload: ScheduleIn) -> None:
    if payload.trigger_type == "cron":
        if not payload.cron or len(payload.cron.split()) != 5:
            raise HTTPException(400, "cron 触发必须填写 5 段 cron 表达式")
        try:
            CronTrigger.from_crontab(payload.cron)
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"cron 表达式不合法: {exc}")
    elif payload.trigger_type == "date":
        if payload.run_at is None:
            raise HTTPException(400, "date 触发必须指定 run_at")
        run_at = payload.run_at
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=timezone.utc)
        if run_at <= datetime.now(timezone.utc):
            raise HTTPException(400, "run_at 必须是未来时间")


def _arm(s: Schedule) -> None:
    if not s.enabled:
        remove_user_schedule(s.id)
        return
    if s.trigger_type == "date":
        upsert_user_schedule(s.id, s.account_id, s.instance_id, s.action,
                             trigger_type="date", run_at=s.run_at)
    else:
        upsert_user_schedule(s.id, s.account_id, s.instance_id, s.action,
                             trigger_type="cron", cron=s.cron)


@router.get("", response_model=list[ScheduleOut])
def list_schedules(account_id: int, db: Session = Depends(get_db)):
    return db.scalars(select(Schedule).where(Schedule.account_id == account_id)).all()


@router.post("", response_model=ScheduleOut)
def create_schedule(account_id: int, payload: ScheduleIn, db: Session = Depends(get_db)):
    _validate_payload(payload)
    s = Schedule(account_id=account_id, **payload.model_dump())
    db.add(s)
    db.commit()
    db.refresh(s)
    _arm(s)
    audit(db, action="schedule.create", target=s.instance_id,
          detail={"account_id": account_id, "schedule_id": s.id,
                  "trigger_type": s.trigger_type, "cron": s.cron,
                  "run_at": s.run_at.isoformat() if s.run_at else None,
                  "action": s.action})
    return s


@router.put("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(account_id: int, schedule_id: int, payload: ScheduleIn, db: Session = Depends(get_db)):
    _validate_payload(payload)
    s = db.get(Schedule, schedule_id)
    if not s or s.account_id != account_id:
        raise HTTPException(404, "调度不存在")
    for k, v in payload.model_dump().items():
        setattr(s, k, v)
    db.commit()
    _arm(s)
    return s


@router.delete("/{schedule_id}")
def delete_schedule(account_id: int, schedule_id: int, db: Session = Depends(get_db)):
    s = db.get(Schedule, schedule_id)
    if not s or s.account_id != account_id:
        raise HTTPException(404, "调度不存在")
    remove_user_schedule(s.id)
    db.delete(s)
    db.commit()
    return {"ok": True}
