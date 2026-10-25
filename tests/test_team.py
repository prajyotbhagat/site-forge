"""Knowledge propagation, creative team, design engine, editor and visual critic."""
import pytest

from conftest import PLUMBER, User, answer, complete_onboarding, new_project, to_preview


def test_followup_questions_come_from_the_trade_and_change_the_site(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    st = answer(u, pid, "business_description", value=PLUMBER)
    seen = []
    while st["question"]:
        q = st["question"]
        seen.append(q["field_key"])
        k = q["field_key"]
        if k == "kb_emergency":
            st = answer(u, pid, k, value="yes_247")
        elif k == "kb_quote":
            st = answer(u, pid, k, value="free_quote")
        elif k == "kb_hours":
            st = answer(u, pid, k, value="Mon–Sat, 8am–8pm")
        elif q["kind"] == "email":
            st = answer(u, pid, k, value="o@example.com")
        elif q["suggestion"]:
            st = answer(u, pid, k, "confirm")
        elif q["recommendation"]:
            st = answer(u, pid, k, "recommend")
        else:
            st = answer(u, pid, k, "skip")
    assert {"kb_emergency", "kb_quote", "kb_hours"} <= set(seen)  # plumbing playbook questions
    assert "kb_problem" not in seen  # startup questions are not asked of a plumber
    b = to_preview(u, pid)
    html = u.get(f"/preview/{b['id']}/index.html").text
    assert "24/7" in html and "Free quote before work starts" in html and "Mon–Sat, 8am–8pm" in html


def test_every_agent_writes_to_the_shared_board_and_builds_on_others(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    trail = u.get(f"/api/projects/{pid}/knowledge").json()
    agents = {e["agent"] for e in trail}
    assert {"onboarding", "brand_strategist", "information_architect", "copywriter", "art_director"} <= agents
    art = next(e for e in trail if e["key"] == "art.theme")
    assert "strategy.urgency" in art["based_on"]  # art director used the strategist's conclusion
    assert any(e["key"] == "copy.headline_candidates" and len(e["content"]) >= 2 for e in trail)


def test_different_businesses_get_different_designs(client):
    u = User(client, "a@example.com")
    p1, p2 = new_project(u, "A")["id"], new_project(u, "B")["id"]
    s1, _ = complete_onboarding(u, p1)
    s2, _ = complete_onboarding(u, p2, description="We're launching a SaaS app called ClinicFlow that reduces "
                                                     "no-shows for clinics. We want waitlist sign-ups.")
    d1, d2 = s1["blueprint"]["content"]["design"], s2["blueprint"]["content"]["design"]
    assert d1["theme_id"] != d2["theme_id"] or d1["fonts"] != d2["fonts"]
    assert [x["type"] for x in d1["home_sections"]] != [x["type"] for x in d2["home_sections"]]
    assert d2["cta_label"] == "Join the waitlist"


def test_no_filler_sections(client):
    """Sections without real content are dropped, not padded."""
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    st, _ = complete_onboarding(u, pid)
    types = [x["type"] for x in st["blueprint"]["content"]["design"]["home_sections"]]
    assert "proof" not in types  # no reviews supplied
    considered = next(e for e in u.get(f"/api/projects/{pid}/knowledge").json() if e["key"] == "ia.considered")
    assert any(not c["included"] for c in considered["content"])


def test_change_request_flow(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    b1 = to_preview(u, pid)
    r = u.post(f"/api/projects/{pid}/change-requests", json={"text": "Make it darker, move the FAQ above services and use rounder buttons"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["applied"]) == 3 and out["build"]
    b2 = u.get(f"/api/projects/{pid}/builds").json()[0]
    assert b2["number"] == b1["number"] + 1 and b2["checksum"] != b1["checksum"]
    html = u.get(f"/preview/{b2['id']}/index.html").text
    assert html.index('class="sec faq') < html.index('class="sec services')
    hist = u.get(f"/api/projects/{pid}/spec").json()["history"]
    assert hist[0]["author"] == "you"
    # nonsense and unsupported claims are refused, with an explanation
    r = u.post(f"/api/projects/{pid}/change-requests", json={"text": "make the logo do a backflip"}).json()
    assert r["build"] is None and r["not_understood"]
    r = u.post(f"/api/projects/{pid}/change-requests", json={"text": 'Change the headline to "Award-winning plumbers since 1990"'}).json()
    assert r["build"] is None and "claim" in " ".join(r["not_understood"])


def test_owner_edits_survive_plan_regeneration(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    to_preview(u, pid)
    u.post(f"/api/projects/{pid}/change-requests", json={"text": "use square corners"})
    answer(u, pid, "business_name", value="Sharma & Sons Plumbing")  # regenerates the plan
    spec = u.get(f"/api/projects/{pid}/spec").json()["latest"]
    assert spec["theme"]["radius"] == 0 and spec["theme"]["button"] == "square"


def test_spec_patch_validation():
    from app.sitegen import spec as specmod
    from app.agents import team
    f = {"business_description": PLUMBER, "site_category": "local_service", "business_name": "S", "services": ["Leak repairs"],
         "primary_goal": "calls", "phone": "+91 98765 43210", "features": ["click_to_call"], "pages": ["home", "contact"]}
    from app.agents.llm import RulesProvider
    s = team.run_strategist(f, None, RulesProvider())
    board = {"strategy": s.artifacts["strategy"], "cta_label": "Call now"}
    ia = team.run_architect(f, board, None)
    cp = team.run_copywriter(f, board, ia.artifacts["pages"], RulesProvider())
    ad = team.run_art_director(f, board, ia.artifacts["pages"])
    spec = specmod.validate(team.assemble_spec(f, "s", ia.artifacts["pages"], cp.artifacts, ad.artifacts, "Call now"))
    hero = spec["pages"][0]["sections"][0]["id"]
    for bad in ({"op": "set_section_variant", "section": hero, "variant": "carousel"},
                {"op": "set_theme", "values": {"primary": "red; background:url(x)"}},
                {"op": "set_theme", "values": {"custom_css": "*{}"}},
                {"op": "remove_section", "section": hero},
                {"op": "inject_html", "html": "<script>"}):
        with pytest.raises(ValueError):
            specmod.apply(spec, [bad])


def test_critic_maps_measurements_to_fixes():
    from app.agents.critic import plan_fixes
    spec = {"theme": {"scale": 1.0, "muted": "#555555", "text": "#111111"},
            "pages": [{"slug": "index", "sections": [
                {"id": "h", "type": "hero", "variant": "split", "props": {}, "content": {"headline": "A very long headline that goes on and on"}},
                {"id": "s", "type": "services", "variant": "cards", "props": {"columns": 3}, "content": {"items": [1, 2, 3, 4]}}]}]}
    board = {"copy.headline_candidates": [{"text": "Short one", "score": 5}]}
    ops, notes = plan_fixes(spec, [
        {"code": "headline_too_long", "section": "h", "lines": 7, "page": "index", "viewport": "mobile"},
        {"code": "cta_below_fold", "section": "h", "page": "index", "viewport": "mobile"},
        {"code": "orphan_card", "section": "s", "n": 4, "page": "index", "viewport": "desktop"}], board)
    kinds = {(o["op"], o.get("key") or o.get("variant")) for o in ops}
    assert ("set_content", "headline") in kinds and ("set_section_prop", "hide_mobile_art") in kinds and ("set_section_prop", "columns") in kinds
    # owner-written headline is never replaced
    spec["pages"][0]["sections"][0]["props"]["owner_locked"] = ["headline"]
    ops, notes = plan_fixes(spec, [{"code": "headline_too_long", "section": "h", "lines": 7, "page": "index", "viewport": "mobile"}], board)
    assert all(o["op"] != "set_content" for o in ops)


def test_generated_site_uses_bundled_fonts_and_no_third_parties(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    b = to_preview(u, pid)
    css = u.get(f"/preview/{b['id']}/assets/site.css").text
    assert "@font-face" in css and "fonts/" in css and "http" not in css.split("data:image")[0]
    assert b["security_report"]["passed"]
