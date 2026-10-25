"""Public surface of generated sites: live page serving, the Lead API and cookie-free event counters."""
from __future__ import annotations

import re
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..deps import load_session, membership_role
from ..models import Build, Deployment, Lead, Project, SiteEvent, utcnow
from ..onboarding.schema import EMAIL_RE, PHONE_RE
from ..security import anonymize_ip, limiter
from ..services import notify
from ..services.hosting import hosting
from .release_api import SECURITY_HEADERS, SITE_CSP, safe_file

router = APIRouter(tags=["public"])
ALLOWED_EVENTS = {"page_view", "cta_click", "form_submit", "call_click", "whatsapp_click"}
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _live(db: Session, slug: str) -> tuple[Project, Deployment, Build]:
    p = db.scalar(select(Project).where(Project.slug == slug))
    if p is None or not p.current_release_id or p.lifecycle_state == "archived":
        raise HTTPException(404, "Site not found.")
    dep = db.get(Deployment, p.current_release_id)
    return p, dep, db.get(Build, dep.build_id)


@router.get("/sites/{slug}/{path:path}")
def serve_live(slug: str, path: str, db: Session = Depends(get_db)):
    try:
        p, dep, _ = _live(db, slug)
    except HTTPException:
        return HTMLResponse("<!doctype html><title>Not found</title><h1>This site isn't published yet.</h1>", 404)
    root = hosting.release_path(p.slug, dep.release_id)
    f = safe_file(root, path)
    headers = SECURITY_HEADERS | {"Content-Security-Policy": SITE_CSP.format(fa="'none'"), "X-Frame-Options": "DENY",
                                  "Cache-Control": "public, max-age=60"}
    return FileResponse(f, headers=headers)


@router.get("/sites/{slug}")
def live_root(slug: str):
    return RedirectResponse(f"/sites/{slug}/", 308)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return origin is None
    return urlparse(origin).netloc == request.headers.get("host")


def _context(db: Session, request: Request, slug: str) -> tuple[Project, Build, bool, str]:
    """Decide whether a submission comes from a private preview (test lead) or the live site."""
    ref = urlparse(request.headers.get("referer", ""))
    m = re.match(r"^/preview/([a-f0-9]{32})/", ref.path or "")
    if m and (not ref.netloc or ref.netloc == request.headers.get("host")):
        b = db.get(Build, m.group(1))
        sess = load_session(db, request)
        p = db.get(Project, b.project_id) if b else None
        if not (b and p and p.slug == slug and sess and membership_role(db, sess.user_id, p.tenant_id)):
            raise HTTPException(404, "Form not found.")
        return p, b, True, f"/preview/{b.id}/"
    p, dep, b = _live(db, slug)
    return p, b, False, f"/sites/{slug}/"


def validate_lead(form: dict, data: dict) -> tuple[dict, dict]:
    clean: dict = {}
    errors: dict = {}
    for f in form["fields"]:
        raw = data.get(f["key"], "")
        val = _CTRL.sub("", str(raw)).strip() if raw is not None else ""
        if len(val) > f["max"]:
            errors[f["key"]] = f"Please keep this under {f['max']} characters."
            continue
        if not val:
            if f["required"]:
                errors[f["key"]] = f"Please enter your {f['label'].lower()}."
            continue
        if f["type"] == "email" and not EMAIL_RE.match(val):
            errors[f["key"]] = "Please enter a valid email address."
        elif f["type"] == "tel" and not PHONE_RE.match(val):
            errors[f["key"]] = "Please enter a valid phone number."
        elif f["type"] == "select" and val not in f.get("options", []):
            errors[f["key"]] = "Please choose one of the options."
        else:
            clean[f["key"]] = val
    if form.get("contact_rule") == "email_or_phone" and not (clean.get("email") or clean.get("phone")) \
            and "email" not in errors and "phone" not in errors:
        errors["email"] = "Please give an email or a phone number so we can reply."
    return clean, errors


@router.post("/api/public/sites/{slug}/leads")
async def submit_lead(slug: str, request: Request, db: Session = Depends(get_db)):
    wants_json = "application/json" in request.headers.get("accept", "") or \
        "application/json" in request.headers.get("content-type", "")
    if not _same_origin(request):
        raise HTTPException(403, "Cross-site form submissions are not accepted.")
    ip = anonymize_ip(request.client.host if request.client else None)
    if not limiter.allow(f"lead:{slug}:{ip}", 5, 600):
        raise HTTPException(429, "Too many messages from this device. Please try again later or call us.")
    if "application/json" in request.headers.get("content-type", ""):
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(400, "Invalid request.")
        if not isinstance(data, dict):
            raise HTTPException(400, "Invalid request.")
    else:
        data = dict(await request.form())
    p, b, is_test, base = _context(db, request, slug)
    form = (b.site_config or {}).get("lead_form")
    if not form:
        raise HTTPException(404, "This site has no form.")

    def ok():
        if wants_json:
            return JSONResponse({"ok": True})
        return RedirectResponse(base + "thanks.html", 303)

    if str(data.get("website", "")).strip():  # honeypot: pretend success, store nothing
        return ok()
    ts = str(data.get("ts", "")).strip()
    if ts.isdigit() and time.time() - int(ts) < form["spam_controls"]["min_fill_seconds"]:
        raise HTTPException(429, "That was quick! Please wait a moment and send again.")
    clean, errors = validate_lead(form, data)
    if errors:
        if wants_json:
            return JSONResponse({"ok": False, "errors": errors}, 422)
        raise HTTPException(422, " ".join(errors.values()))
    lead = Lead(tenant_id=p.tenant_id, project_id=p.id, fields=clean, is_test=is_test, ip_hash=ip,
                source_release=None if is_test else p.current_release_id,
                delete_after=utcnow() + timedelta(days=int(form["retention_days"])))
    db.add(lead)
    db.flush()
    to = (b.site_config or {}).get("notify_to")
    if to:
        lines = "\n".join(f"{f['label']}: {clean.get(f['key'], '—')}" for f in form["fields"])
        notify.queue(db, tenant_id=p.tenant_id, project_id=p.id, to=to, key=f"lead:{lead.id}",
                     subject=("[TEST] " if is_test else "") + f"New enquiry from {clean.get('name', 'your website')}",
                     body=f"You have a new enquiry from your website.\n\n{lines}\n\nReply directly to the customer. "
                          f"This enquiry is stored for {form['retention_days']} days.")
    audit.record(db, actor="website-visitor", action="lead.create", tenant_id=p.tenant_id, project_id=p.id,
                 resource=lead.id, details={"test": is_test})
    db.commit()
    return ok()


@router.post("/api/public/sites/{slug}/events")
async def track_event(slug: str, request: Request, db: Session = Depends(get_db)):
    if not _same_origin(request):
        return JSONResponse({"ok": False}, 403)
    ref = urlparse(request.headers.get("referer", ""))
    if (ref.path or "").startswith("/preview/"):
        return JSONResponse({"ok": True, "ignored": "preview"})
    ip = anonymize_ip(request.client.host if request.client else None)
    if not limiter.allow(f"evt:{slug}:{ip}", 120, 600):
        return JSONResponse({"ok": False}, 429)
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse({"ok": False}, 400)
    event = str((body or {}).get("event", ""))
    if event not in ALLOWED_EVENTS:
        return JSONResponse({"ok": False}, 422)
    p = db.scalar(select(Project).where(Project.slug == slug))
    if p is None or not p.current_release_id:
        return JSONResponse({"ok": False}, 404)
    day = utcnow().strftime("%Y-%m-%d")
    row = db.scalar(select(SiteEvent).where(SiteEvent.project_id == p.id, SiteEvent.day == day, SiteEvent.event == event))
    if row is None:
        row = SiteEvent(tenant_id=p.tenant_id, project_id=p.id, day=day, event=event, count=0)
        db.add(row)
    row.count += 1
    db.commit()
    return JSONResponse({"ok": True})
