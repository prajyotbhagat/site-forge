"""Backend & Data agent and Analytics & Growth agent."""
from __future__ import annotations

from typing import Any

from ..onboarding.schema import FORM_FIELDS
from .contracts import AgentResult, Risk

FIELD_SPECS = {
    "name": {"type": "text", "max": 100, "autocomplete": "name", "classification": "personal"},
    "email": {"type": "email", "max": 254, "autocomplete": "email", "classification": "personal"},
    "phone": {"type": "tel", "max": 20, "autocomplete": "tel", "classification": "personal"},
    "company": {"type": "text", "max": 120, "autocomplete": "organization", "classification": "business"},
    "service": {"type": "select", "max": 120, "autocomplete": "off", "classification": "business"},
    "message": {"type": "textarea", "max": 2000, "autocomplete": "off", "classification": "free_text"},
}


def run_backend(a: dict[str, Any]) -> AgentResult:
    feats = a.get("features") or []
    if "contact_form" not in feats:
        return AgentResult(agent="backend_data", summary="No form selected; no visitor data will be stored.",
                           artifacts={"lead_form": None, "data_flows": []})
    chosen = [f for f in (a.get("form_fields") or ["name", "email", "message"]) if f in FIELD_SPECS]
    if "name" not in chosen:
        chosen.insert(0, "name")
    if not ({"email", "phone"} & set(chosen)):
        chosen.insert(1, "email")  # we must be able to reply
    fields = []
    for f in chosen:
        spec = dict(FIELD_SPECS[f])
        spec.update({"key": f, "label": FORM_FIELDS[f],
                     "required": f in {"name"} or (f == "email" and "phone" not in chosen)
                     or (f == "phone" and "email" not in chosen)})
        if f == "service":
            spec["options"] = (a.get("services") or [])[:12] + ["Something else"]
        fields.append(spec)
    retention = int(a.get("lead_retention_days") or 180)
    lead_form = {
        "fields": fields,
        "contact_rule": "email_or_phone" if {"email", "phone"} <= set(chosen) else None,
        "endpoint": "/api/public/sites/{slug}/leads",
        "spam_controls": {"honeypot_field": "website", "min_fill_seconds": 3, "rate_limit": "5 per 10 minutes per visitor"},
        "retention_days": retention,
        "access": "owners_and_admins",
        "notification": {"channel": "email", "to": "contact_email (kept server-side)"},
    }
    flows = [
        {"from": "Visitor's browser", "to": "SiteForge Lead API", "data": [FORM_FIELDS[f] for f in chosen], "protection": "TLS, validation"},
        {"from": "Lead API", "to": "Platform database", "data": "Enquiry record", "protection": f"Encrypted at rest, deleted after {retention} days"},
        {"from": "Lead API", "to": "Your email inbox", "data": "Enquiry summary", "protection": "Sent once per enquiry"},
    ]
    risks = []
    if "message" in chosen:
        risks.append(Risk(id="free-text", severity="low", title="Free-text message",
                          detail="Visitors may type more than you need.",
                          mitigation="Form hint asks people not to share sensitive details; retention limit applies."))
    return AgentResult(agent="backend_data", summary=f"Lead form with {len(fields)} fields; stored {retention} days.",
                       artifacts={"lead_form": lead_form, "data_flows": flows,
                                  "api_contract": {"POST /api/public/sites/{slug}/leads": {
                                      "body": {f["key"]: f["type"] for f in fields} | {"website": "honeypot", "ts": "int"},
                                      "responses": {"303": "redirect to thanks.html", "200": "json ok", "422": "validation errors",
                                                    "429": "rate limited"}}}},
                       risks=risks, tests_required=["unit:lead-validation", "integration:lead-notification-idempotent"],
                       risk_tier="medium")


def run_analytics(a: dict[str, Any]) -> AgentResult:
    goal = a.get("primary_goal") or "enquiries"
    events = ["page_view", "cta_click"]
    feats = a.get("features") or []
    if "contact_form" in feats:
        events.append("form_submit")
    if "click_to_call" in feats:
        events.append("call_click")
    if "whatsapp" in feats:
        events.append("whatsapp_click")
    target = (a.get("monthly_lead_target") or "").strip()
    primary_metric = {"calls": "call_click", "waitlist": "form_submit", "bookings": "form_submit"}.get(goal, "form_submit")
    if primary_metric not in events:
        primary_metric = "cta_click"
    return AgentResult(
        agent="analytics_growth",
        summary=f"Tracking {len(events)} cookie-free events; primary measure is {primary_metric}.",
        artifacts={"events": events, "primary_metric": primary_metric,
                   "target": target or None, "review_after_days": 30,
                   "principles": ["No cookies or personal data in events",
                                  "Compare against the first 30 days as a baseline",
                                  "Small changes, one at a time, before drawing conclusions"]},
    )
