"""鉴权：两种方式 OR 关系。

模式 1：账号 + 密码（推荐）
  .env 设 AUTH_USERNAME + AUTH_PASSWORD
  浏览器登录后服务端签发 session token，cookie ch_token 存 30 天
  退出登录 = 删 cookie + 该 token 失效

模式 2：静态 token（API 客户端用）
  .env 设 ACCESS_TOKEN
  在请求里带 X-Auth-Token: <ACCESS_TOKEN> 直接通过
  适合 curl / API 自动化

两者**或**关系：任一通过即放行。两者都没设 = 鉴权关闭。
"""

from __future__ import annotations

import hmac
import logging
import secrets
import time
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.core.config import get_settings

log = logging.getLogger(__name__)

PUBLIC_PATHS = {
    "/api/health",
    "/api/auth/info",
    "/api/auth/login",
    "/api/auth/logout",
    "/docs",
    "/redoc",
    "/openapi.json",
}

# 内存 session 存储：token -> issued_at_ts
# 进程重启会全部失效（用户需要重新登录），简单可靠
_SESSIONS: dict[str, float] = {}
_SESSION_TTL = 86400 * 30  # 30 天


def auth_enabled() -> bool:
    s = get_settings()
    return bool(s.auth_username.strip() or s.access_token.strip())


def _check_static_token(provided: str) -> bool:
    expected = get_settings().access_token.strip()
    if not expected:
        return False
    return hmac.compare_digest(provided.strip(), expected)


def _check_session(token: str) -> bool:
    if not token:
        return False
    issued = _SESSIONS.get(token)
    if issued is None:
        return False
    if time.time() - issued > _SESSION_TTL:
        _SESSIONS.pop(token, None)
        return False
    return True


def verify_credentials(username: str, password: str) -> bool:
    s = get_settings()
    if not s.auth_username.strip():
        return False
    u_ok = hmac.compare_digest(username, s.auth_username.strip())
    p_ok = hmac.compare_digest(password, s.auth_password)
    return u_ok and p_ok


def issue_session() -> str:
    tok = secrets.token_urlsafe(32)
    _SESSIONS[tok] = time.time()
    return tok


def revoke_session(token: str) -> None:
    _SESSIONS.pop(token, None)


def _extract_token(request: Request) -> str:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:].strip()
    t = request.headers.get("x-auth-token", "").strip()
    if t:
        return t
    return request.cookies.get("ch_token", "").strip()


class TokenAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not auth_enabled():
            return await call_next(request)
        path = request.url.path
        if not path.startswith("/api"):
            return await call_next(request)
        if path in PUBLIC_PATHS or path.startswith("/api/ws/"):
            return await call_next(request)

        token = _extract_token(request)
        if _check_session(token) or _check_static_token(token):
            return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "未授权：请登录"})


def ws_check_token(query_token: str) -> bool:
    """WebSocket 路由的鉴权。同时支持 session token 和 static token。"""
    if not auth_enabled():
        return True
    if not query_token:
        return False
    return _check_session(query_token) or _check_static_token(query_token)
