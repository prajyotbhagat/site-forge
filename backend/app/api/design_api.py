"""Colours & templates: recommendations from APIs, the owner's logo, a site they like, industry and colour theory."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config
from ..agents import orchestrator
from ..agents.knowledge import Board
from ..agents.playbooks import pick_playbook
from ..agents.team import STYLE_COMPAT, THEME_BASE, build_theme
from ..db import get_db
from ..deps import ProjectAccess, get_project_access
from ..models import Blueprint
from ..onboarding.schema import PALETTES
from ..services import colors, release
from ..sitegen.render import FONT_DIR, FONTS
from .release_api import _build_view, safe_file

router = APIRouter(tags=["design"])


def _current_primary(db: Session, access: ProjectAccess, values: dict) -> str | None:
    spec = orchestrator.latest_spec(db, access.project)
    if spec:
        return spec.spec["theme"]["primary"]
    pv = colors.parse_value(values.get("palette"))
    return pv[0] if pv else None


@router.get("/api/projects/{project_id}/palettes")
def palettes(seed: str | None = None, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    values, _ = orchestrator.load_answers(db, access.project)
    pb = pick_playbook(access.project.business_type, values.get("business_description") or "", values.get("site_category"))
    groups: list[dict] = []
    seed_hex = colors.normalize_hex(seed or "") if seed else None

    if values.get("logo"):
        path = config.settings.uploads_dir / access.project.tenant_id / values["logo"].removeprefix("upload://")
        logo = colors.from_image(path)
        if logo:
            groups.append({"source": "logo", "title": "From your logo", "palettes": logo})
            seed_hex = seed_hex or logo[0]["primary"]
    ref = Board(db, access.project).get("brand.reference")
    if values.get("reference_site") and ref:
        try:
            site = colors.analyse_site(values["reference_site"])
            if site["palettes"]:
                groups.append({"source": "website", "title": f"From {site['host']}", "palettes": site["palettes"]})
                seed_hex = seed_hex or site["palettes"][0]["primary"]
        except Exception:  # noqa: BLE001 - site may be down now; the earlier suggestion is still on record
            pass
    ind = colors.industry(pb.get("id"))
    if ind:
        groups.append({"source": "industry", "title": f"Popular for {pb['label']}", "palettes": ind})
    seed_hex = seed_hex or _current_primary(db, access, values) or (ind[0]["primary"] if ind else "#1f3a5f")
    groups.append({"source": "harmony", "title": f"Colour theory around {seed_hex}", "palettes": colors.harmonies(seed_hex), "seed": seed_hex})
    ext, notes = colors.external(seed_hex)
    if ext:
        groups.append({"source": "api", "title": "From colour APIs", "palettes": colors.dedupe(ext)})
    groups.append({"source": "preset", "title": "Classic presets", "palettes": [
        colors.make(f"preset-{k}", label, v["primary"], v["accent"], "preset", "A tried-and-tested combination.") | {"value": k}
        for k, (label, v) in PALETTES.items()]})
    return {"seed": seed_hex, "groups": groups, "api_status": notes or ["ok"] if colors.enabled_apis() else ["disabled"]}


class SiteIn(BaseModel):
    url: str = Field(min_length=3, max_length=500)


@router.post("/api/projects/{project_id}/palettes/from-website")
def from_website(body: SiteIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    access.require("onboarding.write")
    try:
        r = colors.analyse_site(body.url)
    except colors.UnsafeURL as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, "I couldn't reach that site right now. Try again, or pick colours another way.") from exc
    Board(db, access.project).write("brand_scout", "insight", "brand.reference",
                                    {k: r[k] for k in ("host", "colours_found", "fonts_found", "template")},
                                    "Colours and fonts read from a site you like.", 1.0, [])
    audit.record(db, actor=access.user.id, action="design.reference_site", tenant_id=access.project.tenant_id,
                 project_id=access.project.id, details={"host": r["host"]})
    db.commit()
    return r


@router.get("/api/projects/{project_id}/templates")
def templates(access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    values, _ = orchestrator.load_answers(db, access.project)
    pb = pick_playbook(access.project.business_type, values.get("business_description") or "", values.get("site_category"))
    compat = STYLE_COMPAT.get(values.get("style") or "modern", list(THEME_BASE))
    rec = pb.get("theme") if pb.get("theme") in compat else compat[0]
    spec = orchestrator.latest_spec(db, access.project)
    current = spec.spec["theme"]["id"] if spec else values.get("template")
    palette = values.get("palette") or "navy"
    if spec:
        palette = f"custom:{spec.spec['theme']['primary']}:{spec.spec['theme']['accent']}"
    out = []
    for tid, t in THEME_BASE.items():
        th = build_theme(tid, palette, values.get("style"))
        out.append({"id": tid, "name": t["name"], "mood": t["mood"], "recommended": tid == rec, "current": tid == current,
                    "fits_style": tid in compat, "theme": th,
                    "fonts": {"heading": {"family": FONTS[th["font_heading"]][0], "file": f"{th['font_heading']}-latin-{_w(th['font_heading'], th['heading_weight'])}-normal.woff2"},
                              "body": {"family": FONTS[th["font_body"]][0], "file": f"{th['font_body']}-latin-400-normal.woff2"}}})
    return {"templates": out, "business": values.get("business_name") or access.project.name,
            "headline": (spec.spec["pages"][0]["sections"][0]["content"].get("headline") if spec else None)}


def _w(fid: str, want: int) -> int:
    return min(FONTS[fid][1], key=lambda x: abs(x - want))


class DesignIn(BaseModel):
    palette: str | None = Field(default=None, max_length=40)
    template: str | None = Field(default=None, max_length=40)


@router.post("/api/projects/{project_id}/design")
def change_design(body: DesignIn, access: ProjectAccess = Depends(get_project_access), db: Session = Depends(get_db)):
    """After the plan is approved: switch template and/or colours → validated theme patch → new preview."""
    from ..sitegen import spec as specmod
    access.require("build.create")
    p = access.project
    bp = db.scalar(select(Blueprint).where(Blueprint.project_id == p.id, Blueprint.status == "approved")
                   .order_by(Blueprint.version.desc()))
    cur = orchestrator.latest_spec(db, p, bp.id) if bp else None
    if cur is None or p.lifecycle_state not in release.BUILDABLE_STATES:
        raise HTTPException(409, "Approve your plan first; then you can try other colours and designs on the preview.")
    theme = cur.spec["theme"]
    values: dict = {}
    said = []
    if body.template:
        if body.template not in THEME_BASE:
            raise HTTPException(422, "Unknown design.")
        nt = build_theme(body.template, f"custom:{theme['primary']}:{theme['accent']}", None)
        values.update({k: v for k, v in nt.items() if k in specmod.THEME_EDITABLE})
        said.append(f"Design → {THEME_BASE[body.template]['name']}")
    if body.palette:
        pv = colors.parse_value(body.palette.lower())
        if not pv:
            raise HTTPException(422, "Please choose a colour option or two valid colours.")
        base = build_theme(values.get("id", theme["id"]), body.palette.lower(), None)
        values.update({k: base[k] for k in ("primary", "accent", "on_accent", "dark", "surface", "bg", "text", "muted")})
        said.append(f"Colours → {base['primary']} / {base['accent']}")
    if not values:
        raise HTTPException(422, "Nothing to change.")
    ops = [{"op": "set_theme", "values": values}]
    if body.template:
        ops += _relayout(db, p, cur.spec, body.template)
        if len(ops) > 1:
            said.append(f"{len(ops) - 1} section layout(s) adapted to the new design (your own layout choices kept)")
    new_spec, _ = specmod.apply(cur.spec, ops)
    row = orchestrator.save_spec(db, p, bp.id, new_spec, f"owner:{access.user.id}", "; ".join(said), ops)
    Board(db, p).write("owner", "change", f"change.{row.version}", {"request": "; ".join(said), "applied": said},
                       "You chose this directly.", 1.0, [])
    audit.record(db, actor=access.user.id, action="design.change", tenant_id=p.tenant_id, project_id=p.id, details={"changes": said})
    db.commit()
    b = release.start_build(db, p, access.user.id, access.role)
    db.refresh(b)
    return {"applied": said, "build": _build_view(b)}


def _relayout(db: Session, p, spec: dict, template: str) -> list[dict]:
    """Ask the art director how this design would lay out each section; keep layouts the owner chose."""
    from ..agents import team
    from ..agents.design import cta_label
    from ..agents.llm import RulesProvider
    values, _ = orchestrator.load_answers(db, p)
    values = {**values, "template": template}
    strat = team.run_strategist(values, p.business_type, RulesProvider()).artifacts["strategy"]
    pages = [{"slug": pg["slug"], "sections": [s["type"] for s in pg["sections"]]} for pg in spec["pages"]]
    layout = team.run_art_director(values, {"strategy": strat, "cta_label": cta_label(values)}, pages).artifacts["layout"]
    ops = []
    for pg in spec["pages"]:
        for s in pg["sections"]:
            lay = layout.get(f"{pg['slug']}:{s['type']}")
            if not lay or "variant" in (s["props"].get("owner_locked") or []):
                continue
            if lay["variant"] != s["variant"]:
                ops.append({"op": "set_section_variant", "section": s["id"], "variant": lay["variant"]})
            for k, v in lay["props"].items():
                if s["props"].get(k) != v:
                    ops.append({"op": "set_section_prop", "section": s["id"], "key": k, "value": v})
    return ops


@router.get("/api/fonts/{name}")
def font(name: str):
    return FileResponse(safe_file(FONT_DIR, name), media_type="font/woff2", headers={"Cache-Control": "public, max-age=604800"})
