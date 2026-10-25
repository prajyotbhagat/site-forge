"""Visual Critic + Layout Fixer: render → measure in a real browser → adjust the spec → re-render.

The critic measures what a designer would look at: does anything overflow or get clipped, does the
headline wrap into a wall of text on a phone, is the main button visible without scrolling, are there
orphaned cards or wildly uneven card heights, is text contrast readable, and is the same text repeated.
Every issue maps to a concrete, validated spec patch. With a vision-capable model configured, screenshots
are also reviewed by the model and its suggestions go through the same patch validation.
"""
from __future__ import annotations

import base64
import functools
import http.server
import json
import logging
import socketserver
import threading
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from ..sitegen import spec as specmod
from .llm import LLMProvider, ProviderUnavailable

log = logging.getLogger("siteforge.critic")

MEASURE_JS = r"""
(vp) => {
  const issues = [];
  const secOf = el => { const s = el.closest('section[id]'); return s ? s.id : null; };
  const doc = document.documentElement;
  if (doc.scrollWidth - innerWidth > 1) {
    let worst = null, w = 0;
    document.querySelectorAll('main *').forEach(el => { const r = el.getBoundingClientRect(); if (r.right - innerWidth > w) { w = r.right - innerWidth; worst = el; } });
    issues.push({code: 'overflow_x', section: worst ? secOf(worst) : null, detail: `page is ${doc.scrollWidth - innerWidth}px wider than the screen`});
  }
  document.querySelectorAll('main h1, main h2, main h3, main p, main a.btn').forEach(el => {
    if (el.scrollWidth - el.clientWidth > 2 && getComputedStyle(el).overflowX === 'visible' && el.clientWidth > 0)
      issues.push({code: 'text_clipped', section: secOf(el), detail: el.textContent.trim().slice(0, 60)});
  });
  const h1 = document.querySelector('main h1');
  if (h1) {
    const lh = parseFloat(getComputedStyle(h1).lineHeight) || 1;
    const lines = Math.round(h1.getBoundingClientRect().height / lh);
    if ((vp === 'mobile' && lines > 4) || (vp === 'desktop' && lines > 3))
      issues.push({code: 'headline_too_long', section: secOf(h1), detail: `${lines} lines on ${vp}`, lines});
  }
  const heroBtn = document.querySelector('.hero .actions .btn');
  if (heroBtn && vp === 'mobile') {
    const b = heroBtn.getBoundingClientRect().bottom + scrollY;
    if (b > innerHeight - 70) issues.push({code: 'cta_below_fold', section: secOf(heroBtn), detail: `main button ends at ${Math.round(b)}px; screen is ${innerHeight}px`});
  }
  if (vp === 'desktop') document.querySelectorAll('.cards').forEach(g => {
    const cards = [...g.children]; if (cards.length < 2) return;
    const tops = cards.map(c => Math.round(c.getBoundingClientRect().top));
    const rows = {}; tops.forEach(t => rows[t] = (rows[t] || 0) + 1);
    const counts = Object.values(rows);
    if (counts.length > 1 && counts[counts.length - 1] === 1 && counts[0] > 1 && !g.classList.contains('bento'))
      issues.push({code: 'orphan_card', section: secOf(g), detail: `${cards.length} cards leave one alone on the last row`, n: cards.length});
    const hs = cards.map(c => c.getBoundingClientRect().height);
    if (Math.max(...hs) / Math.min(...hs) > 1.7) issues.push({code: 'uneven_cards', section: secOf(g), detail: `tallest card is ${(Math.max(...hs) / Math.min(...hs)).toFixed(1)}× the shortest`});
  });
  const lum = c => { const m = c.match(/[\d.]+/g); if (!m) return 1; const [r, g, b] = m.slice(0, 3).map(v => { v = v / 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; }); return .2126 * r + .7152 * g + .0722 * b; };
  const bgOf = el => { while (el) { const s = getComputedStyle(el); const c = s.backgroundColor; if (s.backgroundImage.includes('gradient') && el.tagName === 'SECTION') return null; if (c && !c.startsWith('rgba(0, 0, 0, 0)') && c !== 'transparent') { const a = c.match(/[\d.]+/g); if (!a[3] || +a[3] > .9) return c; } el = el.parentElement; } return 'rgb(255,255,255)'; };
  const seen = new Set();
  document.querySelectorAll('main h1, main h2, main h3, main p, main li, main a, main dt, main dd, main label').forEach(el => {
    if (!el.textContent.trim() || el.closest('.hp') || el.closest('svg')) return;
    const bg = bgOf(el); if (!bg) return;
    const fg = getComputedStyle(el).color; const L1 = lum(fg), L2 = lum(bg);
    const ratio = (Math.max(L1, L2) + .05) / (Math.min(L1, L2) + .05);
    const big = parseFloat(getComputedStyle(el).fontSize) >= 24;
    if (ratio < (big ? 3 : 4.5)) { const key = secOf(el) + fg + bg; if (!seen.has(key)) { seen.add(key); issues.push({code: 'low_contrast', section: secOf(el), detail: `${ratio.toFixed(2)}:1 for “${el.textContent.trim().slice(0, 40)}”`}); } }
  });
  const texts = {};
  document.querySelectorAll('main section[id] p, main section[id] li').forEach(el => {
    const t = el.textContent.trim(); if (t.length < 18) return; const s = secOf(el);
    texts[t] = texts[t] || new Set(); texts[t].add(s);
  });
  Object.entries(texts).forEach(([t, s]) => { if (s.size > 1) issues.push({code: 'repeated_text', section: [...s][1], sections: [...s], detail: t.slice(0, 70), text: t}); });
  return issues;
}
"""


class _Quiet(http.server.SimpleHTTPRequestHandler):
    """Serves the build with the same security headers as production, so header problems surface here."""

    def end_headers(self):
        from ..api.release_api import SITE_CSP
        self.send_header("Content-Security-Policy", SITE_CSP.format(fa="'none'"))
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_POST(self):  # noqa: N802
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):  # pragma: no cover
        pass


def measure(site_dir: Path, shots_dir: Path | None = None) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(site_dir)))
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    issues: list[dict] = []
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch()
            for vp, w, h in (("mobile", 390, 844), ("desktop", 1280, 860)):
                pg = b.new_page(viewport={"width": w, "height": h})
                for f in sorted(site_dir.glob("*.html")):
                    pg.goto(f"http://127.0.0.1:{port}/{f.name}", wait_until="load")
                    pg.evaluate("document.fonts.ready")
                    for i in pg.evaluate(MEASURE_JS, vp):
                        issues.append({**i, "page": f.stem, "viewport": vp})
                    if shots_dir and f.name == "index.html":
                        shots_dir.mkdir(parents=True, exist_ok=True)
                        pg.screenshot(path=str(shots_dir / f"critic-{vp}.png"), full_page=True)
                pg.close()
            b.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("visual critic unavailable: %s", exc)
    finally:
        srv.shutdown()
        srv.server_close()
    # one entry per (code, section)
    out, seen = [], set()
    for i in issues:
        k = (i["code"], i.get("section"), i.get("detail", "")[:30] if i["code"] in {"repeated_text", "low_contrast"} else "")
        if k not in seen:
            seen.add(k)
            out.append(i)
    return out


def _section(spec: dict, sid: str | None) -> dict | None:
    for p in spec["pages"]:
        for s in p["sections"]:
            if s["id"] == sid:
                return s
    return None


def plan_fixes(spec: dict, issues: list[dict], board: dict) -> tuple[list[dict], list[dict]]:
    """Map measured issues to validated patch ops. Returns (ops, explanations)."""
    ops: list[dict] = []
    notes: list[dict] = []
    t = spec["theme"]
    done: set = set()

    def add(op: dict, why: str, issue: dict):
        key = json.dumps(op, sort_keys=True)
        if key not in done:
            done.add(key)
            ops.append(op)
            notes.append({"issue": issue["code"], "where": f"{issue.get('page')}/{issue.get('viewport')}", "detail": issue.get("detail"), "fix": why})

    for i in issues:
        sec = _section(spec, i.get("section"))
        code = i["code"]
        if code == "headline_too_long" and sec:
            cands = [c["text"] for c in (board.get("copy.headline_candidates") or []) if c["score"] > 0]
            current = sec["content"].get("headline", "")
            shorter = [c for c in cands if len(c) < len(current) - 4]
            locked = "headline" in (sec["props"].get("owner_locked") or [])
            if locked:  # the owner wrote this headline: never replace it, only make it fit
                if t["scale"] > 0.86:
                    add({"op": "set_theme", "values": {"scale": round(max(0.85, t["scale"] - 0.1), 2)}},
                        "Your headline is long, so I reduced the heading size to help it fit (kept your words)", i)
                else:
                    notes.append({"issue": code, "where": f"{i.get('page')}/{i.get('viewport')}", "detail": i.get("detail"),
                                  "fix": None, "advice": f"Your headline still takes {i.get('lines')} lines on phones. "
                                  f"A shorter one (under ~60 characters) would read better — e.g. “{shorter[0] if shorter else 'something shorter'}”."})
            elif shorter and i.get("lines", 9) > 5:
                add({"op": "set_content", "section": sec["id"], "key": "headline", "value": shorter[0]},
                    f"Swapped to a shorter headline option: “{shorter[0]}”", i)
            elif t["scale"] > 0.86:
                add({"op": "set_theme", "values": {"scale": round(max(0.85, t["scale"] - 0.08), 2)}},
                    "Reduced the heading scale so the headline fits in fewer lines", i)
        elif code == "cta_below_fold" and sec:
            if sec["variant"] == "split" and not sec["props"].get("hide_mobile_art"):
                add({"op": "set_section_prop", "section": sec["id"], "key": "hide_mobile_art", "value": True},
                    "Hid the hero illustration on phones so the main button is visible straight away", i)
            elif not sec["props"].get("compact"):
                add({"op": "set_section_prop", "section": sec["id"], "key": "compact", "value": True},
                    "Tightened hero spacing so the main button is visible without scrolling", i)
            elif t["scale"] > 0.86:
                add({"op": "set_theme", "values": {"scale": round(max(0.85, t["scale"] - 0.06), 2)}}, "Smaller hero type", i)
        elif code == "orphan_card" and sec:
            n = i.get("n") or len(sec["content"].get("items", []))
            cols = 2 if n % 2 == 0 else 3 if n % 3 == 0 else 2
            if n in {5, 7}:
                add({"op": "set_section_variant", "section": sec["id"], "variant": "list"},
                    f"{n} items can't fill a grid evenly → switched to a two-column list", i)
            elif sec["props"].get("columns") != cols:
                add({"op": "set_section_prop", "section": sec["id"], "key": "columns", "value": cols},
                    f"{n} cards → {cols} columns so every row is full", i)
        elif code == "uneven_cards" and sec and sec["variant"] in {"cards", "bento"}:
            add({"op": "set_section_variant", "section": sec["id"], "variant": "list"},
                "Card heights varied a lot → list layout reads more evenly", i)
        elif code in {"text_clipped", "overflow_x"} and sec:
            if sec["type"] == "hero" and t["scale"] > 0.86:
                add({"op": "set_theme", "values": {"scale": round(max(0.85, t["scale"] - 0.08), 2)}}, "Smaller headline to stop clipping", i)
            elif sec["type"] in {"services"} and sec["variant"] != "list":
                add({"op": "set_section_variant", "section": sec["id"], "variant": "list"}, "List layout gives long names room", i)
        elif code == "low_contrast":
            from .team import mix
            add({"op": "set_theme", "values": {"muted": mix(t["muted"], t["text"], 0.35)}}, "Darkened secondary text for readability", i)
        elif code == "repeated_text" and i.get("sections"):
            secs = [x for x in (_section(spec, sid) for sid in i["sections"]) if x]
            head = next((x for x in secs if x["type"] == "page_head" and x["content"].get("sub")), None)
            if head:
                add({"op": "set_content", "section": head["id"], "key": "sub", "value": ""},
                    "Removed a page subtitle that repeated the text just below it", i)
                continue
            sec = secs[-1] if secs else sec
            if sec and sec["type"] == "services" and sec["content"].get("intro"):
                add({"op": "set_content", "section": sec["id"], "key": "intro", "value": ""}, "Removed a repeated intro line", i)
            elif sec and sec["type"] == "hero" and sec["content"].get("chips"):
                add({"op": "set_content", "section": sec["id"], "key": "chips", "value": []}, "Removed hero highlights repeated further down", i)
            elif sec["type"] == "why":
                add({"op": "remove_section", "section": sec["id"]}, "Removed a section that only repeated earlier content", i)
    return ops, notes


class _VisionCritique(BaseModel):
    issues: list[dict] = Field(default_factory=list, max_length=6)
    ops: list[dict] = Field(default_factory=list, max_length=6)


def vision_review(provider: LLMProvider, spec: dict, shots: list[Path]) -> tuple[list[dict], list[dict]]:
    """Ask a vision model for an art-direction critique; its proposed ops are validated like any other."""
    fn: Callable | None = getattr(provider, "complete_json_with_images", None)
    if fn is None or not shots:
        return [], []
    try:
        images = [base64.b64encode(p.read_bytes()).decode() for p in shots if p.exists()]
        out = fn("You are a senior web designer reviewing a small-business site (mobile and desktop screenshots). "
                 "List up to 5 concrete visual problems (hierarchy, spacing, alignment, emphasis, balance) and propose fixes "
                 f"ONLY as ops from this vocabulary: {sorted(specmod.OPS)}; editable theme fields {sorted(specmod.THEME_EDITABLE)}; "
                 f"editable section props {sorted(specmod.PROPS_EDITABLE)}; section variants {specmod.SECTION_VARIANTS}. "
                 "Return {issues:[{code,detail}], ops:[...]}.",
                 json.dumps({"outline": specmod.outline(spec), "theme": spec["theme"]}), images, _VisionCritique)
        good = []
        for op in out.ops:
            try:
                specmod.apply(spec, [op])
                good.append(op)
            except (ValueError, KeyError, TypeError):
                continue
        return [{**i, "source": "vision"} for i in out.issues], good
    except ProviderUnavailable:
        return [], []


def review_loop(spec: dict, render: Callable[[dict, Path], Any], work: Path, board: dict, provider: LLMProvider,
                max_rounds: int = 3) -> tuple[dict, list[dict]]:
    """Iteratively render, measure, and fix. Returns the final spec and a round-by-round report."""
    rounds = []
    for n in range(1, max_rounds + 1):
        site = work / f"round{n}"
        render(spec, site)
        issues = measure(site, work / f"shots{n}")
        v_issues, v_ops = vision_review(provider, spec, [work / f"shots{n}" / "critic-mobile.png", work / f"shots{n}" / "critic-desktop.png"]) if n == 1 else ([], [])
        ops, notes = plan_fixes(spec, issues, board)
        ops += v_ops
        notes += [{"issue": i.get("code", "design"), "where": "vision review", "detail": i.get("detail"), "fix": "model-proposed adjustment"} for i in v_issues]
        rounds.append({"round": n, "issues": issues, "fixes": notes})
        if not ops:
            break
        try:
            spec, _ = specmod.apply(spec, ops)
        except (ValueError, KeyError, TypeError) as exc:
            rounds[-1]["error"] = str(exc)[:200]
            break
    return spec, rounds
