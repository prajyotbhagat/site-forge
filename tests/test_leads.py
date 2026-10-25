"""Lead API: validation, spam controls, notifications, retention, analytics (roadmap phase 5)."""
import time
from datetime import timedelta

J = {"accept": "application/json"}


def test_lead_happy_path_and_idempotent_notification(live_site, client):
    u, p, b, dep = live_site
    r = client.post(f"/api/public/sites/{p['slug']}/leads", json={"name": "Ravi", "email": "ravi@example.com", "message": "Leak"}, headers=J)
    assert r.status_code == 200
    leads = u.get(f"/api/projects/{p['id']}/leads").json()
    assert len(leads) == 1 and leads[0]["notification"] == "delivered_to_outbox" and not leads[0]["is_test"]
    from app import db
    from app.models import Lead, Notification
    from app.services import notify
    s = db.SessionLocal()
    lead = s.query(Lead).first()
    notify.queue(s, tenant_id=lead.tenant_id, project_id=lead.project_id, to="x@example.com", subject="dup", body="dup",
                 key=f"lead:{lead.id}")
    s.commit()
    assert s.query(Notification).count() == 1  # retry did not send twice
    s.close()


def test_no_js_form_post_redirects(live_site, client):
    u, p, b, dep = live_site
    r = client.post(f"/api/public/sites/{p['slug']}/leads", data={"name": "Asha", "phone": "+91 98765 43210"},
                    headers={"referer": f"http://testserver/sites/{p['slug']}/contact.html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/thanks.html")


def test_validation_errors(live_site, client):
    u, p, b, dep = live_site
    url = f"/api/public/sites/{p['slug']}/leads"
    r = client.post(url, json={"name": "", "email": "nope"}, headers=J)
    assert r.status_code == 422 and {"name", "email"} <= set(r.json()["errors"])
    r = client.post(url, json={"name": "A"}, headers=J)
    assert r.status_code == 422 and "email" in r.json()["errors"]  # need email or phone
    r = client.post(url, json={"name": "A" * 500, "email": "a@example.com"}, headers=J)
    assert r.status_code == 422


def test_spam_controls(live_site, client):
    u, p, b, dep = live_site
    url = f"/api/public/sites/{p['slug']}/leads"
    r = client.post(url, json={"name": "Bot", "email": "b@example.com", "website": "http://spam"}, headers=J)
    assert r.status_code == 200  # honeypot: pretend success
    r = client.post(url, json={"name": "Fast", "email": "f@example.com", "ts": str(int(time.time()))}, headers=J)
    assert r.status_code == 429
    assert u.get(f"/api/projects/{p['id']}/leads").json() == []
    r = client.post(url, json={"name": "X", "email": "x@example.com"}, headers={**J, "origin": "https://evil.example"})
    assert r.status_code == 403
    codes = [client.post(url, json={"name": "N", "email": "n@example.com"}, headers=J).status_code for _ in range(5)]
    assert 429 in codes  # rate limit per visitor


def test_preview_submissions_are_test_leads(client):
    from conftest import User, complete_onboarding, new_project, to_preview
    u = User(client, "a@example.com")
    pid = new_project(u)["id"]
    complete_onboarding(u, pid)
    b = to_preview(u, pid)
    slug = u.get(f"/api/projects/{pid}").json()["project"]["slug"]
    u._use()
    r = client.post(f"/api/public/sites/{slug}/leads", json={"name": "T", "email": "t@example.com"},
                    headers={**J, "referer": f"http://testserver/preview/{b['id']}/contact.html"})
    assert r.status_code == 200
    leads = u.get(f"/api/projects/{pid}/leads").json()
    assert leads[0]["is_test"] is True
    client.cookies.clear()  # anonymous visitors can't use the preview context
    r = client.post(f"/api/public/sites/{slug}/leads", json={"name": "T", "email": "t@example.com"},
                    headers={**J, "referer": f"http://testserver/preview/{b['id']}/contact.html"})
    assert r.status_code == 404


def test_retention_purge(live_site, client):
    u, p, b, dep = live_site
    client.post(f"/api/public/sites/{p['slug']}/leads", json={"name": "Old", "email": "o@example.com"}, headers=J)
    from app import db
    from app.models import Lead, utcnow
    s = db.SessionLocal()
    lead = s.query(Lead).first()
    lead.delete_after = utcnow() - timedelta(days=1)
    s.commit()
    s.close()
    assert u.get(f"/api/projects/{p['id']}/leads").json() == []


def test_cookie_free_events(live_site, client):
    u, p, b, dep = live_site
    url = f"/api/public/sites/{p['slug']}/events"
    assert client.post(url, json={"event": "call_click"}).status_code == 200
    assert client.post(url, json={"event": "evil_event"}).status_code == 422
    a = u.get(f"/api/projects/{p['id']}/analytics").json()
    assert a["totals"]["call_click"] == 1
    assert "set-cookie" not in client.post(url, json={"event": "page_view"}).headers
