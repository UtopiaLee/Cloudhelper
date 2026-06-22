"""启动自检 + 健康探针。

启动时验证关键依赖能用；运行期 /api/health 暴露当前状态。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.core.db import SessionLocal, engine
from app.core.scheduler import get_scheduler

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    duration_ms: int = 0


@dataclass
class HealthReport:
    overall_ok: bool
    started_at: float
    checks: list[CheckResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.overall_ok,
            "started_at": self.started_at,
            "uptime_sec": int(time.time() - self.started_at),
            "checks": [
                {"name": c.name, "ok": c.ok, "detail": c.detail, "duration_ms": c.duration_ms}
                for c in self.checks
            ],
        }


_started_at = time.time()


def _check(name: str, fn) -> CheckResult:
    t0 = time.time()
    try:
        detail = fn() or "ok"
        return CheckResult(name=name, ok=True, detail=str(detail), duration_ms=int((time.time() - t0) * 1000))
    except Exception as e:
        log.warning("self-check failed: %s: %s", name, e)
        return CheckResult(name=name, ok=False, detail=f"{type(e).__name__}: {e}",
                           duration_ms=int((time.time() - t0) * 1000))


def _check_db() -> str:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return "SELECT 1 ok"


def _check_crypto() -> str:
    crypto = get_crypto()
    sample = "self-check-token"
    enc = crypto.encrypt(sample)
    dec = crypto.decrypt(enc)
    if dec != sample:
        raise ValueError("encrypt/decrypt mismatch")
    return "fernet ok"


def _check_scheduler() -> str:
    s = get_scheduler()
    if not s.running:
        raise RuntimeError("scheduler not running")
    n = len(s.get_jobs())
    return f"{n} jobs"


def _check_accounts(db: Session) -> str:
    from app.models import CloudAccount
    from sqlalchemy import select, func
    n = db.scalar(select(func.count(CloudAccount.id))) or 0
    return f"{n} accounts"


def run_health(quick: bool = True) -> HealthReport:
    checks = [
        _check("database", _check_db),
        _check("crypto", _check_crypto),
        _check("scheduler", _check_scheduler),
    ]
    if not quick:
        with SessionLocal() as db:
            checks.append(_check("accounts", lambda: _check_accounts(db)))
    overall = all(c.ok for c in checks)
    return HealthReport(overall_ok=overall, started_at=_started_at, checks=checks)


def run_startup_self_check() -> None:
    """启动时跑一次，失败的项打 WARNING 但不阻塞启动。"""
    report = run_health(quick=False)
    for c in report.checks:
        if c.ok:
            log.info("self-check %s: %s (%dms)", c.name, c.detail, c.duration_ms)
        else:
            log.warning("self-check %s FAILED: %s", c.name, c.detail)
    if report.overall_ok:
        log.info("startup self-check ALL OK")
    else:
        log.warning("startup self-check has failures, see above")
