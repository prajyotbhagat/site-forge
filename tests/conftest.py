import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("SITEFORGE_INLINE_JOBS", "1")
os.environ.setdefault("SITEFORGE_BROWSER_QA", "off")  # browser QA is exercised by tests/e2e_browser.py
os.environ["SITEFORGE_LLM_PROVIDER"] = "rules"
os.environ.setdefault("SITEFORGE_COLOR_APIS", "")  # external colour APIs are exercised with mocked transports


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SITEFORGE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from app import config, db
    from app.security import limiter
    config.reload_settings()
    db.init_engine()
    limiter.reset()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, base_url="http://testserver") as c:
        yield c


class User:
    def __init__(self, client, email, password="correct horse battery"):
        self.c = client
        self.password = password
        client.cookies.clear()
        r = client.post("/api/auth/register", json={"email": email, "password": password, "name": email.split("@")[0]})
        assert r.status_code == 200, r.text
        self.cookies = {"sf_session": r.cookies["sf_session"]}
        self.csrf = r.json()["csrf_token"]
        client.cookies.clear()

    def _use(self):
        self.c.cookies.clear()
        for k, v in self.cookies.items():
            self.c.cookies.set(k, v)

    def get(self, path, **kw):
        self._use()
        return self.c.get(path, **kw)

    def post(self, path, json=None, **kw):
        self._use()
        headers = {"x-csrf-token": self.csrf, **kw.pop("headers", {})}
        return self.c.post(path, json=json, headers=headers, **kw)

    def delete(self, path, **kw):
        self._use()
        return self.c.delete(path, headers={"x-csrf-token": self.csrf}, **kw)


PLUMBER = ("I run a two-person plumbing business called Sharma Plumbing in Dehradun and want more calls. "
           "Our number is +91 98765 43210.")


def answer(u, pid, key, action="answer", value=None):
    r = u.post(f"/api/projects/{pid}/answers", json={"field_key": key, "action": action, "value": value})
    assert r.status_code == 200, (key, r.text)
    return r.json()


def complete_onboarding(u, pid, description=PLUMBER, email="owner@sharma.example"):
    st = answer(u, pid, "business_description", value=description)
    asked = ["business_description"]
    for _ in range(40):
        q = st["question"]
        if not q:
            return st, asked
        asked.append(q["field_key"])
        if q["kind"] == "email":
            st = answer(u, pid, q["field_key"], value=email)
        elif q["kind"] == "phone" and not q["suggestion"]:
            st = answer(u, pid, q["field_key"], value="+91 98765 43210")
        elif q["suggestion"]:
            st = answer(u, pid, q["field_key"], "confirm")
        elif q["recommendation"]:
            st = answer(u, pid, q["field_key"], "recommend")
        elif q["allow_skip"]:
            st = answer(u, pid, q["field_key"], "skip")
        else:
            st = answer(u, pid, q["field_key"], value="Homeowners")
    raise AssertionError("onboarding did not finish")


def new_project(u, name="Sharma Plumbing"):
    r = u.post("/api/projects", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


def to_preview(u, pid):
    st = u.get(f"/api/projects/{pid}").json()
    bp = st["blueprint"]
    r = u.post(f"/api/projects/{pid}/blueprint/decision", json={"content_hash": bp["content_hash"]})
    assert r.status_code == 200, r.text
    r = u.post(f"/api/projects/{pid}/builds")
    assert r.status_code == 200, r.text
    builds = u.get(f"/api/projects/{pid}/builds").json()
    return builds[0]


def approve_and_deploy(u, pid, build, key="deploy-key-1"):
    r = u.post(f"/api/projects/{pid}/builds/{build['id']}/decision", json={"checksum": build["checksum"], "decision": "approved"})
    assert r.status_code == 200, r.text
    r = u.post(f"/api/projects/{pid}/deployments", json={"build_id": build["id"], "idempotency_key": key, "password": u.password})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def live_site(client):
    u = User(client, "owner@example.com")
    p = new_project(u)
    complete_onboarding(u, p["id"])
    b = to_preview(u, p["id"])
    dep = approve_and_deploy(u, p["id"], b)
    return u, p, b, dep
