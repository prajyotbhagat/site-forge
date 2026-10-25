"""Full customer journey in a real browser (Playwright + Chromium).

Starts its own server on a free port with a throwaway data directory, then walks through:
sign up → guided onboarding (roadmap Appendix A) → plan approval → build with real-browser QA →
test enquiry from the preview → release approval → publish (password step-up) → live enquiry →
inbox / insights / settings.  Screenshots land in ./e2e-screens.

    python tests/e2e_browser.py
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "e2e-screens"
PASSWORD = "a long passphrase"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    OUT.mkdir(exist_ok=True)
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "SITEFORGE_DATA_DIR": tempfile.mkdtemp(prefix="sf-e2e-"),
           "SITEFORGE_PUBLIC_BASE_URL": base, "SITEFORGE_LLM_PROVIDER": "rules"}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)],
                              cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    errors: list[str] = []
    try:
        for _ in range(50):
            try:
                urllib.request.urlopen(base + "/healthz")
                break
            except OSError:
                time.sleep(0.2)
        with sync_playwright() as pw:
            br = pw.chromium.launch()
            pg = br.new_page(viewport={"width": 1360, "height": 900})
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            pg.on("pageerror", lambda e: errors.append(str(e)))

            pg.goto(base)
            pg.click("text=Create account")
            pg.fill("#nm", "Priya")
            pg.fill("#em", "priya@example.com")
            pg.fill("#pw", PASSWORD)
            pg.click("button[type=submit]")
            pg.fill("#pname", "Priya Tax Advisors website")
            pg.click("text=Start a new website")
            pg.wait_for_selector("#ans")
            pg.fill("#ans", "I run a small accounting and tax consultancy called Priya Tax Advisors. I want more "
                             "enquiries. We serve Roorkee and nearby towns, and remote clients too.")
            pg.click("#txt button[type=submit]")
            pg.wait_for_timeout(500)
            pg.screenshot(path=str(OUT / "01-guide.png"))
            for _ in range(30):
                pg.wait_for_timeout(250)
                if pg.query_selector(".done-card"):
                    break
                prompt = pg.inner_text("#qp")
                if pg.query_selector("#palpick"):
                    pg.wait_for_selector("#palpick .pal")
                    pg.wait_for_timeout(300)
                    pg.screenshot(path=str(OUT / "02a-colour-picker.png"), full_page=True)
                    pg.click("#palpick .pal-group:has-text('Popular for') .pal >> nth=0")
                    continue
                if pg.query_selector("#tplpick"):
                    pg.wait_for_selector("#tplpick .tpl")
                    pg.wait_for_timeout(800)  # fonts
                    pg.screenshot(path=str(OUT / "02b-template-gallery.png"), full_page=True)
                    pg.click("#tplpick .tpl >> nth=0")
                    continue
                if pg.query_selector("#confirm"):
                    pg.click("#confirm")
                elif "email address" in prompt:
                    pg.fill("#ans", "priya@example.com")
                    pg.click("#txt button[type=submit]")
                elif "Who are your typical customers" in prompt:
                    pg.fill("#ans", "Small businesses and salaried professionals")
                    pg.click("#txt button[type=submit]")
                elif "first conversation free" in prompt or "fully online" in prompt:
                    pg.click(".opt[data-val]:not([disabled])")
                elif "licence, registration" in prompt:
                    pg.fill("#ans", "Member, Institute of Chartered Accountants of India")
                    pg.click("#txt button[type=submit]")
                elif "different" in prompt:
                    pg.fill("#ans", "Fixed-fee pricing agreed upfront. Hindi and English spoken")
                    pg.click("#txt button[type=submit]")
                elif pg.query_selector("#rec"):
                    pg.click("#rec")
                elif pg.query_selector("#skip"):
                    pg.click("#skip")
                else:
                    raise AssertionError(f"no way to answer: {prompt}")
            pg.screenshot(path=str(OUT / "02-guide-done.png"))

            pg.click("[data-tab=plan]")
            pg.wait_for_selector("#approve")
            pg.wait_for_selector(".trail li")
            assert pg.locator(".trail li").count() >= 8, "team reasoning trail missing"
            pg.screenshot(path=str(OUT / "03-plan.png"), full_page=True)
            pg.click("#approve")
            pg.wait_for_selector("text=Checks passed", timeout=180000)
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(OUT / "04-preview.png"), full_page=True)

            frame = pg.frame_locator("iframe")
            frame.locator(".hero .actions a.btn-primary").click()
            for _ in range(40):
                if any("/contact.html" in f.url for f in pg.frames):
                    break
                pg.wait_for_timeout(100)
            pg.wait_for_timeout(400)
            frame.locator("#f-name").fill("Test Person")
            frame.locator("#f-email").fill("test@example.com")
            pg.wait_for_timeout(3200)  # minimum fill time spam control
            frame.locator("button[type=submit]").click()
            pg.wait_for_timeout(1200)
            if not any(f.url.endswith("/thanks.html") for f in pg.frames):
                print("frames:", [f.url for f in pg.frames], "status:", frame.locator(".form-status").inner_text(),
                      "errors:", [e for e in frame.locator(".error").all_inner_texts() if e])
                raise AssertionError("preview form did not reach thank-you page")

            pg.screenshot(path=str(OUT / "04b-preview-mobile-toggle.png"))
            # plain-English design change → editor agent → rebuild with visual critic
            pg.fill("#chgt", "Make it darker, show services as a list and rounder buttons")
            pg.click("#chg button[type=submit]")
            pg.wait_for_selector("text=Applied:", timeout=30000)
            pg.wait_for_selector("text=Checks passed", timeout=180000)
            pg.wait_for_selector("#apr", timeout=30000)
            assert pg.locator("text=Design review").count() >= 1
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(OUT / "04c-after-change.png"), full_page=True)
            # switch design directly from the gallery after approval
            pg.click("#chgtpl")
            pg.wait_for_selector("dialog .tpl")
            pg.wait_for_timeout(800)
            pg.screenshot(path=str(OUT / "04d-design-dialog.png"))
            pg.click("dialog .tpl:has-text('Modern product')")
            pg.wait_for_selector("text=Design → Modern product", timeout=30000)
            pg.wait_for_selector("#apr", timeout=180000)
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(OUT / "04e-after-design-switch.png"), full_page=True)
            pg.click("#apr")
            pg.wait_for_selector("#pub")
            pg.screenshot(path=str(OUT / "05-launch.png"), full_page=True)
            pg.click("#pub")
            pg.fill("#pw", PASSWORD)
            pg.click("dialog button[type=submit]")
            pg.wait_for_selector("text=Your site is live", timeout=20000)
            live = pg.get_attribute(".hero-card a", "href")

            site = br.new_page(viewport={"width": 390, "height": 844})
            site.on("console", lambda m: errors.append("site: " + m.text) if m.type == "error" else None)
            site.goto(base + live)
            site.screenshot(path=str(OUT / "06-live-mobile.png"), full_page=True)
            site.goto(base + live + "contact.html")
            site.fill("#f-name", "Real Visitor")
            site.fill("#f-email", "visitor@example.com")
            site.wait_for_timeout(3200)
            site.click("button[type=submit]")
            site.wait_for_url("**/thanks.html")

            pg.click("[data-tab=leads]")
            pg.wait_for_selector("text=Real Visitor")
            assert pg.is_visible("text=Test")
            pg.screenshot(path=str(OUT / "07-enquiries.png"))
            pg.click("[data-tab=insights]")
            pg.wait_for_timeout(600)
            pg.screenshot(path=str(OUT / "08-insights.png"))
            pg.click("[data-tab=settings]")
            pg.wait_for_timeout(600)
            pg.screenshot(path=str(OUT / "09-settings.png"), full_page=True)

            mobile = br.new_page(viewport={"width": 390, "height": 844})
            mobile.context.add_cookies(pg.context.cookies())
            mobile.goto(pg.url.replace("/settings", "/guide"))
            mobile.wait_for_timeout(800)
            mobile.screenshot(path=str(OUT / "10-app-mobile.png"))
            overflow = mobile.evaluate("document.documentElement.scrollWidth - innerWidth")
            assert overflow <= 1, f"app overflows horizontally on phones by {overflow}px"
            br.close()
    finally:
        server.terminate()
    if errors:
        print("console errors:", errors)
        return 1
    print(f"E2E OK — screenshots in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
