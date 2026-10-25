from __future__ import annotations

import json
import re
import secrets
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import audit, config, lifecycle, policy
from ..agents import orchestrator
from ..db import get_db
from ..deps import Principal, ProjectAccess, current_principal, get_project_access, membership_role, require_reauth
from ..models import (AgentRun, Approval, AuditEvent, Blueprint, Build, Deployment, IntegrationConnection, Lead,
                      Membership, Notification, OnboardingAnswer, Project, Requirement, SiteEvent, User)
from ..security import Vault
from ..services import release
from ..services.hosting import hosting

router = APIRouter(prefix="/api", tags=["projects"])


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    workspace_id: str | None = None


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "site"
    return f"{base}-{secrets.token_hex(3)}"


@router.get("/projects")
def list_projects(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    rows = db.execute(select(Project, Membership.role).join(Membership, Membership.tenant_id == Project.tenant_id)
                      .where(Membership.user_id == principal.user.id, Membership.status == "active",
                             Project.lifecycle_state != "archived")
                      .order_by(Project.updated_at.desc())).all()
    return [orchestrator.project_view(p) | {"role": r} for p, r in rows]


@router.post("/projects")
def create_project(body: ProjectIn, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    if body.workspace_id:
        tenant_id = body.workspace_id
    else:
        m = db.scalar(select(Membership).where(Membership.user_id == principal.user.id, Membership.status == "active")
                      .order_by(Membership.created_at))
        if m is None:
            raise HTTPException(403, "You don't belong to a workspace.")
        tenant_id = m.tenant_id
    role = membership_role(db, principal.user.id, tenant_id)
    if not policy.has_permission(role, "project.create"):
        raise HTTPException(403, "Your role can't create projects in this workspace.")
    p = Project(tenant_id=tenant_id, name=body.name.strip(), slug=_slugify(body.name))
    db.add(p)
    db.flush()
    orchestrator.start(db, p)
    nf = orchestrator.next_field({}, {})
    p.transcript = p.transcript + [{"role": "assistant", "text": nf.prompt, "at": p.created_at.isoformat()}]
    audit.record(db, actor=principal.user.id, action="project.create", tenant_id=tenant_id, project_id=p.id)
    db.commit()
    return orchestrator.project_view(p)


@router.get("/projects/{project_id}")
def get_state(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    return orchestrator.state_view(db, access.project) | {"role": access.role,
                                                          "permissions": sorted(k for k, v in policy.PERMISSIONS.items() if access.role in v)}


class AnswerIn(BaseModel):
    field_key: str = Field(max_length=80)
    action: Literal["answer", "confirm", "recommend", "not_sure", "skip"] = "answer"
    value: Any = None
    expected_version: int | None = None


@router.post("/projects/{project_id}/answers")
def answer(body: AnswerIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("onboarding.write")
    if body.expected_version is not None and body.expected_version != access.project.state_version:
        # Optimistic concurrency: two tabs answering at once must not silently overwrite each other.
        db.rollback()
        raise HTTPException(409, "This project changed in another window. Refreshing…")
    out = orchestrator.handle_answer(db, access.project, access.user.id, body.field_key, body.action, body.value)
    db.commit()
    out["project"] = orchestrator.project_view(access.project)
    return out


@router.get("/projects/{project_id}/questions/{field_key}")
def get_question(field_key: str, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    """Re-open any relevant question so the owner can correct an earlier answer."""
    from ..onboarding.schema import get_field
    values, meta = orchestrator.load_answers(db, access.project)
    field = get_field(field_key, values)
    if field is None or not field.is_relevant(values):
        raise HTTPException(404, "Question not found.")
    return orchestrator.question_payload(field, values, meta)


_MAGIC = {b"\x89PNG\r\n\x1a\n": "png", b"\xff\xd8\xff": "jpg"}


@router.post("/projects/{project_id}/uploads")
async def upload(file: UploadFile = File(...), access: ProjectAccess = Depends(get_project_access),
                 db: Session = Depends(get_db)):
    access.require("onboarding.write")
    data = await file.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(413, "Please upload a file under 2 MB.")
    ext = next((e for magic, e in _MAGIC.items() if data.startswith(magic)), None)
    if ext is None and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ext = "webp"
    if ext is None:
        raise HTTPException(415, "Please upload a PNG, JPG or WebP image.")
    name = f"{uuid.uuid4().hex}.{ext}"
    d = config.settings.uploads_dir / access.project.tenant_id
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(data)
    audit.record(db, actor=access.user.id, action="upload.create", tenant_id=access.project.tenant_id,
                 project_id=access.project.id, resource=name, details={"bytes": len(data)})
    db.commit()
    return {"reference": f"upload://{name}"}


@router.get("/projects/{project_id}/agent-runs")
def agent_runs(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    rows = db.scalars(select(AgentRun).where(AgentRun.project_id == access.project.id)
                      .order_by(AgentRun.created_at.desc()).limit(100)).all()
    return [{"id": r.id, "agent": r.agent_type, "status": r.status, "provider": r.provider, "duration_ms": r.duration_ms,
             "input_version": r.input_version, "summary": (r.output or {}).get("summary"),
             "error_category": r.error_category, "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/projects/{project_id}/audit")
def audit_log(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("audit.read")
    rows = db.scalars(select(AuditEvent).where(AuditEvent.project_id == access.project.id,
                                               AuditEvent.tenant_id == access.project.tenant_id)
                      .order_by(AuditEvent.created_at.desc()).limit(200)).all()
    users = {u.id: u.email for u in db.scalars(select(User)).all()} if rows else {}
    return [{"action": r.action, "actor": users.get(r.actor, r.actor), "outcome": r.outcome, "resource": r.resource,
             "details": r.details, "created_at": r.created_at.isoformat()} for r in rows]


# ---------------------------------------------------------------- team


class InviteIn(BaseModel):
    email: str = Field(max_length=320)
    role: Literal["admin", "editor", "reviewer", "viewer"]


@router.get("/projects/{project_id}/members")
def members(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    rows = db.execute(select(User, Membership).join(Membership, Membership.user_id == User.id)
                      .where(Membership.tenant_id == access.project.tenant_id)).all()
    return [{"email": u.email, "name": u.name, "role": m.role, "status": m.status} for u, m in rows]


@router.post("/projects/{project_id}/members")
def invite(body: InviteIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("members.manage")
    user = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if user is None:
        raise HTTPException(404, "No account uses that email yet. Ask them to sign up first, then invite them.")
    existing = db.scalar(select(Membership).where(Membership.user_id == user.id,
                                                  Membership.tenant_id == access.project.tenant_id))
    if existing:
        if existing.role == "owner":
            raise HTTPException(409, "Owners' roles can't be changed here.")
        existing.role = body.role
    else:
        db.add(Membership(user_id=user.id, tenant_id=access.project.tenant_id, role=body.role, invited_by=access.user.id))
    audit.record(db, actor=access.user.id, action="members.invite", tenant_id=access.project.tenant_id,
                 project_id=access.project.id, resource=user.id, details={"role": body.role})
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- export / delete


@router.get("/projects/{project_id}/export")
def export(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("project.read")
    p = access.project

    def rows(model):
        return [{c.name: getattr(r, c.name) for c in model.__table__.columns}
                for r in db.scalars(select(model).where(model.project_id == p.id)).all()]

    data = {
        "format": "siteforge-export/1",
        "project": {c.name: getattr(p, c.name) for c in Project.__table__.columns},
        "answers": rows(OnboardingAnswer), "requirements": rows(Requirement), "blueprints": rows(Blueprint),
        "builds": rows(Build), "approvals": rows(Approval), "deployments": rows(Deployment),
        "integrations": [r | {"secret_reference": "[stored in vault]"} for r in rows(IntegrationConnection)],
    }
    if policy.has_permission(access.role, "leads.read"):
        data["leads"] = rows(Lead)
    audit.record(db, actor=access.user.id, action="project.export", tenant_id=p.tenant_id, project_id=p.id)
    db.commit()
    return Response(json.dumps(data, default=str, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{p.slug}-export.json"'})


class ConfirmIn(BaseModel):
    password: str | None = None


@router.post("/projects/{project_id}/delete")
def delete_project(body: ConfirmIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    decision = policy.evaluate("delete_project", role=access.role)
    if not decision.allowed:
        raise HTTPException(403, " ".join(decision.reasons))
    require_reauth(db, access.principal, body.password)
    p = access.project
    for conn in db.scalars(select(IntegrationConnection).where(IntegrationConnection.project_id == p.id)).all():
        if conn.secret_reference:
            Vault.delete(db, p.tenant_id, conn.secret_reference)
    for model in (Deployment, Approval, Build, Requirement, Blueprint, AgentRun, Lead, Notification, SiteEvent,
                  IntegrationConnection, OnboardingAnswer):
        db.execute(delete(model).where(model.project_id == p.id))
    hosting.delete_site(p.slug)
    release.delete_build_files(p.id)
    audit.record(db, actor=access.user.id, action="project.delete", tenant_id=p.tenant_id, project_id=p.id,
                 details={"name": p.name, "slug": p.slug})
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.get("/meta/lifecycle")
def lifecycle_meta():
    return {"states": lifecycle.STATES, "labels": lifecycle.LABELS, "policy": policy.ACTION_TIERS}
