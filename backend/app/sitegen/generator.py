"""Deterministic site generator: approved blueprint → static site files.

Same blueprint in → byte-identical files out, so the artifact checksum is reproducible and approvals
can be bound to it.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import shutil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from .. import config

TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["j2", "html"]),
                   undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
_text_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=False, undefined=StrictUndefined,
                        keep_trailing_newline=True)

EYEBROWS = {"local_service": "Local service", "professional_service": "Professional services",
            "startup": "Now launching", "portfolio": "Portfolio"}


def _json_for_html(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def site_context(blueprint: dict, slug: str) -> dict:
    a = blueprint["business"]
    design = blueprint["design"]
    copy = blueprint["content"]["copy"]
    form = blueprint["data"].get("lead_form")
    feats = blueprint["features"]
    phone = a.get("phone") if ({"click_to_call", "whatsapp"} & set(feats) or a.get("primary_goal") == "calls") else None
    digits = re.sub(r"\D", "", phone or "")
    goal = a.get("primary_goal") or "enquiries"
    form_ctx = None
    if form:
        form_ctx = dict(form)
        form_ctx["endpoint"] = form["endpoint"].replace("{slug}", slug)
    return {
        "slug": slug,
        "name": a.get("business_name") or "Our business",
        "language": a.get("language") or "en",
        "style": a.get("style") or "modern",
        "nav": design["nav"],
        "home_sections": design["home_sections"],
        "cta_label": design["cta_label"],
        "goal": goal,
        "phone": phone,
        "phone_tel": ("+" if (phone or "").strip().startswith("+") else "") + digits,
        "phone_digits": digits,
        "whatsapp": "whatsapp" in feats and bool(phone),
        "sticky_call": "click_to_call" in feats or goal == "calls",
        "area": a.get("service_area"),
        "remote": a.get("remote_service") == "yes",
        "logo": "assets/logo" + Path(a["logo"]).suffix if a.get("logo") else None,
        "form": form_ctx,
        "form_heading": {"waitlist": "Join the waitlist", "bookings": "Request a booking"}.get(goal, "Send us an enquiry"),
        "form_intro": {"waitlist": "Leave your details and we'll let you know as soon as we launch.",
                       "bookings": "Tell us when suits you and we'll confirm a time."}.get(goal, "Tell us a little about what you need and we'll get back to you."),
        "submit_label": {"waitlist": "Join the waitlist", "bookings": "Send booking request"}.get(goal, "Send enquiry"),
        "form_field_labels": ", ".join(f["label"].lower() for f in form["fields"]) if form else "",
        "retention_days": form["retention_days"] if form else 0,
        "offer_label": {"startup": "Product", "portfolio": "Work"}.get(a.get("site_category") or "", "Services"),
        "has_services": "services" in design["pages"],
        "services_intro": "Here's what we can help with. Not sure which you need? Just ask.",
        "contact_line": copy.get("contact_line") or "Get in touch.",
        "eyebrow": (f"Serving {a['service_area']}" if a.get("service_area") and len(a["service_area"]) < 40
                    else EYEBROWS.get(a.get("site_category") or "", "Welcome")),
    }


def generate(blueprint: dict, slug: str, out_dir: Path, base_url: str, tenant_id: str) -> dict:
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets").mkdir(parents=True)
    site = site_context(blueprint, slug)
    copy = blueprint["content"]["copy"]
    meta_all = copy["meta"]
    pages = list(blueprint["design"]["pages"]) + list(blueprint["design"].get("extra_pages", []))
    tpl = _env.get_template("pages.html.j2")
    schema_json = _json_for_html(blueprint["content"]["schema_org"])
    written = []
    for page in pages:
        fname = "index.html" if page == "home" else f"{page}.html"
        html = tpl.render(site=site, copy=copy, page=page, meta=meta_all.get(page, meta_all["home"]),
                          schema_json=schema_json)
        html = re.sub(r"\n\s*\n+", "\n", html)
        (out_dir / fname).write_text(html)
        written.append(fname)
    (out_dir / "assets" / "site.css").write_text(_text_env.get_template("site.css.j2").render(t=blueprint["design"]["tokens"]))
    (out_dir / "assets" / "site.js").write_text(_text_env.get_template("site.js.j2").render())
    if blueprint["business"].get("logo"):
        src = config.settings.uploads_dir / tenant_id / blueprint["business"]["logo"].removeprefix("upload://")
        if src.exists():
            shutil.copyfile(src, out_dir / ("assets/logo" + src.suffix))
    public_pages = [p for p in written if p != "thanks.html"]
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n")
    urls = "".join(f"  <url><loc>{base_url}{'' if p == 'index.html' else p}</loc></url>\n" for p in public_pages)
    (out_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n')
    return {"pages": written, "site": {k: v for k, v in site.items() if k not in {"form"}}}


def checksum(dir_: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(p for p in dir_.rglob("*") if p.is_file()):
        h.update(str(f.relative_to(dir_)).encode() + b"\0")
        h.update(hashlib.sha256(f.read_bytes()).digest())
    return h.hexdigest()


def file_hashes(dir_: Path) -> dict[str, str]:
    return {str(f.relative_to(dir_)): hashlib.sha256(f.read_bytes()).hexdigest()
            for f in sorted(p for p in dir_.rglob("*") if p.is_file())}


def diff_dirs(old: Path | None, new: Path) -> dict:
    """File-level + line-level diff for review (the 'pull request' view)."""
    new_h = file_hashes(new)
    old_h = file_hashes(old) if old and old.exists() else {}
    added = sorted(set(new_h) - set(old_h))
    removed = sorted(set(old_h) - set(new_h))
    changed = sorted(k for k in set(new_h) & set(old_h) if new_h[k] != old_h[k])
    patches = {}
    for k in changed:
        if k.endswith((".html", ".css", ".js", ".xml", ".txt")):
            a = (old / k).read_text().splitlines()  # type: ignore[operator]
            b = (new / k).read_text().splitlines()
            lines = list(difflib.unified_diff(a, b, f"a/{k}", f"b/{k}", lineterm="", n=1))
            patches[k] = "\n".join(lines[:200])
    return {"added": added, "removed": removed, "changed": changed, "patches": patches,
            "summary": f"{len(added)} added, {len(changed)} changed, {len(removed)} removed"}
