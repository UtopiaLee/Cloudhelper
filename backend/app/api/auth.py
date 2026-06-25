"""鉴权相关接口。"""

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.auth import (
    _check_static_token, _extract_token, auth_enabled, issue_session,
    revoke_session, verify_credentials,
)
from app.core.ratelimit import (
    check_locked, get_remaining_attempts, hit_rate_limit,
    record_failure, record_success,
)
from app.services.audit import audit
from sqlalchemy.orm import Session
from fastapi import Depends
from app.core.db import get_db

router = APIRouter()


def _client_ip(request: Request) -> str:
    # 只信任直连对端 IP 作为限流/锁定的 key。
    # X-Forwarded-For / X-Real-IP 由客户端可控，信任它会让攻击者轮换该头绕过
    # 每 IP 的登录限流与锁定。没有可信反代配置时，固定用 request.client.host。
    return request.client.host if request.client else "unknown"


class LoginIn(BaseModel):
    username: str = ""
    password: str = ""
    token: str = ""


class LoginOut(BaseModel):
    ok: bool
    token: str = ""
    message: str = ""


@router.get("/auth/info")
def auth_info() -> dict:
    from app.core.config import get_settings
    s = get_settings()
    return {
        "auth_required": auth_enabled(),
        "username_auth": bool(s.auth_username.strip()),
        "token_auth": bool(s.access_token.strip()),
    }


@router.post("/auth/login", response_model=LoginOut)
def login(payload: LoginIn, request: Request, response: Response,
          db: Session = Depends(get_db)) -> LoginOut:
    if not auth_enabled():
        return LoginOut(ok=True, message="鉴权未启用")

    ip = _client_ip(request)

    # 1. 锁定检查
    locked = check_locked(ip)
    if locked is not None:
        mins = locked // 60 + 1
        raise HTTPException(429, f"登录尝试过多，IP {ip} 已被锁定，{mins} 分钟后再试")

    # 2. 速率限制
    rl = hit_rate_limit(ip)
    if rl is not None:
        raise HTTPException(429, f"登录请求过快，请 {rl} 秒后再试")

    def _fail(reason: str) -> None:
        lock = record_failure(ip)
        audit(db, action="auth.login", target=ip,
              detail={"username": payload.username, "reason": reason},
              ok=False, error=reason)
        if lock:
            raise HTTPException(429, f"连续登录失败 5 次，IP 已被锁定 15 分钟")
        remain = get_remaining_attempts(ip)
        raise HTTPException(401, f"{reason}（还剩 {remain} 次尝试）")

    # 3. 校验凭据
    if payload.username and payload.password:
        if not verify_credentials(payload.username, payload.password):
            _fail("用户名或密码错误")
    elif payload.token:
        if not _check_static_token(payload.token):
            _fail("token 错误")
    else:
        raise HTTPException(400, "请提供用户名/密码 或 token")

    # 4. 成功
    record_success(ip)
    if payload.username:
        method = "password"
    else:
        method = "token"
    # 无论哪种登录方式都签发短随机 session token 放进 httponly cookie，
    # 避免把静态 ACCESS_TOKEN 回写到 cookie / 响应体（减少其在链路里出现的次数）。
    tok = issue_session()
    audit(db, action="auth.login", target=ip,
          detail={"username": payload.username, "method": method}, ok=True)
    response.set_cookie(
        "ch_token", tok,
        httponly=True, secure=True, samesite="strict", max_age=86400 * 30,
    )
    return LoginOut(ok=True, token=tok)


@router.post("/auth/logout")
def logout(request: Request, response: Response) -> dict:
    tok = _extract_token(request)
    if tok:
        revoke_session(tok)
    response.delete_cookie("ch_token")
    return {"ok": True}
