"""Alert rules (what should notify you)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...models import AlertRule
from ...schemas import AlertRuleIn, AlertRuleOut, AlertRuleUpdate, Message
from ...services.timeutil import as_utc
from ..deps import get_db

router = APIRouter(prefix="/alerts", tags=["alerts"])


FIELDS = tuple(AlertRuleIn.model_fields)


def _out(rule: AlertRule) -> AlertRuleOut:
    out = AlertRuleOut.model_validate(rule)
    return out.model_copy(update={"created_at": as_utc(rule.created_at)})


def _get(db: Session, rule_id: int) -> AlertRule:
    rule = db.get(AlertRule, rule_id)
    if rule is None:
        raise HTTPException(404, f"alert rule {rule_id} not found")
    return rule


def _clean(data: AlertRuleIn) -> dict:
    values = data.model_dump()
    for key in ("keywords", "brand", "category"):
        if isinstance(values.get(key), str):
            values[key] = values[key].strip() or None
    values["channels"] = list(dict.fromkeys(values.get("channels") or ["in_app"]))
    return values


@router.get("", response_model=list[AlertRuleOut])
def list_rules(db: Session = Depends(get_db)) -> list[AlertRuleOut]:
    return [_out(r) for r in db.scalars(select(AlertRule).order_by(AlertRule.id))]


@router.post("", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
def create_rule(body: AlertRuleIn, db: Session = Depends(get_db)) -> AlertRuleOut:
    rule = AlertRule(**_clean(body))
    db.add(rule)
    db.commit()
    return _out(rule)


@router.patch("/{rule_id}", response_model=AlertRuleOut)
def update_rule(rule_id: int, body: AlertRuleUpdate, db: Session = Depends(get_db)) -> AlertRuleOut:
    rule = _get(db, rule_id)
    current = {name: getattr(rule, name) for name in FIELDS}
    changes = body.model_dump(exclude_unset=True)
    for required in ("name", "exclude_suspicious", "channels", "enabled"):
        if required in changes and changes[required] is None:
            raise HTTPException(422, f"{required} cannot be null")
    try:
        merged = AlertRuleIn.model_validate({**current, **changes})
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False)) from exc
    for name, value in _clean(merged).items():
        setattr(rule, name, value)
    db.commit()
    return _out(rule)


@router.delete("/{rule_id}", response_model=Message)
def delete_rule(rule_id: int, db: Session = Depends(get_db)) -> Message:
    db.delete(_get(db, rule_id))
    db.commit()
    return Message(message=f"alert rule {rule_id} deleted")
