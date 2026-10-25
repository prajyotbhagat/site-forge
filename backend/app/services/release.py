"""Build → QA/security gates → approval → deploy → health check → rollback (roadmap phases 4–7)."""
from __future__ import annotations

import shutil
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, config, lifecycle, policy
from ..agents import critic, orchestrator, qa, security_privacy
from ..agents.knowledge import Board
from ..agents.llm import get_provider
from ..sitegen.render import render_site
from ..agents.contracts import AgentResult, Risk
from ..agents.orchestrator import run_agent
from ..db import SessionLocal
from ..models import Approval, Blueprint, Build, Deployment, Project, SiteSpec, as_utc, utcnow
from ..sitegen import generator
from . import jobs
from .hosting import hosting

BUILDABLE_STATES = {"scope_approved", "preview_ready", "changes_requested", "failed", "live", "monitoring",
                    "approved_for_release"}


def public_base_url(slug: str) -> str:
    import os
    return os.getenv("SITEFORGE_PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/") + f"/sites/{slug}/"


def build_paths(project_id: str, build_id: str) -> tuple[Path, Path]:
    root = config.settings.builds_dir / project_id / build_id
    return root / "site", root / "reports"


def start_build(db: Session, project: Project, actor_id: str, role: str) -> Build:
    decision = policy.evaluate("generate_preview", role=role)
    if not decision.allowed:
        raise HTTPException(403, " ".join(decision.reasons))
    running = db.scalar(select(Build).where(Build.project_id == project.id, Build.status == "running"))
    if running:
        return running  # idempotent: one build at a time per project
    bp = db.scalar(select(Blueprint).where(Blueprint.project_id == project.id, Blueprint.status == "approved")
                   .order_by(Blueprint.version.desc()))
    if bp is None or project.lifecycle_state not in BUILDABLE_STATES:
        raise HTTPException(409, "Please approve your website plan before building.")
    number = (db.scalar(select(func.max(Build.number)).where(Build.project_id == project.id)) or 0) + 1
    spec_row = orchestrator.latest_spec(db, project, bp.id)
    build = Build(tenant_id=project.tenant_id, project_id=project.id, number=number, blueprint_id=bp.id,
                  spec_id=spec_row.id if spec_row else None,
                  branch=f"sf/{project.slug}/build-{number}", commit_sha="", artifact_path="", checksum="",
                  status="running")
    db.add(build)
    lifecycle.transition(project, "building")
    audit.record(db, actor=actor_id, action="build.started", tenant_id=project.tenant_id, project_id=project.id,
                 resource=build.id, details={"branch": build.branch, "blueprint_version": bp.version})
    db.commit()
    jobs.submit(_run_build, build.id)
    return build


def _run_build(build_id: str) -> None:
    db = SessionLocal()
    try:
        build = db.get(Build, build_id)
        project = db.get(Project, build.project_id)
        bp = db.get(Blueprint, build.blueprint_id)
        site_dir, reports_dir = build_paths(project.id, build.id)
        try:
            spec_row = db.get(SiteSpec, build.spec_id) if build.spec_id else orchestrator.latest_spec(db, project, bp.id)
            lead_form = bp.content["data"].get("lead_form")
            schema_org = bp.content["content"]["schema_org"]
            base = public_base_url(project.slug)

            def render(spec: dict, out: Path) -> dict:
                return render_site(spec, out, base, project.tenant_id, lead_form, schema_org)

            # Visual critic: render → measure in a real browser → fix → re-render (up to 3 rounds)
            board = Board(db, project)
            spec = spec_row.spec
            critic_report: list = []
            if config.settings.browser_qa != "off":
                work = reports_dir / "critic"
                res = run_agent(db, project, "visual_critic", lambda: _critic(spec, render, work, board, get_provider()))
                critic_report = res.artifacts.get("rounds", [])
                if res.artifacts.get("spec") and res.artifacts["spec"] != spec:
                    spec = res.artifacts["spec"]
                    applied = [f for r in critic_report for f in r["fixes"]]
                    spec_row = orchestrator.save_spec(db, project, bp.id, spec, "visual_critic",
                                                      f"Auto-adjusted {len(applied)} layout issue(s) after measuring the rendered site", applied)
                    for i, fx in enumerate(applied):
                        board.write("visual_critic", "critique", f"critic.{spec_row.version}.{i}",
                                    {"issue": fx["issue"], "where": fx["where"], "fix": fx["fix"]}, fx.get("detail") or "", 1.0,
                                    ["art.layouts", "copy.headline"])
            build.spec_id = spec_row.id
            meta = render(spec, site_dir)
            build.artifact_path = str(site_dir)
            build.checksum = generator.checksum(site_dir)
            build.commit_sha = build.checksum[:40]
            build.site_config = {"pages": meta["pages"], "lead_form": lead_form,
                                 "notify_to": bp.content["business"].get("contact_email"),
                                 "blueprint_version": bp.version, "spec_version": spec_row.version, "critic": critic_report}
            prev = db.scalar(select(Build).where(Build.project_id == project.id, Build.number < build.number,
                                                 Build.status.in_(["passed", "blocked"]))
                             .order_by(Build.number.desc()))
            build.diff = generator.diff_dirs(Path(prev.artifact_path) if prev else None, site_dir)

            def qa_agent():
                report = qa.run(site_dir, reports_dir, spec["theme"])
                return AgentResult(agent="qa_accessibility", status="ok" if report["passed"] else "blocked",
                                   summary=f"{report['total']} checks, {report['failed']} blocking, {report['warnings']} warnings.",
                                   artifacts={"report": report})

            def sec_agent():
                private = [bp.content["business"].get("contact_email") or ""]
                report = security_privacy.review_artifact(site_dir, private)
                return AgentResult(agent="security_privacy", status="ok" if report["passed"] else "blocked",
                                   summary=f"Scanned {report['files_scanned']} files; {report['blocking']} blocking finding(s).",
                                   artifacts={"report": report},
                                   risks=[Risk(id=f["id"], severity=f["severity"], title=f["detail"][:120],
                                               blocking=f["severity"] in {"critical", "high"})
                                          for f in report["findings"]][:50])

            qa_res = run_agent(db, project, "qa_accessibility", qa_agent)
            sec_res = run_agent(db, project, "security_privacy", sec_agent)
            build.test_report = qa_res.artifacts.get("report", {"passed": False, "error": qa_res.summary})
            build.security_report = sec_res.artifacts.get("report", {"passed": False, "error": sec_res.summary})
            gates = build.test_report.get("passed") and build.security_report.get("passed") and not bp.content.get("blocking")
            build.status = "passed" if gates else "blocked"
            lifecycle.transition(project, "preview_ready")
            audit.record(db, actor="system", action="build.completed", tenant_id=project.tenant_id,
                         project_id=project.id, resource=build.id,
                         details={"status": build.status, "checksum": build.checksum})
        except Exception as exc:  # noqa: BLE001
            build.status = "failed"
            build.test_report = {"passed": False, "error": f"Build failed: {type(exc).__name__}"}
            if lifecycle.can_transition(project.lifecycle_state, "failed"):
                lifecycle.transition(project, "failed")
            audit.record(db, actor="system", action="build.completed", outcome="failure", tenant_id=project.tenant_id,
                         project_id=project.id, resource=build.id, details={"error": str(exc)[:300]})
        db.commit()
    finally:
        db.close()


def _critic(spec: dict, render, work: Path, board: Board, provider) -> AgentResult:
    final, rounds = critic.review_loop(spec, render, work, board.view_values(), provider)
    found = sum(len(r["issues"]) for r in rounds)
    fixed = sum(len(r["fixes"]) for r in rounds)
    return AgentResult(agent="visual_critic", summary=f"{len(rounds)} review round(s): {found} issue(s) measured, {fixed} fix(es) applied.",
                       artifacts={"spec": final, "rounds": rounds})


def gates_passed(build: Build) -> bool:
    return build.status == "passed" and bool(build.test_report.get("passed")) and bool(build.security_report.get("passed"))


def review_build(db: Session, project: Project, build: Build, actor_id: str, role: str, decision: str,
                 checksum: str, comment: str | None) -> Approval:
    if not policy.has_permission(role, "release.approve"):
        raise HTTPException(403, "Only an Owner or Admin can approve a release.")
    if build.status == "running":
        raise HTTPException(409, "This build is still running.")
    if checksum != build.checksum:
        raise HTTPException(409, "This preview has changed since you opened it. Please review the latest version.")
    if decision == "approved" and not gates_passed(build):
        raise HTTPException(409, "This build has blocking QA or security findings and can't be approved.")
    appr = Approval(tenant_id=project.tenant_id, project_id=project.id, action="release", resource_id=build.id,
                    diff_hash=build.checksum, approver_id=actor_id, decision=decision, comment=comment,
                    expires_at=utcnow() + timedelta(hours=config.settings.approval_ttl_hours))
    db.add(appr)
    lifecycle.transition(project, "approved_for_release" if decision == "approved" else "changes_requested")
    audit.record(db, actor=actor_id, action=f"release.{decision}", tenant_id=project.tenant_id, project_id=project.id,
                 resource=build.id, details={"checksum": build.checksum, "comment": comment})
    return appr


def _valid_approval(db: Session, build: Build) -> Approval | None:
    appr = db.scalar(select(Approval).where(Approval.resource_id == build.id, Approval.action == "release")
                     .order_by(Approval.created_at.desc()))
    if appr is None or appr.decision != "approved" or appr.diff_hash != build.checksum:
        return None
    if appr.expires_at and as_utc(appr.expires_at) < utcnow():
        return None
    return appr


def deploy(db: Session, project: Project, build: Build, actor_id: str, role: str, idempotency_key: str) -> Deployment:
    existing = db.scalar(select(Deployment).where(Deployment.idempotency_key == idempotency_key))
    if existing:
        if existing.project_id != project.id:
            raise HTTPException(409, "Idempotency key already used.")
        return existing
    appr = _valid_approval(db, build)
    artifact_intact = bool(build.artifact_path) and Path(build.artifact_path).exists() and \
        generator.checksum(Path(build.artifact_path)) == build.checksum
    decision = policy.evaluate("deploy_production", role=role, context={
        "gates_passed": gates_passed(build), "approval_valid": appr is not None, "artifact_intact": artifact_intact})
    if not decision.allowed:
        audit.record(db, actor=actor_id, action="deploy.denied", outcome="denied", tenant_id=project.tenant_id,
                     project_id=project.id, resource=build.id, details={"reasons": decision.reasons})
        db.commit()
        raise HTTPException(403, " ".join(decision.reasons))

    previous = db.scalar(select(Deployment).where(Deployment.project_id == project.id, Deployment.status == "live"))
    n = (db.scalar(select(func.count()).select_from(Deployment).where(Deployment.project_id == project.id)) or 0) + 1
    dep = Deployment(tenant_id=project.tenant_id, project_id=project.id, build_id=build.id,
                     release_id=f"r{n}-{build.checksum[:8]}", status="deploying",
                     rollback_target=previous.id if previous else None, idempotency_key=idempotency_key,
                     approval_id=appr.id, deployed_by=actor_id)
    db.add(dep)
    lifecycle.transition(project, "deploying")
    db.flush()

    hosting.upload(project.slug, dep.release_id, Path(build.artifact_path))
    project.current_release_id = dep.id  # switch traffic
    health = hosting.health_check(project.slug, dep.release_id, build.checksum, build.site_config.get("pages", []))
    dep.health = health
    if health["healthy"]:
        dep.status = "live"
        dep.deployed_at = utcnow()
        if previous:
            previous.status = "superseded"
        lifecycle.transition(project, "live")
        audit.record(db, actor=actor_id, action="deploy.live", tenant_id=project.tenant_id, project_id=project.id,
                     resource=dep.id, details={"release": dep.release_id, "checksum": build.checksum,
                                               "approval": appr.id, "rollback_target": dep.rollback_target})
    else:
        dep.status = "rolled_back"
        project.current_release_id = previous.id if previous else None
        lifecycle.transition(project, "live" if previous else "failed")
        audit.record(db, actor="system", action="deploy.auto_rollback", outcome="failure", tenant_id=project.tenant_id,
                     project_id=project.id, resource=dep.id, details={"health": health})
    run_agent(db, project, "deployment_ops", lambda: AgentResult(
        agent="deployment_ops", status="ok" if health["healthy"] else "blocked",
        summary=f"Release {dep.release_id}: {'live' if health['healthy'] else 'health check failed, rolled back'}.",
        artifacts={"release_id": dep.release_id, "health": health, "rollback_target": dep.rollback_target},
        risk_tier="high", requires_human_approval=True))
    return dep


def rollback(db: Session, project: Project, actor_id: str, role: str) -> Deployment:
    decision = policy.evaluate("rollback_production", role=role)
    if not decision.allowed:
        raise HTTPException(403, " ".join(decision.reasons))
    current = db.get(Deployment, project.current_release_id) if project.current_release_id else None
    if current is None or not current.rollback_target:
        raise HTTPException(409, "There's no earlier release to go back to.")
    target = db.get(Deployment, current.rollback_target)
    tbuild = db.get(Build, target.build_id)
    health = hosting.health_check(project.slug, target.release_id, tbuild.checksum, tbuild.site_config.get("pages", []))
    if not health["healthy"]:
        raise HTTPException(409, "The earlier release failed its health check; rollback stopped.")
    lifecycle.transition(project, "deploying")
    current.status = "rolled_back"
    target.status = "live"
    project.current_release_id = target.id
    lifecycle.transition(project, "live")
    audit.record(db, actor=actor_id, action="deploy.rollback", tenant_id=project.tenant_id, project_id=project.id,
                 resource=target.id, details={"from": current.release_id, "to": target.release_id})
    return target


def delete_build_files(project_id: str) -> None:
    shutil.rmtree(config.settings.builds_dir / project_id, ignore_errors=True)
