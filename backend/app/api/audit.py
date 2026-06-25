from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import AuditLog

router = APIRouter()


@router.get("")
def list_audit(
    limit: int = Query(100, ge=1, le=1000),
    schedule_id: int | None = None,
    instance_id: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(AuditLog).order_by(AuditLog.id.desc())
    if schedule_id is not None:
        q = q.where(AuditLog.detail["schedule_id"].as_integer() == schedule_id)
    if instance_id:
        q = q.where(AuditLog.target == instance_id)
    rows = db.scalars(q.limit(limit)).all()
    return [
        {
            "id": r.id, "at": r.at.isoformat() + "Z" if r.at and r.at.tzinfo is None else (r.at.isoformat() if r.at else None),
            "actor": r.actor, "action": r.action,
            "target": r.target, "detail": r.detail, "ok": r.ok, "error": r.error,
        }
        for r in rows
    ]
