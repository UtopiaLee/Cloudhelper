"""审计日志写入 + Webhook 通知。"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuditLog

log = logging.getLogger(__name__)


def audit(db: Session, *, action: str, target: str = "", detail: dict | None = None,
          actor: str = "system", ok: bool = True, error: str = "") -> None:
    db.add(AuditLog(action=action, target=target, detail=detail or {}, actor=actor, ok=ok, error=error))
    db.commit()


def notify(message: str, **extra: Any) -> None:
    url = get_settings().notify_webhook_url
    if not url:
        log.info("notify (no webhook): %s %s", message, extra)
        return
    try:
        httpx.post(url, json={"message": message, **extra}, timeout=5.0)
    except Exception as e:
        log.warning("notify failed: %s", e)
