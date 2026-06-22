from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.auth import revoke_all_sessions, verify_credentials
from app.core.config import get_settings, reload_settings, update_env_vars
from app.core.health import run_health
from app.core.knock import set_knock_secret
from app.core.scheduler import get_scheduler
from app.core.db import get_db
from app.services.audit import audit

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
        raise HTTPException(404, f"job {job_id} 不存在")
    job.modify(next_run_time=datetime.now(job.trigger.timezone))
    return {"ok": True, "id": job_id, "scheduled_for": "now"}


class SecurityStatusOut(BaseModel):
    username_auth_enabled: bool
    token_auth_enabled: bool
    current_username: str = ""
    knock_configured: bool


class UpdateAuthIn(BaseModel):
    current_password: str = ""
    new_username: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=6, max_length=256)


class RotateKnockOut(BaseModel):
    knock_secret: str


@router.get("/system/security", response_model=SecurityStatusOut)
def security_status() -> SecurityStatusOut:
    s = get_settings()
    return SecurityStatusOut(
        username_auth_enabled=bool(s.auth_username.strip()),
        token_auth_enabled=bool(s.access_token.strip()),
        current_username=s.auth_username.strip(),
        knock_configured=bool(s.knock_secret.strip()),
    )


@router.post("/system/security/auth")
def update_auth(payload: UpdateAuthIn, db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    old_user = s.auth_username.strip()

    if old_user:
        if not payload.current_password:
            raise HTTPException(400, "请先输入当前密码")
        if not verify_credentials(old_user, payload.current_password):
            raise HTTPException(401, "当前密码错误")

    env_path = update_env_vars({
        "AUTH_USERNAME": payload.new_username.strip(),
        "AUTH_PASSWORD": payload.new_password,
    })
    reload_settings()
    revoke_all_sessions()

    audit(
        db,
        action="system.security.update_auth",
        target="system",
        detail={"old_username": old_user, "new_username": payload.new_username.strip(), "env": str(env_path)},
        ok=True,
    )
    return {"ok": True, "message": "登录账号密码已更新"}


@router.post("/system/security/knock/rotate", response_model=RotateKnockOut)
def rotate_knock_secret(db: Session = Depends(get_db)) -> RotateKnockOut:
    from secrets import token_urlsafe

    new_secret = token_urlsafe(24)
    env_path = update_env_vars({"KNOCK_SECRET": new_secret})
    reload_settings()
    set_knock_secret(new_secret)

    audit(
        db,
        action="system.security.rotate_knock",
        target="system",
        detail={"env": str(env_path)},
        ok=True,
    )
    return RotateKnockOut(knock_secret=new_secret)
