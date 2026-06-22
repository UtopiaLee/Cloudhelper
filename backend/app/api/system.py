from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.core.health import run_health
from app.core.scheduler import get_scheduler

router = APIRouter()


class HealthOut(BaseModel):
    status: str
    time: datetime


@router.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok", time=datetime.utcnow())


@router.get("/health/full")
def health_full(deep: bool = Query(False, description="true 则跑深度自检（含 DB 读账号）")) -> dict:
    """详细健康检查 — 浏览器 / 运维探针都能调。"""
    report = run_health(quick=not deep)
    return report.to_dict()


@router.get("/system/jobs")
def list_jobs() -> list[dict]:
    """列出所有 APScheduler job 及下次运行时间。"""
    s = get_scheduler()
    out = []
    for j in s.get_jobs():
        out.append({
            "id": j.id,
            "name": j.name or j.id,
            "trigger": str(j.trigger),
            "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
            "func": getattr(j, "func_ref", ""),
            "misfire_grace_time": j.misfire_grace_time,
        })
    return out


@router.post("/system/jobs/{job_id}/run-now")
def trigger_job(job_id: str) -> dict:
    """立即触发某个系统任务（用 /system/jobs 列出来的 id）。"""
    s = get_scheduler()
    job = s.get_job(job_id)
    if not job:
        from fastapi import HTTPException
        raise HTTPException(404, f"job {job_id} 不存在")
    job.modify(next_run_time=datetime.now(job.trigger.timezone))
    return {"ok": True, "id": job_id, "scheduled_for": "now"}
