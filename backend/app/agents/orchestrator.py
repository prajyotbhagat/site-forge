"""Onboarding Orchestrator (roadmap sections 4–5).

The single owner of the conversation and of canonical project state. Specialists only return typed
proposals; the orchestrator validates them, never lets them overwrite what the owner said, removes
duplicate questions and asks one clear question at a time.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Callable

from fastapi import HTTPException
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import audit, config, lifecycle
from ..models import AgentRun, Blueprint, OnboardingAnswer, Project, Requirement, SiteSpec, utcnow
from ..onboarding.schema import FIELD_MAP, SHORT_LABELS, STEPS, Field, ValidationError, all_fields, get_field, normalize
from ..security import redact
from . import backend_data, design, discovery, planning, security_privacy, team
from .knowledge import Board
from ..sitegen import spec as specmod
from .contracts import AgentResult
from .llm import get_provider

SKIP_THRESHOLD = 0.8
FREE_TEXT_FIELDS = {"business_description", "differentiators", "audience"}
POST_BRIEF_STATES = {"brief_ready", "scope_approved", "preview_ready", "changes_requested", "approved_for_release",
                     "live", "monitoring", "failed", "paused"}

# ---------------------------------------------------------------- state access


def load_answers(db: Session, project: Project) -> tuple[dict[str, Any], dict[str, dict]]:
    rows = db.scalars(select(OnboardingAnswer).where(OnboardingAnswer.project_id == project.id,
                                                     OnboardingAnswer.tenant_id == project.tenant_id,
                                                     OnboardingAnswer.is_current.is_(True))).all()
    values: dict[str, Any] = {}
    meta: dict[str, dict] = {}
    for r in rows:
        if r.source != "skipped":
            values[r.field_key] = r.value
        meta[r.field_key] = {"source": r.source, "confidence": r.confidence, "reason": r.reason,
                             "confirmed": r.confirmed_at is not None, "version": r.version,
                             "updated_by": r.updated_by}
    return values, meta


def _write(db: Session, project: Project, key: str, value: Any, source: str, confidence: float, actor: str,
           reason: str | None = None, confirmed: bool = False) -> OnboardingAnswer:
    prev_version = db.scalar(select(func.max(OnboardingAnswer.version)).where(
        OnboardingAnswer.project_id == project.id, OnboardingAnswer.field_key == key)) or 0
    db.execute(update(OnboardingAnswer).where(OnboardingAnswer.project_id == project.id,
                                              OnboardingAnswer.field_key == key,
                                              OnboardingAnswer.is_current.is_(True)).values(is_current=False))
    row = OnboardingAnswer(tenant_id=project.tenant_id, project_id=project.id, field_key=key, value=value,
                           source=source, confidence=confidence, reason=reason, updated_by=actor,
                           confirmed_at=utcnow() if confirmed else None, version=prev_version + 1, is_current=True)
    db.add(row)
    project.state_version += 1
    return row


def _is_settled(meta: dict | None, field: Field | None = None) -> bool:
    """Owner-provided, confirmed, skipped, or confidently extracted → don't ask again.

    Security-relevant fields (e.g. where enquiries are sent) always need explicit confirmation.
    """
    if not meta:
        return False
    if meta["source"] in {"user_provided", "skipped"} or meta["confirmed"]:
        return True
    if field is not None and field.impact == "security":
        return False
    return meta["source"] in {"extracted", "inferred"} and meta["confidence"] >= SKIP_THRESHOLD


# ---------------------------------------------------------------- agent execution


def run_agent(db: Session, project: Project, agent_type: str, fn: Callable[..., AgentResult], *args) -> AgentResult:
    used = db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.project_id == project.id)) or 0
    if used >= config.settings.agent_budget_runs_per_project:
        raise HTTPException(429, "This project has reached its automation budget. Please contact support.")
    t0 = time.perf_counter()
    status, err, out = "ok", None, None
    try:
        result = fn(*args)
        # Re-validate through the schema: malformed output is rejected even from our own code paths.
        result = AgentResult.model_validate(result.model_dump())
        out = result
        status = result.status
    except PydanticValidationError as exc:
        status, err = "error", "schema_validation"
        result = AgentResult(agent=agent_type, status="error", summary="Agent output failed validation.",
                             evidence=[str(exc)[:500]])
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - convert to a safe, recorded error
        status, err = "error", type(exc).__name__
        result = AgentResult(agent=agent_type, status="error", summary="The assistant hit a problem; we'll retry.",
                             evidence=[str(exc)[:300]])
    db.add(AgentRun(tenant_id=project.tenant_id, project_id=project.id, agent_type=agent_type,
                    input_version=project.state_version, status=status,
                    provider=get_provider().name if agent_type in {"business_discovery", "content_seo", "brand_strategist", "copywriter", "visual_critic", "editor"} else "deterministic",
                    output=redact((out or result).model_dump(mode="json")),
                    duration_ms=int((time.perf_counter() - t0) * 1000), error_category=err))
    return result


def apply_proposals(db: Session, project: Project, result: AgentResult, values: dict, meta: dict) -> list[dict]:
    """Validate specialist proposals against the schema and provenance rules before merging."""
    applied = []
    for ch in result.proposed_changes:
        field = get_field(ch.field_key, values)
        if field is None:
            continue  # out-of-scope field: rejected
        m = meta.get(ch.field_key)
        if m and (m["source"] in {"user_provided", "skipped"} or m["confirmed"]):
            continue  # never overwrite the owner's own answers
        if m and m["confidence"] >= ch.confidence:
            continue
        try:
            value = normalize(field, ch.value, {**values, ch.field_key: ch.value})
        except ValidationError:
            continue
        _write(db, project, ch.field_key, value, ch.source, ch.confidence, result.agent, ch.reason)
        values[ch.field_key] = value
        meta[ch.field_key] = {"source": ch.source, "confidence": ch.confidence, "reason": ch.reason,
                              "confirmed": False, "version": 0, "updated_by": result.agent}
        applied.append({"field_key": ch.field_key, "value": value, "confidence": ch.confidence, "reason": ch.reason})
    return applied


# ---------------------------------------------------------------- presentation helpers


def display_value(field: Field, value: Any, values: dict) -> str:
    if value is None:
        return "—"
    opts = {o.value: o.label for o in field.option_list(values)}
    if field.kind == "multi":
        return ", ".join(opts.get(v, v) for v in value)
    if field.kind == "list":
        return ", ".join(value)
    if field.kind == "choice":
        return opts.get(value, str(value))
    if field.kind == "upload":
        return "Uploaded"
    if field.kind == "palette":
        from ..services.colors import CUSTOM
        m = CUSTOM.match(str(value))
        return f"Custom {m.group(1)} / {m.group(2)}" if m else opts.get(value, str(value))
    if field.kind == "template":
        return opts.get(value, str(value))
    return str(value)


SOURCE_LABELS = {"user_provided": "You said", "extracted": "Found in your message", "inferred": "My guess — please check",
                 "recommended": "Recommended", "skipped": "Skipped"}


def question_payload(field: Field, values: dict, meta: dict) -> dict:
    m = meta.get(field.key)
    rec = None
    if field.recommend:
        try:
            r = field.recommend(values)
            if r:
                rec = {"value": r[0], "label": display_value(field, r[0], values), "reason": r[1]}
        except Exception:  # noqa: BLE001
            rec = None
    suggestion = None
    if m and m["source"] in {"inferred", "extracted"} and field.key in values:
        suggestion = {"value": values[field.key], "label": display_value(field, values[field.key], values),
                      "reason": m["reason"]}
    step_idx = next(i for i, s in enumerate(STEPS) if s["id"] == field.step)
    return {
        "field_key": field.key, "step": field.step, "step_index": step_idx, "prompt": field.prompt,
        "kind": field.kind, "why": field.why, "technical": field.technical, "placeholder": field.placeholder,
        "required": field.required, "allow_skip": field.allow_skip or not field.required,
        "options": [o.__dict__ for o in field.option_list(values)],
        "recommendation": rec, "suggestion": suggestion,
        "current": values.get(field.key),
    }


def next_field(values: dict, meta: dict) -> Field | None:
    for f in all_fields(values):
        if not f.is_relevant(values):
            continue
        if _is_settled(meta.get(f.key), f):
            continue
        return f
    return None


def progress(values: dict, meta: dict, project: Project) -> dict:
    relevant = [f for f in all_fields(values) if f.is_relevant(values)]
    done = [f for f in relevant if _is_settled(meta.get(f.key), f)]
    nf = next_field(values, meta)
    current_step = nf.step if nf else None
    state = project.lifecycle_state
    post = {"brief_ready": "review", "scope_approved": "build", "building": "build", "preview_ready": "build",
            "changes_requested": "build", "approved_for_release": "launch", "deploying": "launch", "live": "launch",
            "monitoring": "launch", "failed": "build"}.get(state)
    current = current_step or post or "review"
    order = [s["id"] for s in STEPS]
    steps = []
    for s in STEPS:
        fields = [f for f in relevant if f.step == s["id"]]
        status = "current" if s["id"] == current else ("done" if order.index(s["id"]) < order.index(current) else "upcoming")
        if state in {"live", "monitoring"}:
            status = "done"
        steps.append({**s, "status": status, "questions": len(fields)})
    return {"answered": len(done), "total": len(relevant), "percent": int(100 * len(done) / max(1, len(relevant))),
            "current_step": current, "steps": steps}


def facts(values: dict, meta: dict) -> list[dict]:
    out = []
    for f in all_fields(values):
        if f.key not in meta or not f.is_relevant(values):
            continue
        m = meta[f.key]
        out.append({"field_key": f.key, "step": f.step, "label": SHORT_LABELS.get(f.key, f.key), "prompt": f.prompt, "value": values.get(f.key),
                    "display": display_value(f, values.get(f.key), values) if m["source"] != "skipped" else "Skipped",
                    "source": m["source"], "source_label": SOURCE_LABELS.get(m["source"], m["source"]),
                    "confirmed": m["confirmed"], "needs_check": m["source"] in {"inferred", "extracted"} and not m["confirmed"],
                    "reason": m["reason"]})
    return out


def _say(project: Project, role: str, text: str) -> dict:
    msg = {"role": role, "text": text, "at": utcnow().isoformat()}
    project.transcript = (project.transcript or []) + [msg]
    return msg


# ---------------------------------------------------------------- main entry point


def state_view(db: Session, project: Project) -> dict:
    values, meta = load_answers(db, project)
    nf = next_field(values, meta)
    bp = latest_blueprint(db, project)
    return {
        "project": project_view(project),
        "question": question_payload(nf, values, meta) if nf and project.lifecycle_state in {"draft", "onboarding"} else None,
        "progress": progress(values, meta, project),
        "facts": facts(values, meta),
        "transcript": (project.transcript or [])[-60:],
        "blueprint": blueprint_view(bp) if bp else None,
    }


def project_view(p: Project) -> dict:
    return {"id": p.id, "name": p.name, "slug": p.slug, "lifecycle_state": p.lifecycle_state,
            "lifecycle_label": lifecycle.LABELS.get(p.lifecycle_state, p.lifecycle_state),
            "business_type": p.business_type, "current_release_id": p.current_release_id,
            "state_version": p.state_version}


def start(db: Session, project: Project) -> None:
    if not project.transcript:
        _say(project, "assistant", "Hi! I'll help you plan, build and launch your website — no technical knowledge "
                                   "needed. You can say “Recommend for me” or skip anything you're unsure about, and "
                                   "your progress saves automatically.")


def handle_answer(db: Session, project: Project, actor_id: str, field_key: str, action: str, value: Any) -> dict:
    values, meta = load_answers(db, project)
    field = get_field(field_key, values)
    if field is None:
        raise HTTPException(422, "Unknown question.")
    if not field.is_relevant(values):
        raise HTTPException(422, "That question doesn't apply to your project.")
    before = values.get(field_key)
    messages: list[dict] = []

    if action == "answer":
        try:
            value = normalize(field, value, values)
        except ValidationError as exc:
            raise HTTPException(422, str(exc)) from exc
        _write(db, project, field_key, value, "user_provided", 1.0, actor_id, confirmed=True)
        messages.append(_say(project, "user", display_value(field, value, values) if field.kind != "longtext" else value))
    elif action == "confirm":
        if field_key not in values:
            raise HTTPException(422, "There's nothing to confirm yet.")
        m = meta[field_key]
        _write(db, project, field_key, values[field_key], m["source"], m["confidence"], actor_id, m["reason"], confirmed=True)
        value = values[field_key]
        messages.append(_say(project, "user", f"Yes — {display_value(field, value, values)}"))
    elif action in {"recommend", "not_sure"}:
        rec = field.recommend(values) if field.recommend else None
        if not rec:
            if field.required:
                raise HTTPException(422, "I can't recommend an answer for this one — it's specific to you.")
            action = "skip"
        else:
            value = normalize(field, rec[0], values)
            reason = rec[1] if action == "recommend" else f"You weren't sure, so I picked a safe default. {rec[1]}"
            _write(db, project, field_key, value, "recommended", 0.9, actor_id, reason, confirmed=True)
            messages.append(_say(project, "user", "Recommend for me" if action == "recommend" else "I'm not sure"))
            messages.append(_say(project, "assistant", f"I've gone with **{display_value(field, value, values)}**. {rec[1]}"))
    if action == "skip":
        if field.required and not field.allow_skip:
            raise HTTPException(422, f"This one is needed: {field.why}")
        _write(db, project, field_key, None, "skipped", 1.0, actor_id, confirmed=True)
        messages.append(_say(project, "user", "Skip for now"))
        value = None
    elif action not in {"answer", "confirm", "recommend", "not_sure"}:
        raise HTTPException(422, "Unknown action.")

    values, meta = load_answers(db, project)

    # Free-text answers go to Business Discovery so the owner is never asked for something they already said.
    if action == "answer" and (field.key in FREE_TEXT_FIELDS or field.kind == "longtext"):
        result = run_agent(db, project, "business_discovery", discovery.run, str(value), values, get_provider())
        applied = apply_proposals(db, project, result, values, meta)
        if result.artifacts.get("business_type") and not project.business_type:
            project.business_type = result.artifacts["business_type"]
        if applied:
            noted = [f"{SHORT_LABELS.get(a['field_key'], a['field_key'])}: "
                     f"**{display_value(get_field(a['field_key'], values), a['value'], values)}**" for a in applied
                     if a["field_key"] != "services" or a["confidence"] >= SKIP_THRESHOLD]
            if noted:
                messages.append(_say(project, "assistant", "From what you said, I've noted:\n- " + "\n- ".join(noted)
                                     + "\nYou can change any of these on the right."))
        for r in result.risks:
            messages.append(_say(project, "assistant", f"{r.title}. {r.mitigation}"))
        values, meta = load_answers(db, project)

    if action == "answer" and field.key in {"reference_site", "logo"}:
        messages += _scout(db, project, actor_id, field.key, value, values, meta)
        values, meta = load_answers(db, project)

    messages += _conflict_notes(project, field, values, before)

    if project.lifecycle_state == "draft":
        lifecycle.transition(project, "onboarding")
    elif project.lifecycle_state in POST_BRIEF_STATES and before != values.get(field_key):
        lifecycle.transition(project, "onboarding")
        _supersede_blueprints(db, project)
        messages.append(_say(project, "assistant", "Got it — I've updated your plan. You'll review it again before "
                                                   "anything changes on your live site."))

    nf = next_field(values, meta)
    if nf is None and project.lifecycle_state == "onboarding":
        bp = build_blueprint(db, project)
        lifecycle.transition(project, "brief_ready")
        messages.append(_say(project, "assistant",
                             "That's everything I need. Your website plan is ready — please review what's included, "
                             "what's left for later and what data the site collects, then approve it to start building."))
        audit.record(db, actor=actor_id, action="blueprint.drafted", tenant_id=project.tenant_id,
                     project_id=project.id, resource=bp.id, details={"version": bp.version})
    elif nf is not None:
        messages.append(_say(project, "assistant", question_payload(nf, values, meta)["prompt"]))
    audit.record(db, actor=actor_id, action="onboarding.answer", tenant_id=project.tenant_id, project_id=project.id,
                 resource=field_key, details={"action": action})
    return {"messages": messages, **state_view(db, project)}


def _scout(db: Session, project: Project, actor_id: str, key: str, value: Any, values: dict, meta: dict) -> list[dict]:
    """Brand scout: turns a site the owner likes, or their logo, into colour and design suggestions."""
    from ..services import colors
    found: dict = {}

    def scout() -> AgentResult:
        changes, knowledge, notes = [], [], []
        if key == "reference_site":
            try:
                r = colors.analyse_site(str(value))
            except colors.UnsafeURL as exc:
                return AgentResult(agent="brand_scout", status="needs_input", summary=str(exc), evidence=[str(exc)])
            except Exception as exc:  # noqa: BLE001 - network problems are expected
                return AgentResult(agent="brand_scout", status="needs_input",
                                   summary=f"Couldn't read that site ({type(exc).__name__})", evidence=[str(exc)[:200]])
            found.update(r)
            if r["palettes"]:
                pal = r["palettes"][0]
                changes.append({"field_key": "palette", "value": pal["value"], "source": "extracted", "confidence": 0.75,
                                "reason": f"{pal['name']}: {pal['why']}"})
            if r["template"]:
                changes.append({"field_key": "template", "value": r["template"], "source": "extracted", "confidence": 0.75,
                                "reason": r["font_note"] or "Matches the type style of the site you like."})
            knowledge.append({"kind": "insight", "key": "brand.reference", "content": {k: r[k] for k in ("host", "colours_found", "fonts_found", "template")},
                              "rationale": "Colours and fonts read from the site you like (content is never copied).", "based_on": ["reference_site"]})
            notes.append(f"Read {r['host']}: {len(r['colours_found'])} colours, fonts {', '.join(r['fonts_found'][:2]) or 'not declared'}.")
        else:
            path = config.settings.uploads_dir / project.tenant_id / str(value).removeprefix("upload://")
            pals = colors.from_image(path)
            found["palettes"] = pals
            if pals:
                changes.append({"field_key": "palette", "value": pals[0]["value"], "source": "extracted", "confidence": 0.75,
                                "reason": f"{pals[0]['name']}: {pals[0]['why']}"})
                knowledge.append({"kind": "insight", "key": "brand.logo_colours", "content": [p["primary"] for p in pals] + [pals[0]["accent"]],
                                  "rationale": "Dominant colours in your logo.", "based_on": ["logo"]})
        return AgentResult(agent="brand_scout", summary=" ".join(notes) or f"{len(changes)} suggestion(s)",
                           proposed_changes=changes, artifacts={"knowledge": knowledge})

    res = run_agent(db, project, "brand_scout", scout)
    if res.status != "ok":
        return [_say(project, "assistant", f"I couldn't use that: {res.summary}. No problem — you can pick colours and a design in a moment.")]
    _learn(Board(db, project), res)
    applied = apply_proposals(db, project, res, values, meta)
    if not applied:
        return [_say(project, "assistant", "Thanks — I'll keep that in mind for your colours.")]
    what = []
    for a in applied:
        if a["field_key"] == "palette":
            what.append("colours from " + ("your logo" if key == "logo" else found.get("host", "that site")))
        elif a["field_key"] == "template":
            what.append("a matching design style")
    return [_say(project, "assistant", f"Nice — I've lined up {' and '.join(what)}. I'll show them as suggestions so you can confirm or change them.")]


def _conflict_notes(project: Project, field: Field, values: dict, before: Any) -> list[dict]:
    notes = []
    if field.key == "features":
        later = [v for v in (values.get("features") or []) if v in {"booking", "newsletter", "accounts", "payments"}]
        if later:
            notes.append(_say(project, "assistant",
                              "Some of those (" + ", ".join(later) + ") are planned for a later release. I've added them "
                              "to your plan; for now the enquiry form covers them — for example, booking requests by form."))
        if "contact_form" not in (values.get("features") or []) and not ({"click_to_call", "whatsapp"} & set(values.get("features") or [])):
            notes.append(_say(project, "assistant", "Without a form or call button visitors won't have a way to reach "
                                                    "you, so I'll still add a contact page."))
    if field.key == "site_category" and values.get("site_category") == "shop":
        notes.append(_say(project, "assistant", "Full online shops (cart, payments, stock) are coming in a later release. "
                                                "I'll build a product showcase with an enquiry form now and keep the shop in your plan."))
    if field.key == "primary_goal" and values.get("primary_goal") == "bookings":
        notes.append(_say(project, "assistant", "Online booking calendars arrive in a later release; I'll set up booking "
                                                "requests through your form so you can confirm times yourself."))
    return notes


# ---------------------------------------------------------------- blueprint


def latest_blueprint(db: Session, project: Project) -> Blueprint | None:
    return db.scalar(select(Blueprint).where(Blueprint.project_id == project.id, Blueprint.tenant_id == project.tenant_id)
                     .order_by(Blueprint.version.desc()).limit(1))


def _supersede_blueprints(db: Session, project: Project) -> None:
    db.execute(update(Blueprint).where(Blueprint.project_id == project.id, Blueprint.status != "superseded")
               .values(status="superseded"))


def canonical_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def _learn(board: Board, result: AgentResult) -> None:
    for k in result.artifacts.get("knowledge", []):
        board.write(result.agent, k["kind"], k["key"], k["content"], k.get("rationale", ""), k.get("confidence", 1.0),
                    k.get("based_on", []))


def build_blueprint(db: Session, project: Project) -> Blueprint:
    """Run the whole team. Each agent reads the shared board and writes its conclusions back to it."""
    values, meta = load_answers(db, project)
    provider = get_provider()
    board = Board(db, project)
    for f in facts(values, meta):  # 0. owner facts, with provenance, become the first entries on the board
        if f["source"] != "skipped":
            board.write("onboarding", "fact", f"fact.{f['field_key']}", f["value"], f["source_label"],
                        f.get("confidence", 1.0) if isinstance(f.get("confidence"), float) else 1.0)

    planner = run_agent(db, project, "requirements_planner", planning.run_planner, values)
    architect = run_agent(db, project, "platform_architect", planning.run_architect, values)
    backend = run_agent(db, project, "backend_data", backend_data.run_backend, values)
    regulated = None
    for run in db.scalars(select(AgentRun).where(AgentRun.project_id == project.id,
                                                 AgentRun.agent_type == "business_discovery")).all():
        regulated = (run.output or {}).get("artifacts", {}).get("regulated") or regulated
    sec = run_agent(db, project, "security_privacy", security_privacy.review_plan, values, regulated,
                    backend.artifacts.get("lead_form"))
    analytics = run_agent(db, project, "analytics_growth", backend_data.run_analytics, values)

    # creative team, in order, each building on the board
    strategist = run_agent(db, project, "brand_strategist", team.run_strategist, values, project.business_type, provider)
    _learn(board, strategist)
    shared = {**board.view(), "strategy": strategist.artifacts.get("strategy"), "cta_label": design.cta_label(values)}
    ia = run_agent(db, project, "information_architect", team.run_architect, values, shared, project.business_type)
    _learn(board, ia)
    shared = {**board.view(), "strategy": shared["strategy"], "cta_label": shared["cta_label"]}
    pages = ia.artifacts.get("pages", [])
    copywriter = run_agent(db, project, "copywriter", team.run_copywriter, values, shared, pages, provider)
    _learn(board, copywriter)
    if copywriter.status != "error":  # all drafted section content, so the editor can add sections later
        board.write("copywriter", "insight", "copy.library", copywriter.artifacts.get("content", {}),
                    "Content drafted for every possible section, kept for later change requests.", 1.0, [])
    art = run_agent(db, project, "art_director", team.run_art_director, values, shared, pages)
    _learn(board, art)
    agents = [planner, architect, backend, sec, analytics, strategist, ia, copywriter, art]
    if any(a.status == "error" for a in agents):
        raise HTTPException(503, "Some planning steps failed; please try again in a moment.")

    slug = project.slug
    spec = team.assemble_spec(values, slug, pages, copywriter.artifacts, art.artifacts, shared["cta_label"])
    spec = specmod.validate(spec)
    theme = spec["theme"]
    schema_org = {"@context": "https://schema.org",
                  "@type": "LocalBusiness" if values.get("site_category") in {"local_service", "professional_service"} else "Organization",
                  "name": values.get("business_name") or project.name}
    if values.get("service_area"):
        schema_org["areaServed"] = values["service_area"]
    if spec["site"].get("phone"):
        schema_org["telephone"] = spec["site"]["phone"]

    reqs = planner.artifacts["requirements"]
    assumptions = [{"field_key": f["field_key"], "label": f["label"], "value": f["display"], "why": f["reason"]}
                   for f in facts(values, meta) if f["needs_check"] or f["source"] == "recommended"]
    if any(s_["type"] == "process" for p in spec["pages"] for s_ in p["sections"]):
        assumptions.append({"field_key": "process", "label": "How it works (typical steps for your trade)",
                            "value": " → ".join(st["title"] for st in copywriter.artifacts["content"]["process"]["steps"]),
                            "why": "Drafted from the industry playbook — edit it in the preview if your process differs."})
    risks = [r.model_dump() | {"agent": a.agent} for a in agents for r in a.risks]
    public_business = {k: v for k, v in values.items() if k in FIELD_MAP or k.startswith("kb_")}
    home = next(p for p in spec["pages"] if p["slug"] == "index")
    content_bp = {
        "business": public_business,
        "business_type": project.business_type,
        "features": values.get("features") or [],
        "requirements": reqs,
        "scope": {"in_scope": [r for r in reqs if r["status"] == "in_scope"],
                  "deferred": [r for r in reqs if r["status"] == "deferred"],
                  "out_of_scope": [r for r in reqs if r["status"] == "out_of_scope"]},
        "strategy": strategist.artifacts.get("strategy"),
        "design": {"tokens": {k: theme[k] for k in ("primary", "accent", "bg", "surface", "text", "muted", "on_accent", "dark")},
                   "template": theme["name"], "theme_id": theme["id"], "fonts": [theme["font_heading"], theme["font_body"]],
                   "nav": spec["site"]["nav"], "cta_label": shared["cta_label"],
                   "extra_pages": [p["slug"] for p in spec["pages"] if p["slug"] in {"privacy", "thanks"}],
                   "pages": [p["slug"] for p in spec["pages"]],
                   "home_sections": [{"type": s_["type"], "variant": s_["variant"], "why": s_.get("why", "")} for s_ in home["sections"]],
                   "headline": home["sections"][0]["content"].get("headline")},
        "content": {"schema_org": schema_org, "notes": copywriter.evidence},
        "data": backend.artifacts,
        "security": sec.artifacts,
        "platform": architect.artifacts,
        "measurement": analytics.artifacts,
        "assumptions": assumptions,
        "risks": risks,
        "blocking": [r for r in risks if r["blocking"]],
        "agent_summaries": {a.agent: a.summary for a in agents},
    }
    _supersede_blueprints(db, project)
    version = (db.scalar(select(func.max(Blueprint.version)).where(Blueprint.project_id == project.id)) or 0) + 1
    bp = Blueprint(tenant_id=project.tenant_id, project_id=project.id, version=version, content=content_bp,
                   content_hash=canonical_hash(content_bp), status="draft")
    db.add(bp)
    db.flush()
    spec, reapplied = _reapply_owner_edits(db, project, spec)
    save_spec(db, project, bp.id, spec, "creative_team", "Drafted by the strategist, architect, copywriter and art director",
              [{"reapplied_owner_edits": reapplied}] if reapplied else [])
    db.query(Requirement).filter(Requirement.project_id == project.id).delete()
    for r in reqs:
        db.add(Requirement(tenant_id=project.tenant_id, project_id=project.id, blueprint_version=version, **r))
    project.selected_stack = architect.artifacts["stack"]
    db.flush()
    return bp


def latest_spec(db: Session, project: Project, blueprint_id: str | None = None) -> SiteSpec | None:
    q = select(SiteSpec).where(SiteSpec.project_id == project.id)
    if blueprint_id:
        q = q.where(SiteSpec.blueprint_id == blueprint_id)
    return db.scalar(q.order_by(SiteSpec.version.desc()).limit(1))


def save_spec(db: Session, project: Project, blueprint_id: str, spec: dict, author: str, reason: str,
              changes: list) -> SiteSpec:
    prev = latest_spec(db, project)
    row = SiteSpec(tenant_id=project.tenant_id, project_id=project.id, blueprint_id=blueprint_id,
                   version=(prev.version if prev else 0) + 1, parent_id=prev.id if prev else None,
                   spec=specmod.validate(spec), author=author, reason=reason, changes=changes)
    db.add(row)
    db.flush()
    return row


def _reapply_owner_edits(db: Session, project: Project, spec: dict) -> tuple[dict, int]:
    """When the plan is regenerated, the owner's earlier design requests are carried over where they still fit."""
    rows = db.scalars(select(SiteSpec).where(SiteSpec.project_id == project.id, SiteSpec.author.like("owner:%"))
                      .order_by(SiteSpec.version)).all()
    n = 0
    for r in rows:
        for op in r.changes:
            if not isinstance(op, dict) or "op" not in op:
                continue
            try:
                spec, _ = specmod.apply(spec, [op])
                n += 1
            except (ValueError, KeyError, TypeError):
                continue
    return spec, n


def blueprint_view(bp: Blueprint) -> dict:
    return {"id": bp.id, "version": bp.version, "status": bp.status, "content_hash": bp.content_hash,
            "content": bp.content, "created_at": bp.created_at.isoformat() if bp.created_at else None}
