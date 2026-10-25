"""Tenant isolation, auth, CSRF, secrets, policy (roadmap §9 and §12 'Security' layer)."""
import json

import pytest

from conftest import User, complete_onboarding, new_project, to_preview


def test_csrf_required_for_state_changes(client):
    u = User(client, "a@example.com")
    u._use()
    r = client.post("/api/projects", json={"name": "x"})
    assert r.status_code == 403


def test_tenant_isolation_everywhere(live_site, client):
    owner, p, b, dep = live_site
    pid = p["id"]
    lead = client.post(f"/api/public/sites/{p['slug']}/leads", json={"name": "Ravi", "phone": "+91 98765 43210"},
                       headers={"accept": "application/json"})
    assert lead.status_code == 200
    intruder = User(client, "mallory@example.com")
    for path in [f"/api/projects/{pid}", f"/api/projects/{pid}/builds", f"/api/projects/{pid}/leads",
                 f"/api/projects/{pid}/audit", f"/api/projects/{pid}/export", f"/api/projects/{pid}/deployments",
                 f"/api/projects/{pid}/integrations", f"/api/projects/{pid}/agent-runs",
                 f"/preview/{b['id']}/index.html"]:
        assert intruder.get(path).status_code == 404, path
    assert intruder.post(f"/api/projects/{pid}/deployments/rollback", json={"password": intruder.password}).status_code == 404
    assert intruder.post(f"/api/projects/{pid}/answers", json={"field_key": "business_name", "value": "pwned"}).status_code == 404
    assert pid not in [x["id"] for x in intruder.get("/api/projects").json()]
    # creating a project in someone else's workspace is refused
    tenant_of_owner = owner.get("/api/auth/me").json()["workspaces"][0]["id"]
    assert intruder.post("/api/projects", json={"name": "x", "workspace_id": tenant_of_owner}).status_code == 403


def test_roles_least_privilege(live_site, client):
    owner, p, b, dep = live_site
    viewer = User(client, "viewer@example.com")
    editor = User(client, "editor@example.com")
    assert owner.post(f"/api/projects/{p['id']}/members", json={"email": "viewer@example.com", "role": "viewer"}).status_code == 200
    assert owner.post(f"/api/projects/{p['id']}/members", json={"email": "editor@example.com", "role": "editor"}).status_code == 200
    assert viewer.get(f"/api/projects/{p['id']}").status_code == 200
    assert viewer.post(f"/api/projects/{p['id']}/answers", json={"field_key": "business_name", "value": "x"}).status_code == 403
    assert viewer.get(f"/api/projects/{p['id']}/leads").status_code == 403
    assert editor.get(f"/api/projects/{p['id']}/leads").status_code == 403  # visitor data is owner/admin only
    r = editor.post(f"/api/projects/{p['id']}/deployments/rollback", json={"password": editor.password})
    assert r.status_code == 403
    assert editor.post(f"/api/projects/{p['id']}/delete", json={"password": editor.password}).status_code == 403


def test_step_up_auth_for_publish(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    b = to_preview(u, pid)
    u.post(f"/api/projects/{pid}/builds/{b['id']}/decision", json={"checksum": b["checksum"], "decision": "approved"})
    r = u.post(f"/api/projects/{pid}/deployments", json={"build_id": b["id"], "idempotency_key": "k" * 10,
                                                         "password": "wrong password"})
    assert r.status_code == 401


def test_secrets_never_leave_the_vault(live_site, client):
    owner, p, b, dep = live_site
    secret = "s3cret-app-password-XYZ"
    r = owner.post(f"/api/projects/{p['id']}/integrations/smtp",
                   json={"host": "smtp.example.com", "port": 587, "username": "me", "from_address": "me@example.com",
                         "password": secret})
    assert r.status_code == 200 and secret not in r.text
    for path in ("integrations", "audit", "export", "agent-runs"):
        assert secret not in owner.get(f"/api/projects/{p['id']}/{path}").text
    from app import db
    from app.models import IntegrationConnection
    from app.security import Vault
    s = db.SessionLocal()
    conn = s.query(IntegrationConnection).first()
    assert conn.secret_reference.startswith("vault://")
    assert Vault.reveal(s, conn.tenant_id, conn.secret_reference) == secret
    with pytest.raises(PermissionError):
        Vault.reveal(s, "another-tenant", conn.secret_reference)
    s.close()


def test_redaction():
    from app.security import redact
    out = redact({"msg": "key sk-ant-abcdefghijklmnopqrstuvwx used", "password": "hunter2", "nested": ["AKIAABCDEFGHIJKLMNOP"]})
    assert "sk-ant" not in json.dumps(out) and out["password"] == "[REDACTED]" and "AKIA" not in json.dumps(out)


def test_secret_scanner_has_no_false_positives_on_font_names():
    from app.security import find_secrets
    assert not find_secrets('src:url("fonts/space-grotesk-latin-700-normal.woff2")')
    assert find_secrets("const k = 'sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123'")


def test_policy_engine():
    from app import policy
    for action in ("buy_domain", "spend_money", "send_bulk_marketing", "expand_access_scopes", "totally_unknown"):
        d = policy.evaluate(action, role="owner")
        assert not d.allowed, action
    d = policy.evaluate("deploy_production", role="owner", context={"gates_passed": True, "approval_valid": False,
                                                                    "artifact_intact": True})
    assert not d.allowed and d.requires_reauth
    assert policy.evaluate("generate_preview", role="viewer").allowed is False
    assert policy.evaluate("generate_preview", role="editor").allowed is True


def test_lifecycle_rules():
    from app import lifecycle
    assert lifecycle.can_transition("approved_for_release", "deploying")
    assert not lifecycle.can_transition("onboarding", "deploying")
    assert not lifecycle.can_transition("brief_ready", "live")
    assert not lifecycle.can_transition("archived", "onboarding")


def test_upload_validation(client):
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    r = u.post(f"/api/projects/{pid}/uploads", files={"file": ("x.svg", b"<svg onload=alert(1)>", "image/svg+xml")})
    assert r.status_code == 415
    png = b"\x89PNG\r\n\x1a\n" + b"\0" * 100
    r = u.post(f"/api/projects/{pid}/uploads", files={"file": ("logo.png", png, "image/png")})
    assert r.status_code == 200 and r.json()["reference"].startswith("upload://")


def test_login_rate_limit(client):
    User(client, "a@example.com")
    codes = [client.post("/api/auth/login", json={"email": "a@example.com", "password": "nope nope nope"}).status_code
             for _ in range(10)]
    assert codes[0] == 401 and codes[-1] == 429


def test_security_headers(live_site, client):
    owner, p, b, dep = live_site
    r = client.get(f"/sites/{p['slug']}/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    r = owner.get(f"/preview/{b['id']}/index.html")
    assert r.headers["x-robots-tag"].startswith("noindex")
    assert client.get(f"/sites/{p['slug']}/../../.secret_key").status_code == 404
