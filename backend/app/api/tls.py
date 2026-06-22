"""TLS 证书管理 API。

证书写到 settings.data_dir/ssl/{cert.pem, key.pem}，docker-compose 已经把
data/ 挂到 nginx 容器的 /data/ssl，nginx 配置 include 时检测到就启用 443。

通过 nginx -s reload 让改动立刻生效（容器间通过 docker socket 或 supervisord 难做，
所以这里改用：写完文件后给前端 nginx 容器发 HUP 信号 —— 但跨容器困难。
最实用：写完文件提示用户 docker compose restart frontend。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from cryptography import x509
from cryptography.hazmat.backends import default_backend
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.core.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter()


def _ssl_dir() -> Path:
    p = get_settings().data_dir / "ssl"
    p.mkdir(parents=True, exist_ok=True)
    return p


class TLSStatus(BaseModel):
    enabled: bool
    cert_path: str
    key_path: str
    subject: str = ""
    issuer: str = ""
    not_before: Optional[datetime] = None
    not_after: Optional[datetime] = None
    days_until_expiry: Optional[int] = None
    sans: list[str] = []
    error: str = ""


def _parse_cert(cert_path: Path) -> TLSStatus:
    status = TLSStatus(enabled=True, cert_path=str(cert_path), key_path=str(_ssl_dir() / "key.pem"))
    try:
        data = cert_path.read_bytes()
        cert = x509.load_pem_x509_certificate(data, default_backend())
        status.subject = cert.subject.rfc4514_string()
        status.issuer = cert.issuer.rfc4514_string()
        status.not_before = cert.not_valid_before_utc.replace(tzinfo=None)
        status.not_after = cert.not_valid_after_utc.replace(tzinfo=None)
        status.days_until_expiry = (status.not_after - datetime.utcnow()).days
        try:
            san_ext = cert.extensions.get_extension_for_oid(x509.SubjectAlternativeName.oid)
            sans = san_ext.value
            status.sans = [str(n.value) for n in sans]
        except x509.ExtensionNotFound:
            status.sans = []
    except Exception as e:
        status.error = f"证书解析失败：{e}"
    return status


@router.get("/system/tls", response_model=TLSStatus)
def get_tls_status() -> TLSStatus:
    cert_path = _ssl_dir() / "cert.pem"
    key_path = _ssl_dir() / "key.pem"
    if not cert_path.exists() or not key_path.exists():
        return TLSStatus(
            enabled=False, cert_path=str(cert_path), key_path=str(key_path),
        )
    return _parse_cert(cert_path)


@router.post("/system/tls/upload", response_model=TLSStatus)
async def upload_tls(
    cert: UploadFile = File(...),
    key: UploadFile = File(...),
) -> TLSStatus:
    """上传证书 + 私钥。两个都是 PEM 格式。"""
    cert_bytes = await cert.read()
    key_bytes = await key.read()

    # 简单校验
    if not re.search(rb"-----BEGIN CERTIFICATE-----", cert_bytes):
        raise HTTPException(400, "证书文件格式不正确，必须是 PEM 格式（含 BEGIN CERTIFICATE）")
    if not re.search(rb"-----BEGIN (?:RSA |EC |)PRIVATE KEY-----", key_bytes):
        raise HTTPException(400, "私钥文件格式不正确，必须是 PEM 格式")

    # 校验证书 + 私钥匹配（解析公钥比对）
    try:
        from cryptography.hazmat.primitives import serialization
        cert_obj = x509.load_pem_x509_certificate(cert_bytes, default_backend())
        key_obj = serialization.load_pem_private_key(key_bytes, password=None, backend=default_backend())
        cert_pub = cert_obj.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        key_pub = key_obj.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        if cert_pub != key_pub:
            raise HTTPException(400, "证书与私钥不匹配（公钥不一致）")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"证书/私钥解析失败：{e}")

    cert_path = _ssl_dir() / "cert.pem"
    key_path = _ssl_dir() / "key.pem"
    cert_path.write_bytes(cert_bytes)
    key_path.write_bytes(key_bytes)
    try:
        # 仅 POSIX 有效
        cert_path.chmod(0o644)
        key_path.chmod(0o600)
    except Exception:
        pass
    log.info("TLS cert uploaded: subject=%s", cert_obj.subject.rfc4514_string())
    return _parse_cert(cert_path)


@router.delete("/system/tls")
def delete_tls() -> dict:
    cert_path = _ssl_dir() / "cert.pem"
    key_path = _ssl_dir() / "key.pem"
    cert_path.unlink(missing_ok=True)
    key_path.unlink(missing_ok=True)
    log.info("TLS cert deleted")
    return {"ok": True}
