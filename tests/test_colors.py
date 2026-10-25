"""Colour sources: external APIs (mocked), logo, reference site, harmonies, custom colours, templates."""
import io

import httpx
import pytest

from conftest import PLUMBER, User, answer, complete_onboarding, new_project, to_preview


def _png(colors):
    from PIL import Image, ImageDraw
    im = Image.new("RGBA", (200, 100), (255, 255, 255, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, 120, 100), fill=colors[0])
    d.ellipse((130, 20, 190, 80), fill=colors[1])
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_harmonies_are_readable():
    from app.agents.design import contrast
    from app.services import colors
    for seed in ("#ffee00", "#2a9d8f", "#111111", "#ffffff", "#e63946"):
        for p in colors.harmonies(seed):
            assert contrast("#ffffff", p["primary"]) >= 4.5, (seed, p)


def test_color_api_adapters_parse_and_validate():
    from app.services import colors

    def ok(req):
        if "thecolorapi" in str(req.url):
            assert req.url.params["hex"] == "2a9d8f"
            return httpx.Response(200, json={"colors": [{"hex": {"value": "#2A9D8F"}}, {"hex": {"value": "#E76F51"}}]})
        assert req.method == "POST"
        return httpx.Response(200, json={"result": [[42, 157, 143], [38, 70, 83], [233, 196, 106], [244, 162, 97], [231, 111, 81]]})
    t = httpx.MockTransport(ok)
    assert len(colors.thecolorapi("#2a9d8f", t)) == 3
    assert colors.colormind("#2a9d8f", t)[0]["source"] == "colormind"

    def junk(req):
        return httpx.Response(200, json={"result": [["<script>", 1, 2], [999, 0, 0]]})
    with pytest.raises(ValueError):
        colors.colormind("#2a9d8f", httpx.MockTransport(junk))


def test_external_failures_trip_breaker_and_never_raise(monkeypatch):
    from app.services import colors
    monkeypatch.setenv("SITEFORGE_COLOR_APIS", "thecolorapi,colormind")
    colors.breaker.reset()
    colors._cache.clear()

    def down(req):
        raise httpx.ConnectError("down")
    res, notes = colors.external("#123456", httpx.MockTransport(down))
    assert res == [] and len(notes) == 2
    res, notes = colors.external("#123456", httpx.MockTransport(down))
    assert all("paused" in n for n in notes)  # second call doesn't wait on a dead API
    colors.breaker.reset()


def test_ssrf_guard():
    from app.services import colors
    for bad in ("http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.1.2.3", "http://[::1]/",
                "http://x.com:2375/", "http://user:pass@x.com/"):
        with pytest.raises(colors.UnsafeURL):
            colors.safe_get(bad)

    def redirect(req):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    def only_loopback_blocked(host):
        if host.startswith("127."):
            raise colors.UnsafeURL("private")
    with pytest.raises(colors.UnsafeURL):
        colors.safe_get("https://public.example/", transport=httpx.MockTransport(redirect), check_host=only_loopback_blocked)


def test_reference_site_analysis():
    from app.services import colors
    html = ('<meta name="theme-color" content="#0b6e4f"><link rel="stylesheet" href="/s.css">'
            '<link rel="stylesheet" href="https://evil.example/x.css">')
    css = ':root{--brand-primary:#0b6e4f;--accent:#f4a259} h1{font-family:"Playfair Display",serif}'
    fetched = []

    def fetch(u):
        fetched.append(u)
        return (u, css) if u.endswith(".css") else (u, html)
    r = colors.analyse_site("https://shop.example", fetch=fetch)
    assert r["palettes"][0]["primary"] and r["template"] == "elegant"
    assert not any("evil.example" in u for u in fetched)  # only the site's own stylesheets


def test_logo_colours_become_a_suggestion(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    answer(u, pid, "business_description", value=PLUMBER)
    ref = u.post(f"/api/projects/{pid}/uploads", files={"file": ("l.png", _png([(200, 40, 60, 255), (20, 90, 160, 255)]), "image/png")}).json()["reference"]
    st = answer(u, pid, "logo", value=ref)
    pal = next(f for f in st["facts"] if f["field_key"] == "palette")
    assert pal["source"] == "extracted" and pal["value"].startswith("custom:#")
    q = u.get(f"/api/projects/{pid}/questions/palette").json()
    assert q["suggestion"] and "logo" in q["suggestion"]["reason"].lower()
    groups = u.get(f"/api/projects/{pid}/palettes").json()["groups"]
    assert {"logo", "industry", "harmony", "preset"} <= {g["source"] for g in groups}


def test_reference_site_suggests_colours_and_template(client, monkeypatch):
    from app.services import colors
    html = '<style>:root{--primary:#7a1f5c;--accent:#f2c14e} body{font-family:Nunito,sans-serif}</style>'
    monkeypatch.setattr(colors, "safe_get", lambda url, **k: ("https://liked.example/", html))
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    answer(u, pid, "business_description", value=PLUMBER)
    st = answer(u, pid, "reference_site", value="liked.example")
    facts = {f["field_key"]: f for f in st["facts"]}
    assert facts["palette"]["value"].startswith("custom:") and facts["template"]["value"] == "friendly"
    board = u.get(f"/api/projects/{pid}/knowledge").json()
    assert any(e["key"] == "brand.reference" for e in board)


def test_custom_colours_and_template_choice_reach_the_site(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    answer(u, pid, "business_description", value=PLUMBER)
    r = u.post(f"/api/projects/{pid}/answers", json={"field_key": "palette", "value": "#123 / zzz"})
    assert r.status_code == 422
    answer(u, pid, "palette", value="#0b6e4f, #f4a259")
    answer(u, pid, "template", value="elegant")
    st, _ = complete_onboarding(u, pid)
    d = st["blueprint"]["content"]["design"]
    assert d["theme_id"] == "elegant" and d["tokens"]["accent"] == "#f4a259"
    b = to_preview(u, pid)
    css = u.get(f"/preview/{b['id']}/assets/site.css").text
    assert "DM Serif Display" in css and "#f4a259" in css


def test_design_endpoint_rebuilds_with_new_colours(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    b1 = to_preview(u, pid)
    tpls = u.get(f"/api/projects/{pid}/templates").json()["templates"]
    assert len(tpls) == 8 and sum(t["recommended"] for t in tpls) == 1
    r = u.post(f"/api/projects/{pid}/design", json={"template": "tech", "palette": "custom:#ffee00:#111111"})
    assert r.status_code == 200, r.text
    b2 = u.get(f"/api/projects/{pid}/builds").json()[0]
    assert b2["number"] == b1["number"] + 1
    css = u.get(f"/preview/{b2['id']}/assets/site.css").text
    html = u.get(f"/preview/{b2['id']}/index.html").text
    assert "hero-centered" in html  # layouts re-chosen by the art director for the new design
    assert "Space Grotesk" in css and "--primary:#ffee00" not in css  # yellow was darkened for readability
    assert u.get("/api/fonts/inter-latin-400-normal.woff2").status_code == 200
    assert u.get("/api/fonts/..%2F..%2Fconfig.py").status_code == 404
