"""Requirements Planner and Platform Architect agents."""
from __future__ import annotations

from typing import Any

from ..onboarding.schema import CATEGORIES, FEATURES, FORM_FIELDS, GOALS, PAGES, SUPPORTED_CATEGORIES
from .contracts import AgentResult, Risk


def _req(key, description, category, priority="P0", status="in_scope", dependency=None, acceptance=None,
         risk="low", data=None) -> dict:
    return {"key": key, "description": description, "category": category, "priority": priority, "status": status,
            "dependency": dependency or [], "acceptance_criteria": acceptance or [], "risk_level": risk,
            "data_collected": data or []}


def run_planner(a: dict[str, Any]) -> AgentResult:
    reqs: list[dict] = []
    cat = a.get("site_category")
    pages = a.get("pages") or ["home", "contact"]
    feats = a.get("features") or []
    offer_label = {"startup": "Product", "portfolio": "Work"}.get(cat or "", "Services")

    for p in pages:
        label = offer_label if p == "services" else PAGES.get(p, p.title())
        reqs.append(_req(f"page-{p}", f"{label} page", "frontend",
                         acceptance=["Renders without horizontal scrolling at 375px and 1280px",
                                     "Exactly one H1; unique title and meta description",
                                     "Primary call to action visible"]))
    goal = a.get("primary_goal") or "enquiries"
    reqs.append(_req("primary-cta", f"Primary action: {GOALS.get(goal, (goal,))[0]}", "conversion",
                     acceptance=["CTA appears in the header and hero on every page",
                                 "CTA clicks are counted without cookies"]))

    if "contact_form" in feats:
        fields = a.get("form_fields") or ["name", "email", "message"]
        reqs.append(_req("lead-form", "Enquiry form with secure storage and email notification", "backend",
                         dependency=["page-contact"] if "contact" in pages else [],
                         acceptance=["Server-side validation rejects malformed input",
                                     "Spam controls: honeypot, minimum fill time, rate limit",
                                     "Owner receives one notification per enquiry (idempotent)",
                                     f"Enquiries deleted after {a.get('lead_retention_days', '180')} days",
                                     "Only owners/admins can read enquiries"],
                         risk="medium", data=[FORM_FIELDS.get(f, f) for f in fields]))
        reqs.append(_req("privacy-notice", "Privacy notice describing what the form collects and why", "trust",
                         dependency=["lead-form"],
                         acceptance=["Linked from the form and footer", "Lists fields, purpose, retention and contact"]))
    if "click_to_call" in feats:
        reqs.append(_req("click-to-call", "Tap-to-call button", "conversion",
                         acceptance=["Uses a valid tel: link", "Sticky on mobile"]))
    if "whatsapp" in feats:
        reqs.append(_req("whatsapp", "WhatsApp chat link", "conversion", acceptance=["Opens wa.me with the business number"]))
    for f in feats:
        label, tier = FEATURES.get(f, (f, "later"))
        if tier == "later":
            reqs.append(_req(f"deferred-{f}", label, "backend", priority="P1" if f in {"booking", "newsletter"} else "P2",
                             status="deferred", risk="high" if f in {"accounts", "payments"} else "medium"))
    if cat == "shop":
        reqs.append(_req("ecommerce", "Full online shop (cart, payments, inventory)", "backend", priority="P2",
                         status="out_of_scope", risk="high"))
        reqs.append(_req("product-showcase", "Product showcase with enquiry form (interim)", "frontend"))
    if a.get("language") and a.get("language") != "en":
        reqs.append(_req("multilingual", "Additional site language", "frontend", priority="P1", status="deferred"))
    if a.get("hosting") == "own_account":
        reqs.append(_req("own-hosting", "Deploy into customer's own hosting account", "platform", priority="P1",
                         status="deferred"))
    if a.get("domain_status") == "have" and a.get("domain_name"):
        reqs.append(_req("custom-domain", f"Connect {a['domain_name']} (manual DNS change you approve separately)",
                         "platform", priority="P1", status="deferred", risk="high"))
    reqs += [
        _req("seo-basics", "Titles, meta descriptions, sitemap.xml, robots.txt", "seo",
             acceptance=["Preview is never indexable", "Live sitemap lists every page"]),
        _req("accessibility", "Accessible by default", "quality",
             acceptance=["Labels on every input", "Images have alt text", "Visible focus", "Text contrast ≥ 4.5:1"]),
        _req("analytics", "Cookie-free visit and conversion counts", "measurement",
             acceptance=["No personal data in events", "Counts visible in your dashboard"]),
        _req("release-safety", "Preview, approval, health check and one-click rollback", "platform",
             acceptance=["Publishing needs your approval of this exact build", "Failed health check rolls back automatically"]),
    ]

    risks = []
    if cat and cat not in SUPPORTED_CATEGORIES:
        risks.append(Risk(id="category-scope", severity="info", title="Online shop not in first release",
                          mitigation="Building a showcase + enquiry site now; shop features stay in your plan."))
    deferred = [r for r in reqs if r["status"] != "in_scope"]
    return AgentResult(
        agent="requirements_planner",
        summary=f"{sum(r['status'] == 'in_scope' for r in reqs)} requirements in scope, {len(deferred)} for later.",
        artifacts={"requirements": reqs},
        risks=risks,
        tests_required=["unit:lead-validation", "browser:responsive", "browser:a11y-basics"],
    )


def run_architect(a: dict[str, Any]) -> AgentResult:
    stack = {
        "generator": "SiteForge component templates v1 (controlled, pre-reviewed components)",
        "pages": "Static HTML/CSS with a tiny first-party script — fast and hard to break",
        "forms": "Managed Lead API (server-side validation, spam controls, encrypted platform database)",
        "hosting": "SiteForge managed hosting with previews, release history and rollback",
        "email": "Platform email outbox (lead notifications)",
        "analytics": "Cookie-free first-party counters",
    }
    plain = [
        "Your pages are pre-built, so they load quickly and there's no server software for you to maintain.",
        "Enquiries go to a secure database we manage; you get an email for each one.",
        "Every change gets a private preview first. Nothing goes live until you approve it.",
        "If a release has a problem, we can switch back to the previous version in one step.",
    ]
    tradeoffs = [
        {"option": "Managed (recommended)", "ease": "Easiest", "cost": "Included", "flexibility": "Standard sections",
         "lock_in": "Low — export your content and site files anytime"},
        {"option": "Own hosting account (later)", "ease": "Harder", "cost": "Your hosting bill",
         "flexibility": "Higher", "lock_in": "None"},
    ]
    risks = []
    if a.get("hosting") == "own_account":
        risks.append(Risk(id="own-hosting-deferred", severity="info", title="Own hosting is a later release",
                          mitigation="Launching on managed hosting; files are exportable."))
    return AgentResult(agent="platform_architect", summary="Recommended the managed static site + Lead API stack.",
                       artifacts={"stack": stack, "plain_language": plain, "tradeoffs": tradeoffs}, risks=risks)


def category_label(a: dict[str, Any]) -> str:
    return CATEGORIES.get(a.get("site_category") or "", "Business")
