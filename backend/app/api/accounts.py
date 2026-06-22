import json
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import get_crypto
from app.core.db import get_db
from app.models import CloudAccount
from app.providers import make_provider
from app.schemas import AccountCreate, AccountOut

router = APIRouter()


@router.get("", response_model=list[AccountOut])
def list_accounts(
    group: str | None = None,
    provider: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(CloudAccount).order_by(CloudAccount.id)
    if group:
        q = q.where(CloudAccount.group_tag == group)
    if provider:
        q = q.where(CloudAccount.provider == provider)
    return db.scalars(q).all()


@router.post("", response_model=AccountOut)
def create_account(payload: AccountCreate, db: Session = Depends(get_db)):
    if db.scalar(select(CloudAccount).where(CloudAccount.name == payload.name)):
        raise HTTPException(400, "账户名已存在")
    enc = get_crypto().encrypt(json.dumps(payload.credentials))
    acc = CloudAccount(
        name=payload.name, provider=payload.provider, credentials_enc=enc,
        default_region=payload.default_region, group_tag=payload.group_tag,
        note=payload.note, monthly_traffic_gb=payload.monthly_traffic_gb,
        credit_total_usd=payload.credit_total_usd,
        credit_used_usd=payload.credit_used_usd,
        credit_expires_at=payload.credit_expires_at,
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc


@router.delete("/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    db.delete(acc)
    db.commit()
    return {"ok": True}


@router.put("/{account_id}")
def update_account(account_id: int, payload: AccountCreate, db: Session = Depends(get_db)):
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    acc.name = payload.name
    acc.default_region = payload.default_region
    acc.group_tag = payload.group_tag
    acc.note = payload.note
    acc.monthly_traffic_gb = payload.monthly_traffic_gb
    acc.credit_total_usd = payload.credit_total_usd
    acc.credit_used_usd = payload.credit_used_usd
    acc.credit_expires_at = payload.credit_expires_at
    if payload.credentials:
        acc.credentials_enc = get_crypto().encrypt(json.dumps(payload.credentials))
    db.commit()
    return {"ok": True}


@router.post("/{account_id}/test")
def test_account(account_id: int, db: Session = Depends(get_db)):
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    try:
        provider = make_provider(acc, db=db)
        regions = provider.list_regions()
        return {"ok": True, "regions_sample": regions[:5], "regions_total": len(regions)}
    except Exception as e:
        raise HTTPException(400, f"连通性测试失败: {e}")


# ---------- 批量导入 / 导出 ----------

@router.get("/export")
def export_accounts(db: Session = Depends(get_db)) -> dict[str, Any]:
    """导出所有账号为 JSON（含明文凭据）。

    !! 警告：返回内容包含明文凭据，调用方需自行妥善保管。
    """
    crypto = get_crypto()
    items: list[dict[str, Any]] = []
    for a in db.scalars(select(CloudAccount).order_by(CloudAccount.id)).all():
        try:
            credentials = json.loads(crypto.decrypt(a.credentials_enc))
        except Exception:
            credentials = {}
        items.append({
            "name": a.name,
            "provider": a.provider,
            "default_region": a.default_region,
            "group_tag": a.group_tag,
            "note": a.note,
            "monthly_traffic_gb": a.monthly_traffic_gb,
            "credit_total_usd": a.credit_total_usd,
            "credit_used_usd": a.credit_used_usd,
            "credit_expires_at": a.credit_expires_at.isoformat() if a.credit_expires_at else None,
            "credentials": credentials,
        })
    from datetime import datetime as _dt
    return {
        "version": 1,
        "exported_at": _dt.utcnow().isoformat() + "Z",
        "accounts": items,
    }


@router.post("/import")
def import_accounts(
    body: dict[str, Any] = Body(...),
    overwrite: bool = False,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """批量导入账号。

    body: { "accounts": [ {name, provider, ..., credentials}, ... ] }
    overwrite=True 时同名账号会覆盖；否则跳过。
    """
    accounts = body.get("accounts")
    if not isinstance(accounts, list):
        raise HTTPException(400, "请求 JSON 必须包含 accounts 数组")

    from datetime import date as _date
    crypto = get_crypto()
    created = 0
    updated = 0
    skipped = 0
    errors: list[dict] = []

    for idx, item in enumerate(accounts):
        try:
            name = (item.get("name") or "").strip()
            provider = (item.get("provider") or "").strip().lower()
            credentials = item.get("credentials") or {}
            if not name or not provider:
                errors.append({"index": idx, "error": "缺少 name 或 provider"})
                continue
            if provider not in ("aws", "gcp", "oracle", "azure"):
                errors.append({"index": idx, "name": name, "error": f"未知 provider: {provider}"})
                continue

            existing = db.scalar(select(CloudAccount).where(CloudAccount.name == name))
            if existing and not overwrite:
                skipped += 1
                continue

            expires = item.get("credit_expires_at")
            expires_date = None
            if expires:
                try:
                    expires_date = _date.fromisoformat(str(expires)[:10])
                except ValueError:
                    pass

            payload = {
                "name": name,
                "provider": provider,
                "default_region": (item.get("default_region") or "").strip(),
                "group_tag": (item.get("group_tag") or "").strip(),
                "note": (item.get("note") or "").strip(),
                "monthly_traffic_gb": float(item.get("monthly_traffic_gb") or 1.0),
                "credit_total_usd": float(item.get("credit_total_usd") or 0),
                "credit_used_usd": float(item.get("credit_used_usd") or 0),
                "credit_expires_at": expires_date,
                "credentials_enc": crypto.encrypt(json.dumps(credentials)),
            }

            if existing:
                for k, v in payload.items():
                    setattr(existing, k, v)
                updated += 1
            else:
                db.add(CloudAccount(**payload))
                created += 1
        except Exception as e:
            errors.append({"index": idx, "name": item.get("name"), "error": str(e)})

    db.commit()
    return {
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
        "total": len(accounts),
    }
