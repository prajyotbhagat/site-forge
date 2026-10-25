"""QA & Accessibility agent: static HTML checks plus real-browser responsive checks (Playwright)."""
from __future__ import annotations

import functools
import http.server
import logging
import re
import socketserver
import threading
from html.parser import HTMLParser
from pathlib import Path

from .. import config
from .design import contrast

log = logging.getLogger("siteforge.qa")


class _Page(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = ""
        self._in_title = False
        self.meta_desc = None
        self.viewport = False
        self.headings: list[int] = []
        self.imgs_missing_alt = 0
        self.controls: list[dict] = []
        self.label_for: set[str] = set()
        self.links: list[str] = []
        self._label_depth = 0
        self.robots = None

    def handle_starttag(self, tag, attrs):
        at = {k: (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = at.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta":
            if at.get("name") == "description":
                self.meta_desc = at.get("content", "")
            if at.get("name") == "viewport":
                self.viewport = True
            if at.get("name") == "robots":
                self.robots = at.get("content")
        elif re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag == "img" and "alt" not in at:
            self.imgs_missing_alt += 1
        elif tag == "label":
            self._label_depth += 1
            if at.get("for"):
                self.label_for.add(at["for"])
        elif tag in {"input", "select", "textarea"} and at.get("type") not in {"hidden", "submit"}:
            self.controls.append({"id": at.get("id"), "aria": at.get("aria-label") or at.get("aria-labelledby"),
                                  "wrapped": self._label_depth > 0, "name": at.get("name")})
        elif tag == "a" and at.get("href"):
            self.links.append(at["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "label":
            self._label_depth = max(0, self._label_depth - 1)

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def static_checks(build_dir: Path, tokens: dict) -> list[dict]:
    results: list[dict] = []

    def add(check: str, ok: bool, file: str = "", detail: str = "", severity: str = "high"):
        results.append({"check": check, "passed": ok, "file": file, "detail": detail, "severity": "info" if ok else severity})

    pages = sorted(build_dir.glob("*.html"))
    add("pages-exist", bool(pages), detail=f"{len(pages)} pages", severity="critical")
    for f in pages:
        rel = f.name
        text = f.read_text()
        p = _Page()
        p.feed(text)
        add("html-lang", bool(p.lang), rel, "<html lang> set")
        add("title", 0 < len(p.title.strip()) <= 70, rel, p.title.strip()[:80], "medium")
        add("meta-description", bool(p.meta_desc) and len(p.meta_desc) <= 160, rel, (p.meta_desc or "")[:80], "medium")
        add("viewport", p.viewport, rel, "responsive viewport meta")
        add("single-h1", p.headings.count(1) == 1, rel, f"{p.headings.count(1)} h1 element(s)")
        skips = [(a, b) for a, b in zip(p.headings, p.headings[1:]) if b > a + 1]
        add("heading-order", not skips, rel, f"skipped levels: {skips}" if skips else "ok", "medium")
        add("img-alt", p.imgs_missing_alt == 0, rel, f"{p.imgs_missing_alt} image(s) missing alt")
        unlabeled = [c["name"] for c in p.controls if not (c["wrapped"] or c["aria"] or (c["id"] and c["id"] in p.label_for))]
        add("form-labels", not unlabeled, rel, f"unlabeled: {unlabeled}" if unlabeled else f"{len(p.controls)} controls labeled")
        broken = []
        for href in p.links:
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if href.startswith("tel:"):
                if not re.fullmatch(r"tel:\+?[0-9]{7,15}", href):
                    broken.append(href)
                continue
            if href.startswith("https://wa.me/"):
                continue
            target = href.split("#")[0]
            if target and not (build_dir / target).exists():
                broken.append(href)
        add("links-resolve", not broken, rel, f"broken: {broken}" if broken else f"{len(p.links)} links ok")
        placeholders = re.findall(r"(?i)lorem ipsum|\bTODO\b|\{\{|\}\}|\bNone\b|\bundefined\b", re.sub(r"<[^>]+>", " ", text))
        add("no-placeholder-text", not placeholders, rel, f"found: {placeholders[:3]}" if placeholders else "ok")
    add("robots-txt", (build_dir / "robots.txt").exists(), "robots.txt", severity="medium")
    add("sitemap-xml", (build_dir / "sitemap.xml").exists(), "sitemap.xml", severity="medium")
    if tokens:
        for name, fg, bg in [("body text", tokens["text"], tokens["bg"]), ("button text", "#ffffff", tokens["primary"]),
                             ("accent text", tokens["on_accent"], tokens["accent"]), ("muted text", tokens["muted"], tokens["bg"])]:
            ratio = contrast(fg, bg)
            add("contrast", ratio >= 4.5, "tokens", f"{name}: {ratio:.2f}:1")
    return results


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    """Serves the build with the same security headers as production, so header problems surface here."""

    def end_headers(self):
        from ..api.release_api import SITE_CSP
        self.send_header("Content-Security-Policy", SITE_CSP.format(fa="'none'"))
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    """Static file server for the QA sandbox; platform API calls (events/leads) are stubbed with 204."""

    def do_POST(self):  # noqa: N802
        if self.path.startswith("/api/public/"):
            self.send_response(204)
            self.end_headers()
        else:
            self.send_error(404)

    def log_message(self, *args):  # pragma: no cover
        pass


def browser_checks(build_dir: Path, reports_dir: Path) -> dict:
    """Load every page at phone and desktop widths in headless Chromium."""
    mode = config.settings.browser_qa
    if mode == "off":
        return {"ran": False, "reason": "disabled", "results": []}
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"ran": False, "reason": "playwright not installed", "results": []}

    handler = functools.partial(_QuietHandler, directory=str(build_dir))
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    results: list[dict] = []
    screenshots: list[str] = []
    reports_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            for width, label in ((375, "mobile"), (1280, "desktop")):
                ctx = browser.new_context(viewport={"width": width, "height": 800})
                page = ctx.new_page()
                errors: list[str] = []
                page.on("console", lambda m, e=errors: e.append(m.text) if m.type == "error" else None)
                page.on("pageerror", lambda exc, e=errors: e.append(str(exc)))
                for f in sorted(build_dir.glob("*.html")):
                    errors.clear()
                    page.goto(f"http://127.0.0.1:{port}/{f.name}", wait_until="load")
                    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                    results.append({"check": f"no-horizontal-scroll-{label}", "file": f.name, "passed": overflow <= 1,
                                    "detail": f"overflow {overflow}px", "severity": "info" if overflow <= 1 else "high"})
                    errs = [e for e in errors if "favicon" not in e]
                    results.append({"check": f"no-console-errors-{label}", "file": f.name, "passed": not errs,
                                    "detail": "; ".join(errs)[:300] or "ok", "severity": "info" if not errs else "high"})
                    if label == "mobile":
                        small = page.evaluate("""() => [...document.querySelectorAll('a.btn, button')]
                            .filter(el => el.offsetParent && el.getBoundingClientRect().height < 40).length""")
                        results.append({"check": "touch-targets", "file": f.name, "passed": small == 0,
                                        "detail": f"{small} small tap target(s)", "severity": "info" if small == 0 else "medium"})
                    if f.name in {"index.html", "contact.html"}:
                        shot = reports_dir / f"{f.stem}-{label}.png"
                        page.screenshot(path=str(shot), full_page=True)
                        screenshots.append(shot.name)
                ctx.close()
            browser.close()
    except Exception as exc:  # pragma: no cover - environment dependent
        log.warning("browser QA failed: %s", exc)
        if mode == "on":
            results.append({"check": "browser-qa", "passed": False, "detail": str(exc)[:200], "severity": "high"})
        return {"ran": False, "reason": f"browser unavailable: {exc}"[:200], "results": results}
    finally:
        httpd.shutdown()
        httpd.server_close()
    return {"ran": True, "results": results, "screenshots": screenshots}


def run(build_dir: Path, reports_dir: Path, tokens: dict) -> dict:
    static = static_checks(build_dir, tokens)
    browser = browser_checks(build_dir, reports_dir)
    allr = static + browser.get("results", [])
    blocking = [r for r in allr if not r["passed"] and r["severity"] in {"critical", "high"}]
    warnings = [r for r in allr if not r["passed"] and r["severity"] not in {"critical", "high"}]
    return {"passed": not blocking, "total": len(allr), "failed": len(blocking), "warnings": len(warnings),
            "results": allr, "browser": {k: v for k, v in browser.items() if k != "results"}}
