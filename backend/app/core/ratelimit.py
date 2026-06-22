"""登录防爆破：按 IP 滑动窗口限速 + 连续失败锁定。

策略：
  - 滑动窗口 60 秒，最多 5 次登录尝试（成功/失败都算）
  - 连续 5 次失败 → 锁定该 IP 15 分钟
  - 锁定中返回 429，并告诉剩余秒数
  - 成功登录立即清零失败计数

实现：纯内存 dict（进程重启清空，单机够用）。
"""

from __future__ import annotations

import time
from collections import deque
from threading import Lock
from typing import Optional

WINDOW_SEC = 60
WINDOW_MAX = 5
FAIL_MAX = 5
LOCK_SEC = 900  # 15 分钟

_lock = Lock()
_attempts: dict[str, deque[float]] = {}     # ip -> 最近 attempt 时间戳
_fails: dict[str, int] = {}                  # ip -> 连续失败计数
_locked_until: dict[str, float] = {}         # ip -> 锁定截止时间


def _now() -> float:
    return time.time()


def check_locked(ip: str) -> Optional[int]:
    """如果被锁，返回剩余秒数；否则 None。"""
    with _lock:
        until = _locked_until.get(ip)
        if until is None:
            return None
        remain = int(until - _now())
        if remain <= 0:
            _locked_until.pop(ip, None)
            _fails.pop(ip, None)
            return None
        return remain


def hit_rate_limit(ip: str) -> Optional[int]:
    """记录一次尝试。返回剩余秒数表示超限被拒；否则 None 放行。"""
    with _lock:
        now = _now()
        dq = _attempts.setdefault(ip, deque())
        # 清理窗口外
        while dq and now - dq[0] > WINDOW_SEC:
            dq.popleft()
        if len(dq) >= WINDOW_MAX:
            return int(WINDOW_SEC - (now - dq[0]))
        dq.append(now)
        return None


def record_failure(ip: str) -> Optional[int]:
    """记录一次失败。如果触发锁定，返回锁定时长（秒）；否则 None。"""
    with _lock:
        cnt = _fails.get(ip, 0) + 1
        _fails[ip] = cnt
        if cnt >= FAIL_MAX:
            _locked_until[ip] = _now() + LOCK_SEC
            _fails.pop(ip, None)
            return LOCK_SEC
        return None


def record_success(ip: str) -> None:
    with _lock:
        _fails.pop(ip, None)
        _locked_until.pop(ip, None)


def get_remaining_attempts(ip: str) -> int:
    with _lock:
        return max(0, FAIL_MAX - _fails.get(ip, 0))
