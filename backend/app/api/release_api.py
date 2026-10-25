"""Blueprint approval, builds/previews, release approval, deployment and rollback."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config, lifecycle, policy
from ..agents import orchestrator
from ..db import get_db
from ..deps import ProjectAccess, get_project_access, load_session, membership_role, require_reauth
from ..models import Approval, Blueprint, Build, Deployment, Project
from ..services import release
from ..services.hosting import hosting

router = APIRouter(prefix="/api", tags=["release"])


class ScopeDecisionIn(BaseModel):
    content_hash: str = Field(min_length=64, max_length=64)
    decision: Literal["approved", "rejected"] = "approved"
    comment: str | None = Field(default=None, max_length=1000)


@router.post("/projects/{project_id}/blueprint/decision")
def decide_scope(body: ScopeDecisionIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    p = access.project
    decision = policy.evaluate("approve_scope", role=access.role)
    if not decision.allowed:
        raise HTTPException(403, " ".join(decision.reasons))
    bp = orchestrator.latest_blueprint(db, p)
    if bp is None or bp.status == "superseded" or p.lifecycle_state != "brief_ready":
        raise HTTPException(409, "There's no plan waiting for approval.")
    if bp.content_hash != body.content_hash:
        raise HTTPException(409, "The plan changed since you opened it. Please review the latest version.")
    if body.decision == "approved" and bp.content.get("blocking"):
        raise HTTPException(409, "Please resolve the flagged items before approving.")
    db.add(Approval(tenant_id=p.tenant_id, project_id=p.id, action="scope", resource_id=bp.id, diff_hash=bp.content_hash,
                    approver_id=access.user.id, decision=body.decision, comment=body.comment))
    if body.decision == "approved":
        bp.status = "approved"
        lifecycle.transition(p, "scope_approved")
    else:
        lifecycle.transition(p, "onboarding")
    audit.record(db, actor=access.user.id, action=f"scope.{body.decision}", tenant_id=p.tenant_id, project_id=p.id,
                 resource=bp.id, details={"hash": bp.content_hash, "comment": body.comment})
    db.commit()
    return orchestrator.state_view(db, p)


def _build_view(b: Build) -> dict:
    return {"id": b.id, "number": b.number, "status": b.status, "branch": b.branch, "commit_sha": b.commit_sha,
            "checksum": b.checksum, "diff": b.diff, "test_report": b.test_report, "security_report": b.security_report,
            "gates_passed": release.gates_passed(b), "preview_url": f"/preview/{b.id}/",
            "pages": (b.site_config or {}).get("pages", []), "created_at": b.created_at.isoformat(),
            "critic": (b.site_config or {}).get("critic", []), "spec_version": (b.site_config or {}).get("spec_version")}


@router.post("/projects/{project_id}/builds")
def create_build(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("build.create")
    b = release.start_build(db, access.project, access.user.id, access.role)
    db.refresh(b)
    return _build_view(b)


@router.get("/projects/{project_id}/builds")
def list_builds(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    rows = db.scalars(select(Build).where(Build.project_id == access.project.id).order_by(Build.number.desc()).limit(20)).all()
    approvals = {a.resource_id: a for a in db.scalars(select(Approval).where(Approval.project_id == access.project.id,
                                                                            Approval.action == "release")).all()}
    return [_build_view(b) | {"approval": ({"decision": approvals[b.id].decision, "at": approvals[b.id].created_at.isoformat()}
                                           if b.id in approvals else None)} for b in rows]


class ReleaseDecisionIn(BaseModel):
    checksum: str = Field(min_length=64, max_length=64)
    decision: Literal["approved", "changes_requested"]
    comment: str | None = Field(default=None, max_length=1000)


def _get_build(db: Session, project: Project, build_id: str) -> Build:
    b = db.get(Build, build_id)
    if b is None or b.project_id != project.id or b.tenant_id != project.tenant_id:
        raise HTTPException(404, "Build not found.")
    return b


@router.post("/projects/{project_id}/builds/{build_id}/decision")
def decide_release(build_id: str, body: ReleaseDecisionIn, access: ProjectAccess = Depends(get_project_access),
                   db: Session = Depends(get_db)):
    b = _get_build(db, access.project, build_id)
    release.review_build(db, access.project, b, access.user.id, access.role, body.decision, body.checksum, body.comment)
    db.commit()
    return {"ok": True, "project": orchestrator.project_view(access.project)}


class DeployIn(BaseModel):
    build_id: str
    idempotency_key: str = Field(min_length=8, max_length=100)
    password: str | None = None


@router.post("/projects/{project_id}/deployments")
def deploy(body: DeployIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    require_reauth(db, access.principal, body.password)
    b = _get_build(db, access.project, body.build_id)
    dep = release.deploy(db, access.project, b, access.user.id, access.role, body.idempotency_key)
    db.commit()
    return _dep_view(dep) | {"project": orchestrator.project_view(access.project)}


class RollbackIn(BaseModel):
    password: str | None = None


@router.post("/projects/{project_id}/deployments/rollback")
def rollback(body: RollbackIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    require_reauth(db, access.principal, body.password)
    dep = release.rollback(db, access.project, access.user.id, access.role)
    db.commit()
    return _dep_view(dep) | {"project": orchestrator.project_view(access.project)}


def _dep_view(d: Deployment) -> dict:
    return {"id": d.id, "release_id": d.release_id, "status": d.status, "build_id": d.build_id, "health": d.health,
            "rollback_target": d.rollback_target, "deployed_at": d.deployed_at.isoformat() if d.deployed_at else None,
            "created_at": d.created_at.isoformat()}


@router.get("/projects/{project_id}/deployments")
def list_deployments(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    rows = db.scalars(select(Deployment).where(Deployment.project_id == access.project.id)
                      .order_by(Deployment.created_at.desc()).limit(30)).all()
    return {"live_url": f"/sites/{access.project.slug}/" if access.project.current_release_id else None,
            "deployments": [_dep_view(d) for d in rows]}


@router.get("/projects/{project_id}/health")
def health(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    p = access.project
    if not p.current_release_id:
        return {"live": False}
    dep = db.get(Deployment, p.current_release_id)
    b = db.get(Build, dep.build_id)
    return {"live": True, "release_id": dep.release_id,
            **hosting.health_check(p.slug, dep.release_id, b.checksum, b.site_config.get("pages", []))}


# ---------------------------------------------------------------- serving previews and screenshots

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
SITE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors {fa}")


def safe_file(root: Path, rel: str) -> Path:
    rel = rel or "index.html"
    if rel.endswith("/"):
        rel += "index.html"
    target = (root / rel).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(404, "Page not found.")
    return target


preview_router = APIRouter(tags=["preview"])


@preview_router.get("/preview/{build_id}/{path:path}")
def serve_preview(build_id: str, path: str, request: Request, db: Session = Depends(get_db)):
    sess = load_session(db, request)
    b = db.get(Build, build_id)
    if sess is None or b is None or b.status == "running" or not b.artifact_path:
        raise HTTPException(404, "Preview not found.")
    if membership_role(db, sess.user_id, b.tenant_id) is None:
        raise HTTPException(404, "Preview not found.")
    f = safe_file(Path(b.artifact_path), path)
    headers = SECURITY_HEADERS | {"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store",
                                  "Content-Security-Policy": SITE_CSP.format(fa="'self'")}
    return FileResponse(f, headers=headers)


@preview_router.get("/api/projects/{project_id}/builds/{build_id}/screenshots/{name}")
def screenshot(build_id: str, name: str, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    b = _get_build(db, access.project, build_id)
    _, reports = release.build_paths(access.project.id, b.id)
    return FileResponse(safe_file(reports, name), headers={"Cache-Control": "private, max-age=300"})


# ---------------------------------------------------------------- design conversation: change requests

class ChangeRequestIn(BaseModel):
    text: str = Field(min_length=3, max_length=1000)


@router.post("/projects/{project_id}/change-requests")
def change_request(body: ChangeRequestIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    """Owner asks for a change in plain English → Editor agent → validated spec patch → new preview build."""
    from ..agents import editor, team
    from ..agents.knowledge import Board
    from ..agents.llm import get_provider
    from ..agents.orchestrator import load_answers, run_agent, save_spec
    access.require("build.create")
    p = access.project
    bp = db.scalar(select(Blueprint).where(Blueprint.project_id == p.id, Blueprint.status == "approved")
                   .order_by(Blueprint.version.desc()))
    if bp is None or p.lifecycle_state not in release.BUILDABLE_STATES:
        raise HTTPException(409, "Approve your plan first; then you can ask for design changes on the preview.")
    current = orchestrator.latest_spec(db, p, bp.id)
    values, _ = load_answers(db, p)
    board = Board(db, p)
    library = (board.get("copy.library") or {})
    holder: dict = {}

    def edit():
        from ..agents.contracts import AgentResult
        out = editor.interpret(body.text, current.spec, library, team.owner_text(values), get_provider())
        holder.update(out)
        return AgentResult(agent="editor", status="ok" if out["ops"] else "needs_input",
                           summary="; ".join(out["summary"]) or "No change applied",
                           artifacts={"ops": out["ops"], "not_understood": out["not_understood"]}, risk_tier="medium")

    run_agent(db, p, "editor", edit)
    if not holder.get("ops"):
        db.commit()
        return {"applied": [], "not_understood": holder.get("not_understood") or [body.text], "build": None,
                "message": "I couldn't turn that into a change. Try things like “make the colours darker”, “move the FAQ "
                           "above services”, “show services as a list”, “rounder buttons” or “change the headline to …”."}
    row = save_spec(db, p, bp.id, holder["spec"], f"owner:{access.user.id}", body.text, holder["ops"])
    board.write("editor", "change", f"change.{row.version}", {"request": body.text, "applied": holder["summary"]},
                "Owner request interpreted into validated design changes.", 1.0, ["art.theme", "ia.home_sections"])
    audit.record(db, actor=access.user.id, action="design.change_request", tenant_id=p.tenant_id, project_id=p.id,
                 resource=row.id, details={"text": body.text, "applied": holder["summary"]})
    db.commit()
    b = release.start_build(db, p, access.user.id, access.role)
    db.refresh(b)
    return {"applied": holder["summary"], "not_understood": holder["not_understood"], "build": _build_view(b)}


@router.get("/projects/{project_id}/knowledge")
def knowledge(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    from ..agents.knowledge import Board
    return Board(db, access.project).trail()


@router.get("/projects/{project_id}/spec")
def spec_history(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    from ..models import SiteSpec
    from ..sitegen import spec as specmod
    rows = db.scalars(select(SiteSpec).where(SiteSpec.project_id == access.project.id).order_by(SiteSpec.version.desc()).limit(30)).all()
    return {"latest": ({"version": rows[0].version, "outline": specmod.outline(rows[0].spec), "theme": rows[0].spec["theme"]} if rows else None),
            "history": [{"version": r.version, "author": "you" if r.author.startswith("owner:") else r.author, "reason": r.reason,
                         "changes": r.changes, "created_at": r.created_at.isoformat()} for r in rows]}
