"""Spec → static site. Deterministic: the same spec always yields byte-identical files."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader, StrictUndefined, select_autoescape

from .. import config
from . import art
from .spec import validate

HERE = Path(__file__).parent
FONT_DIR = HERE / "fonts"
FONTS = {
    "inter": ("Inter", [400, 600, 700, 800], "sans"), "manrope": ("Manrope", [400, 600, 700, 800], "sans"),
    "space-grotesk": ("Space Grotesk", [400, 600, 700], "sans"), "plus-jakarta-sans": ("Plus Jakarta Sans", [400, 600, 700, 800], "sans"),
    "nunito": ("Nunito", [400, 600, 700, 800], "sans"), "outfit": ("Outfit", [400, 600, 700, 800], "sans"),
    "fraunces": ("Fraunces", [400, 600, 700], "serif"), "source-serif-4": ("Source Serif 4", [400, 600, 700], "serif"),
    "dm-serif-display": ("DM Serif Display", [400], "serif"),
}

# optional section props/content fields may be absent → lenient lookups (text is still autoescaped)
_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["j2", "html"]),
                   undefined=ChainableUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
_env.globals.update(icon=art.icon, hero_art=art.hero_art, map_art=art.map_art, mesh=art.mesh)
_css_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=False, undefined=StrictUndefined,
                       trim_blocks=True, lstrip_blocks=True)


def _json_for_html(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _initials(name: str) -> str:
    words = [w for w in re.split(r"[\s&-]+", name) if w and w[0].isalnum()]
    return ("".join(w[0] for w in words[:2]) or "•").upper()


def _font_files(fid: str, want: list[int]) -> list[dict]:
    fam, weights, _ = FONTS[fid]
    out = []
    for w in sorted({min(weights, key=lambda x: abs(x - ww)) for ww in want}):
        out.append({"family": fam, "file": f"{fid}-latin-{w}-normal.woff2", "weight": w})
    return out


def render_site(spec: dict, out_dir: Path, base_url: str, tenant_id: str, form: dict | None, schema_org: dict) -> dict:
    spec = validate(spec)
    t, site = spec["theme"], dict(spec["site"])
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "assets" / "fonts").mkdir(parents=True)

    faces = _font_files(t["font_heading"], [t["heading_weight"], 800 if t["heading_weight"] >= 700 else 600]) + \
        _font_files(t["font_body"], [400, 600, 700])
    uniq = {f["file"]: f for f in faces}
    for f in uniq.values():
        shutil.copyfile(FONT_DIR / f["file"], out_dir / "assets" / "fonts" / f["file"])

    if form:
        form = dict(form, endpoint=form["endpoint"].replace("{slug}", site["slug"]))
    site.update({"form": form, "initials": _initials(site["name"]), "logo_src": None})
    if site.get("logo"):
        src = config.settings.uploads_dir / tenant_id / site["logo"].removeprefix("upload://")
        if src.exists():
            shutil.copyfile(src, out_dir / "assets" / ("logo" + src.suffix))
            site["logo_src"] = "assets/logo" + src.suffix

    fh, fb = FONTS[t["font_heading"]], FONTS[t["font_body"]]
    css = _css_env.get_template("v2/site.css.j2").render(
        t=t, font_faces=list(uniq.values()), fh=fh[0], fb=fb[0],
        fh_fallback="Georgia,'Times New Roman',serif" if fh[2] == "serif" else "system-ui,sans-serif",
        pattern_light=art.pattern_url(t["pattern"], t["primary"], 0.16),
        pattern_dark=art.pattern_url(t["pattern"] if t["pattern"] != "none" else "dots", "#ffffff", 0.09))
    (out_dir / "assets" / "site.css").write_text(css)
    (out_dir / "assets" / "site.js").write_text(_css_env.get_template("site.js.j2").render())

    preload = sorted({f["file"] for f in faces if f["family"] == fh[0]})[:1]
    tpl = _env.get_template("v2/page.html.j2")
    has_faq_page = any(p["slug"] == "faq" for p in spec["pages"])
    written = []
    for page in spec["pages"]:
        fname = "index.html" if page["slug"] == "index" else f"{page['slug']}.html"
        html = tpl.render(theme=t, site=site, page=page, schema_json=_json_for_html(schema_org), preload_fonts=preload,
                          has_faq_page=has_faq_page)
        (out_dir / fname).write_text(re.sub(r"\n\s*\n+", "\n", html))
        written.append(fname)
    public = [p for p in written if p != "thanks.html"]
    (out_dir / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {base_url}sitemap.xml\n")
    urls = "".join(f"  <url><loc>{base_url}{'' if p == 'index.html' else p}</loc></url>\n" for p in public)
    (out_dir / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{urls}</urlset>\n')
    return {"pages": written}
