from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models import CloudAccount
from app.providers import make_provider
from app.providers.base import FirewallRule
from app.schemas import FirewallRuleIn, FirewallRuleOut
from app.services.audit import audit

router = APIRouter()


def _get_account(account_id: int, db: Session) -> CloudAccount:
    acc = db.get(CloudAccount, account_id)
    if not acc:
        raise HTTPException(404, "账户不存在")
    return acc


@router.get("", response_model=list[FirewallRuleOut])
def list_rules(account_id: int, region: str | None = None, db: Session = Depends(get_db)):
    provider = make_provider(_get_account(account_id, db), db=db)
    return [FirewallRuleOut(**rule.__dict__) for rule in provider.list_firewall_rules(region=region)]


@router.post("", response_model=dict)
def add_rule(account_id: int, region: str, payload: FirewallRuleIn, db: Session = Depends(get_db)):
    provider = make_provider(_get_account(account_id, db), db=db)
    rule = FirewallRule(id="", **payload.model_dump())
    try:
        rid = provider.add_firewall_rule(rule, region)
    except Exception as e:
        msg = str(e)
        if "InvalidGroup.NotFound" in msg:
            msg += "\n\n💡 提示：选中的安全组不存在或不属于本 region"
        elif "InvalidPermission.Duplicate" in msg:
            msg += "\n\n💡 提示：相同的规则已存在"
        elif "VPCIdNotSpecified" in msg or "default VPC" in msg:
            msg += "\n\n💡 提示：账户在此 region 没有默认 VPC，请手动指定 SG"
        raise HTTPException(400, msg)
    audit(db, action="firewall.add", target=rid, detail={"account_id": account_id, "rule": payload.model_dump()})
    return {"ok": True, "id": rid}


@router.delete("/{rule_id:path}")
def delete_rule(account_id: int, rule_id: str, region: str, db: Session = Depends(get_db)):
    provider = make_provider(_get_account(account_id, db), db=db)
    provider.remove_firewall_rule(rule_id, region)
    audit(db, action="firewall.delete", target=rule_id, detail={"account_id": account_id})
    return {"ok": True}
