"""SSH 密钥管理。生成或上传一把私钥；公钥用户拷去填到云。"""

from __future__ import annotations

import io

import paramiko
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.core.db import get_db
from app.models import SSHKey
from app.schemas import SSHKeyCreate, SSHKeyOut

router = APIRouter()


def _derive_public(pem: str, passphrase: str = "") -> str:
    pp = passphrase or None
    for cls in (paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.RSAKey, paramiko.DSSKey):
        try:
            k = cls.from_private_key(io.StringIO(pem), password=pp)
            return f"{k.get_name()} {k.get_base64()}"
        except (paramiko.SSHException, ValueError):
            continue
    raise ValueError("无法识别的私钥格式（支持 ed25519/ecdsa/rsa/dss，OpenSSH 或 PEM）")


@router.get("", response_model=list[SSHKeyOut])
def list_keys(db: Session = Depends(get_db)):
    return db.scalars(select(SSHKey).order_by(SSHKey.id)).all()


@router.post("", response_model=SSHKeyOut)
def create_key(payload: SSHKeyCreate, db: Session = Depends(get_db)):
    if db.scalar(select(SSHKey).where(SSHKey.name == payload.name)):
        raise HTTPException(400, "同名密钥已存在")
    try:
        public = _derive_public(payload.private_key, payload.passphrase)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if payload.is_default:
        db.execute(update(SSHKey).values(is_default=False))
    crypto = get_crypto()
    row = SSHKey(
        name=payload.name,
        private_key_enc=crypto.encrypt(payload.private_key),
        passphrase_enc=crypto.encrypt(payload.passphrase) if payload.passphrase else "",
        public_key=public,
        is_default=payload.is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/generate", response_model=SSHKeyOut)
def generate_key(name: str, is_default: bool = True, db: Session = Depends(get_db)):
    """后端生成一把 Ed25519 密钥。前端拿到公钥贴到各家云控制台即可。"""
    if db.scalar(select(SSHKey).where(SSHKey.name == name)):
        raise HTTPException(400, "同名密钥已存在")
    key = paramiko.Ed25519Key.generate()
    buf = io.StringIO()
    key.write_private_key(buf)
    pem = buf.getvalue()
    public = f"{key.get_name()} {key.get_base64()} cloudhelper@{name}"

    if is_default:
        db.execute(update(SSHKey).values(is_default=False))
    crypto = get_crypto()
    row = SSHKey(
        name=name,
        private_key_enc=crypto.encrypt(pem),
        passphrase_enc="",
        public_key=public,
        is_default=is_default,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{key_id}")
def delete_key(key_id: int, db: Session = Depends(get_db)):
    row = db.get(SSHKey, key_id)
    if not row:
        raise HTTPException(404, "密钥不存在")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.put("/{key_id}/default")
def set_default(key_id: int, db: Session = Depends(get_db)):
    row = db.get(SSHKey, key_id)
    if not row:
        raise HTTPException(404, "密钥不存在")
    db.execute(update(SSHKey).values(is_default=False))
    row.is_default = True
    db.commit()
    return {"ok": True}
