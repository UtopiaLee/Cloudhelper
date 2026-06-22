"""自定义异常 + 全局 ExceptionHandler。

所有未捕获的云 SDK / paramiko 异常都会被映射到合适的 HTTPException，
带友好提示，永不裸露 traceback 给前端。
"""

from __future__ import annotations

import logging
import traceback

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

log = logging.getLogger(__name__)


class CloudHelperError(Exception):
    """业务错误基类。message 直接给前端。"""
    status_code = 400

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        if status_code is not None:
            self.status_code = status_code


class ProviderError(CloudHelperError):
    """云 SDK 操作失败。"""
    status_code = 400


class CredentialError(CloudHelperError):
    """凭据问题（密钥过期、权限不足等）。"""
    status_code = 401


class NotFoundError(CloudHelperError):
    status_code = 404


# 把 boto3 / google / paramiko 等常见底层异常转成友好消息
def _humanize_exception(exc: Exception) -> tuple[int, str]:
    """返回 (status_code, message)。"""
    name = type(exc).__name__
    msg = str(exc)

    # boto3 / botocore
    if name == "ClientError":
        # AWS ClientError 通常自带 "An error occurred (XXX) when calling..."
        if "InvalidParameterCombination" in msg and "Free Tier" in msg:
            return 400, msg + "\n\n💡 当前 region 此规格不在 Free Tier。换 t3.micro 或换 us-east-1"
        if "InvalidGroup.NotFound" in msg:
            return 400, msg + "\n\n💡 选中的安全组不存在或不属于本 region/VPC"
        if "InvalidPermission.Duplicate" in msg:
            return 400, msg + "\n\n💡 相同的规则已存在"
        if "UnauthorizedOperation" in msg or "AccessDenied" in msg:
            return 401, msg + "\n\n💡 AWS IAM 权限不足，给该用户加对应策略后重试"
        if "InvalidClientTokenId" in msg or "AuthFailure" in msg:
            return 401, msg + "\n\n💡 Access Key 无效或已撤销，去账户页更新凭据"
        if "RequestLimitExceeded" in msg or "Throttling" in msg:
            return 429, msg + "\n\n💡 AWS API 限流，稍后重试"
        if "VcpuLimitExceeded" in msg:
            return 400, msg + "\n\n💡 vCPU 配额用满，去 AWS Service Quotas 申请"
        return 400, msg

    if name == "EndpointConnectionError":
        return 502, f"连接 AWS 端点失败：{msg}"
    if name == "NoCredentialsError":
        return 401, "AWS 凭据未配置或为空"

    # google-cloud
    if "google.api_core.exceptions" in str(type(exc).__module__):
        if "PermissionDenied" in name:
            return 401, msg + "\n\n💡 GCP SA 权限不足"
        if "NotFound" in name:
            return 404, msg
        if "ResourceExhausted" in name:
            return 429, msg + "\n\n💡 GCP 配额耗尽"
        if "Unauthenticated" in name:
            return 401, msg + "\n\n💡 GCP SA 凭据无效"
        return 400, msg
    if "googleapiclient.errors" in str(type(exc).__module__):
        return 400, msg

    # paramiko
    if name == "AuthenticationException":
        return 401, f"SSH 认证失败：{msg}"
    if name == "SSHException":
        return 502, f"SSH 协议错误：{msg}"
    if name == "BadHostKeyException":
        return 502, f"SSH 主机密钥不匹配：{msg}"

    # 数据库
    if "sqlalchemy.exc" in str(type(exc).__module__):
        if "IntegrityError" in name:
            return 400, f"数据冲突：{msg}"
        return 500, f"数据库错误：{msg}"

    # 网络
    if name in ("ConnectionError", "ConnectionRefusedError", "TimeoutError"):
        return 502, f"网络连接失败：{msg}"
    if name == "OSError":
        return 502, f"系统调用失败：{msg}"

    # 兜底
    return 500, f"未处理的错误（{name}）：{msg}"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(CloudHelperError)
    async def cloudhelper_handler(request: Request, exc: CloudHelperError):
        log.info("CloudHelperError: %s", exc.message)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.exception_handler(HTTPException)
    async def http_handler(request: Request, exc: HTTPException):
        # 404 时如果没带 knock，伪装成 nginx 404 HTML
        if exc.status_code == 404:
            from app.core.knock import check_knock, NGINX_404_HTML
            provided = request.headers.get("x-knock-secret", "").strip()
            if not provided:
                provided = request.query_params.get("knock", "").strip()
            if not check_knock(provided):
                from starlette.responses import HTMLResponse
                return HTMLResponse(content=NGINX_404_HTML, status_code=404,
                                    headers={"Server": "nginx"})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail},
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        # Pydantic 校验失败
        errs = exc.errors()
        if errs:
            first = errs[0]
            field = ".".join(str(x) for x in first.get("loc", []))
            return JSONResponse(status_code=422,
                                content={"detail": f"参数错误 [{field}]: {first.get('msg', '')}"})
        return JSONResponse(status_code=422, content={"detail": "请求参数无效"})

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        status, msg = _humanize_exception(exc)
        # 5xx 错误打 traceback 到日志（不发给前端）
        if status >= 500:
            log.error("Unhandled %s on %s %s\n%s",
                      type(exc).__name__, request.method, request.url.path,
                      traceback.format_exc())
        else:
            log.info("Handled %s on %s: %s", type(exc).__name__, request.url.path, msg)
        return JSONResponse(status_code=status, content={"detail": msg})
