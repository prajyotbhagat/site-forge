"""Owner tools: lead inbox, analytics summary, integrations with vault-backed secrets."""
from __future__ import annotations

import re
from datetime import timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import audit, policy
from ..agents import orchestrator
from ..db import get_db
from ..deps import ProjectAccess, get_project_access
from ..models import IntegrationConnection, Lead, Notification, SiteEvent, utcnow
from ..onboarding.schema import EMAIL_RE
from ..security import Vault

router = APIRouter(prefix="/api/projects/{project_id}", tags=["owner"])


def purge_expired(db: Session, project_id: str | None = None) -> int:
    q = delete(Lead).where(Lead.delete_after < utcnow())
    if project_id:
        q = q.where(Lead.project_id == project_id)
    return db.execute(q).rowcount or 0


@router.get("/leads")
def leads(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("leads.read")
    purged = purge_expired(db, access.project.id)
    rows = db.scalars(select(Lead).where(Lead.project_id == access.project.id, Lead.tenant_id == access.project.tenant_id)
                      .order_by(Lead.created_at.desc()).limit(200)).all()
    notes = {n.idempotency_key: n.status for n in db.scalars(select(Notification).where(
        Notification.project_id == access.project.id)).all()}
    audit.record(db, actor=access.user.id, action="leads.read", tenant_id=access.project.tenant_id,
                 project_id=access.project.id, details={"count": len(rows), "purged": purged})
    db.commit()
    return [{"id": l.id, "fields": l.fields, "is_test": l.is_test, "created_at": l.created_at.isoformat(),
             "delete_after": l.delete_after.isoformat(), "notification": notes.get(f"lead:{l.id}")} for l in rows]


@router.delete("/leads/{lead_id}")
def delete_lead(lead_id: str, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("leads.read")
    lead = db.get(Lead, lead_id)
    if lead is None or lead.project_id != access.project.id:
        raise HTTPException(404, "Enquiry not found.")
    db.delete(lead)
    audit.record(db, actor=access.user.id, action="leads.delete", tenant_id=access.project.tenant_id,
                 project_id=access.project.id, resource=lead_id)
    db.commit()
    return {"ok": True}


@router.get("/analytics")
def analytics(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    since = (utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
    rows = db.execute(select(SiteEvent.event, func.sum(SiteEvent.count)).where(
        SiteEvent.project_id == access.project.id, SiteEvent.day >= since).group_by(SiteEvent.event)).all()
    daily = db.execute(select(SiteEvent.day, SiteEvent.event, SiteEvent.count).where(
        SiteEvent.project_id == access.project.id, SiteEvent.day >= since).order_by(SiteEvent.day)).all()
    real_leads = db.scalar(select(func.count()).select_from(Lead).where(Lead.project_id == access.project.id,
                                                                       Lead.is_test.is_(False))) or 0
    bp = orchestrator.latest_blueprint(db, access.project)
    plan = (bp.content.get("measurement") if bp else None) or {}
    return {"totals": {e: int(c) for e, c in rows}, "daily": [{"day": d, "event": e, "count": c} for d, e, c in daily],
            "leads": real_leads, "plan": plan}


# ---------------------------------------------------------------- integrations


class SmtpIn(BaseModel):
    host: str = Field(max_length=255)
    port: int = Field(default=587, ge=1, le=65535)
    username: str = Field(max_length=255)
    from_address: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=500)


def _conn_view(c: IntegrationConnection) -> dict:
    return {"id": c.id, "provider": c.provider, "status": c.status, "scopes": c.granted_scopes,
            "config": c.config, "secret": "stored in vault" if c.secret_reference else None,
            "created_at": c.created_at.isoformat()}


@router.get("/integrations")
def integrations(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    rows = db.scalars(select(IntegrationConnection).where(IntegrationConnection.project_id == access.project.id)).all()
    return [_conn_view(c) for c in rows]


@router.post("/integrations/smtp")
def connect_smtp(body: SmtpIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    decision = policy.evaluate("add_integration", role=access.role)
    if not decision.allowed:
        raise HTTPException(403, " ".join(decision.reasons))
    if not re.fullmatch(r"[A-Za-z0-9.-]{3,255}", body.host) or not EMAIL_RE.match(body.from_address):
        raise HTTPException(422, "Please check the server name and sender address.")
    p = access.project
    for old in db.scalars(select(IntegrationConnection).where(IntegrationConnection.project_id == p.id,
                                                              IntegrationConnection.provider == "smtp")).all():
        if old.secret_reference:
            Vault.delete(db, p.tenant_id, old.secret_reference)
        db.delete(old)
    ref = Vault.store(db, p.tenant_id, body.password)
    conn = IntegrationConnection(tenant_id=p.tenant_id, project_id=p.id, provider="smtp",
                                 external_account_id=body.username, granted_scopes=["send-email"],
                                 config={"host": body.host, "port": body.port, "username": body.username,
                                         "from_address": body.from_address},
                                 secret_reference=ref, status="connected", last_checked_at=utcnow())
    db.add(conn)
    audit.record(db, actor=access.user.id, action="integration.connect", tenant_id=p.tenant_id, project_id=p.id,
                 resource="smtp", details={"host": body.host, "scopes": ["send-email"]})
    db.commit()
    return _conn_view(conn)


@router.delete("/integrations/{conn_id}")
def disconnect(conn_id: str, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("integrations.manage")
    c = db.get(IntegrationConnection, conn_id)
    if c is None or c.project_id != access.project.id:
        raise HTTPException(404, "Connection not found.")
    if c.secret_reference:
        Vault.delete(db, c.tenant_id, c.secret_reference)
    db.delete(c)
    audit.record(db, actor=access.user.id, action="integration.revoke", tenant_id=c.tenant_id,
                 project_id=access.project.id, resource=c.provider)
    db.commit()
    return {"ok": True}

