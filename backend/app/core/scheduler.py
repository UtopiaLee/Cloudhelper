"""APScheduler 单例 + 任务编排。

- 用户定时（cron 周期 或 date 单次）
- SSH 流量采集 + 保活：每 10 分钟跑
- 月初 01:00：重启被流量停的实例
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED, EVENT_JOB_MISSED, JobExecutionEvent
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from sqlalchemy import create_engine

from app.core.config import get_settings

log = logging.getLogger(__name__)
settings = get_settings()


def _jobstore_engine():
    """Dedicated engine for the APScheduler jobstore.

    Separate from the request-path engine and given a busy_timeout so that
    concurrent schedule upserts (request thread) and scheduler writes wait
    for the lock instead of raising 'database is locked'.
    """
    url = settings.database_url
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False, "timeout": 30}
    else:
        connect_args = {}
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(settings.tz)
    except Exception:
        return ZoneInfo("UTC")


_scheduler: BackgroundScheduler | None = None


def _on_job_event(event: JobExecutionEvent) -> None:
    """把 scheduler 内部异常写到日志 + 审计表。"""
    from app.core.db import SessionLocal
    from app.models import AuditLog

    job_id = event.job_id
    if event.code == EVENT_JOB_ERROR:
        exc = getattr(event, "exception", None)
        log.error("scheduler job FAILED id=%s exception=%r", job_id, exc)
        try:
            with SessionLocal() as db:
                db.add(AuditLog(
                    actor="scheduler", action="job.error", target=job_id,
                    detail={"exception": str(exc)} if exc else {}, ok=False,
                    error=str(exc) if exc else "",
                ))
                db.commit()
        except Exception:
            pass
    elif event.code == EVENT_JOB_MISSED:
        log.warning("scheduler job MISSED id=%s", job_id)
        try:
            with SessionLocal() as db:
                db.add(AuditLog(
                    actor="scheduler", action="job.missed", target=job_id,
                    ok=False, error="missed run window",
                ))
                db.commit()
        except Exception:
            pass


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler(
            jobstores={"default": SQLAlchemyJobStore(engine=_jobstore_engine())},
            timezone=_tz(),
            job_defaults={
                "coalesce": True,         # 错过多次只补跑一次
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
        _scheduler.add_listener(_on_job_event, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED | EVENT_JOB_MISSED)
    return _scheduler


def start() -> None:
    s = get_scheduler()
    if s.running:
        return
    s.start()
    for stale in ("sys.cost_ingest", "sys.traffic_ingest", "sys.budget_check", "sys.traffic_poll"):
        if s.get_job(stale):
            s.remove_job(stale)

    s.add_job(
        "app.services.ssh_collector:collect_all",
        CronTrigger.from_crontab("*/10 * * * *", timezone=_tz()),
        id="sys.ssh_collect",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    s.add_job(
        "app.services.ssh_collector:probe_alive_all",
        CronTrigger.from_crontab("*/5 * * * *", timezone=_tz()),
        id="sys.ssh_alive_probe",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=180,
    )
    s.add_job(
        "app.services.billing_tick:tick_all",
        CronTrigger.from_crontab("*/30 * * * *", timezone=_tz()),
        id="sys.billing_tick",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=600,
    )
    s.add_job(
        "app.services.billing_tick:cleanup_old_ticks",
        CronTrigger(day=1, hour=2, minute=0, timezone=_tz()),
        id="sys.billing_cleanup",
        replace_existing=True,
    )
    s.add_job(
        "app.services.scheduler_jobs:monthly_reset",
        CronTrigger(day=1, hour=1, minute=0, timezone=_tz()),
        id="sys.monthly_reset",
        replace_existing=True,
    )
    s.add_job(
        "app.services.scheduler_jobs:remind_account_expiry",
        CronTrigger(hour=9, minute=0, timezone=_tz()),
        id="sys.account_expiry_remind",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=3600,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def upsert_user_schedule(
    schedule_id: int, account_id: int, instance_id: str, action: str,
    *, trigger_type: str = "cron", cron: str = "", run_at: Optional[datetime] = None,
) -> None:
    s = get_scheduler()
    tz = _tz()
    if trigger_type == "date":
        if run_at is None:
            raise ValueError("date 触发需要 run_at")
        # 前端发来的是 UTC ISO；如果不带 tz 就当作 UTC
        from datetime import timezone as _tz_mod
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=_tz_mod.utc)
        # 转成本地时区给 trigger（APScheduler 内部还是按 UTC 比较）
        local_dt = run_at.astimezone(tz)
        trigger = DateTrigger(run_date=local_dt, timezone=tz)
    else:
        parts = (cron or "").split()
        if len(parts) != 5:
            raise ValueError("cron 必须是 5 段 (分 时 日 月 周)")
        trigger = CronTrigger(
            minute=parts[0], hour=parts[1], day=parts[2], month=parts[3], day_of_week=parts[4],
            timezone=tz,
        )
    s.add_job(
        "app.services.scheduler_jobs:run_instance_action",
        trigger,
        kwargs={"account_id": account_id, "instance_id": instance_id, "action": action,
                "schedule_id": schedule_id},
        id=f"user.{schedule_id}",
        replace_existing=True,
        misfire_grace_time=300,
    )


def remove_user_schedule(schedule_id: int) -> None:
    s = get_scheduler()
    job_id = f"user.{schedule_id}"
    if s.get_job(job_id):
        s.remove_job(job_id)
