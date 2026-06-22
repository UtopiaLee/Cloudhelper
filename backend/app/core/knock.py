"""Knock secret 路径保护（隐身强化版）。

工作原理：
  1. 启动时设定 KNOCK_SECRET（.env 配 或 随机生成）
  2. 所有 /api/* 请求必须带 X-Knock-Secret 头（值 = secret）或 ?knock= 参数
  3. 不带 / 不对 → 返回 nginx 风格 HTML 404（而非 JSON），扫描器看不出特征
  4. /api/health?probe=1 仍放行用于探活，但响应是纯文本 "ok"，不暴露 JSON 结构

效果：
  - 不知道 secret 的扫描器看到 nginx 404，认为是空 host
  - openapi.json / docs / redoc 全部关闭，没法批量发现 API 路径
  - 失败时延时随机 100-300ms，阻止时间侧信道
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import random
import secrets
from typing import Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import HTMLResponse, PlainTextResponse, Response

from app.core.config import get_settings

log = logging.getLogger(__name__)

# 完全不拦的路径（仅一个，且用 query 参数判定）
# 默认 /api/health 仍走 knock；唯有 /api/health?probe=1 才放行（给 LB / docker healthcheck）

_current_secret: Optional[str] = None

# 伪装的 nginx 404 页（跟 nginx 默认完全一致）
NGINX_404_HTML = """<html>
<head><title>404 Not Found</title></head>
<body>
<center><h1>404 Not Found</h1></center>
<hr><center>nginx</center>
</body>
</html>
"""


def init_knock() -> str:
    global _current_secret
    configured = get_settings().knock_secret.strip()
    if configured:
        _current_secret = configured
        log.info("knock secret: 使用 .env 配置的 KNOCK_SECRET")
    else:
        _current_secret = secrets.token_urlsafe(24)
        log.warning("=" * 72)
        log.warning("KNOCK_SECRET 未配置，本次启动随机生成：")
        log.warning("  %s", _current_secret)
        log.warning("访问 URL：http://localhost:5173/?key=%s", _current_secret)
        log.warning("（重启后失效，要持久化请在 .env 设 KNOCK_SECRET=...）")
        log.warning("=" * 72)
    return _current_secret


def get_knock_secret() -> str:
    if _current_secret is None:
        init_knock()
    return _current_secret or ""


def check_knock(provided: str) -> bool:
    expected = get_knock_secret()
    if not expected:
        return True
    return hmac.compare_digest(provided or "", expected)


def _fake_404() -> Response:
    """返回跟 nginx 完全一致的 404 页面。"""
    return HTMLResponse(
        content=NGINX_404_HTML,
        status_code=404,
        headers={"Server": "nginx"},
    )


class KnockMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # 非 API 路径：让前端 nginx 处理（CloudHelper 后端不应该收到非 /api 请求）
        if not path.startswith("/api"):
            return await call_next(request)

        # 探活专用：/api/health?probe=1，返回纯文本 "ok"
        # （docker healthcheck / k8s liveness 用）
        if path == "/api/health":
            if request.query_params.get("probe") == "1":
                return PlainTextResponse("ok", status_code=200)
            # 没带 probe → 也走 knock

        # WebSocket 不在这里拦
        if path.startswith("/api/ws/"):
            return await call_next(request)

        provided = request.headers.get("x-knock-secret", "").strip()
        if not provided:
            provided = request.query_params.get("knock", "").strip()

        if check_knock(provided):
            return await call_next(request)

        # 失败：随机延时 100-300ms 防时间侧信道，然后返回 nginx 404
        await asyncio.sleep(random.uniform(0.1, 0.3))
        return _fake_404()
