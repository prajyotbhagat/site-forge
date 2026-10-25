"""Frontend Design and Content & SEO agents."""
from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from ..onboarding.schema import PAGES, PALETTES
from .contracts import AgentResult, Risk
from .llm import LLMProvider, ProviderUnavailable

# ------------------------------------------------------------------ colour utilities


def _hex_to_rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _lum(h: str) -> float:
    def ch(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _hex_to_rgb(h))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _darken(h: str, f: float = 0.9) -> str:
    r, g, b = (int(c * 255 * f) for c in _hex_to_rgb(h))
    return f"#{r:02x}{g:02x}{b:02x}"


def ensure_contrast(fg: str, bg: str, target: float = 4.5) -> str:
    """Darken bg until fg on bg meets WCAG AA."""
    for _ in range(20):
        if contrast(fg, bg) >= target:
            return bg
        bg = _darken(bg)
    return bg


STYLE_TOKENS = {
    "formal": {"font_heading": "Georgia, 'Iowan Old Style', 'Times New Roman', serif",
               "font_body": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
               "heading_weight": "600", "tracking": "0", "radius": "3px", "bg": "#fbfaf7", "surface": "#f2efe8"},
    "modern": {"font_heading": "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
               "font_body": "ui-sans-serif, system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
               "heading_weight": "800", "tracking": "-0.02em", "radius": "10px", "bg": "#ffffff", "surface": "#f3f4f8"},
    "warm": {"font_heading": "ui-rounded, 'SF Pro Rounded', 'Nunito', system-ui, sans-serif",
             "font_body": "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
             "heading_weight": "700", "tracking": "-0.01em", "radius": "18px", "bg": "#fffaf4", "surface": "#f7ece0"},
}

CTA_LABELS = {
    "calls": "Call now",
    "enquiries": "Request a quote",
    "bookings": "Request a booking",
    "waitlist": "Join the waitlist",
    "credibility": "Get in touch",
    "portfolio": "Start a project",
}


def cta_label(a: dict[str, Any]) -> str:
    goal = a.get("primary_goal") or "enquiries"
    if goal == "enquiries" and a.get("site_category") == "professional_service":
        return "Request a consultation"
    return CTA_LABELS.get(goal, "Get in touch")


def run_frontend(a: dict[str, Any]) -> AgentResult:
    style = a.get("style") or "modern"
    tokens = dict(STYLE_TOKENS.get(style, STYLE_TOKENS["modern"]))
    pal = PALETTES.get(a.get("palette") or "navy", PALETTES["navy"])[1]
    text = "#1d1f23"
    primary = ensure_contrast("#ffffff", pal["primary"])
    accent = pal["accent"]
    on_accent = "#111111" if contrast("#111111", accent) >= contrast("#ffffff", accent) else "#ffffff"
    tokens.update({"primary": primary, "accent": accent, "on_accent": on_accent, "text": text, "muted": "#555b66"})
    risks = []
    if primary != pal["primary"]:
        risks.append(Risk(id="contrast-adjusted", severity="info", title="Colour darkened slightly for readability",
                          detail=f"{pal['primary']} → {primary} to reach 4.5:1 contrast with white text."))

    pages = [p for p in (a.get("pages") or ["home", "contact"]) if p in PAGES]
    if "home" not in pages:
        pages.insert(0, "home")
    feats = a.get("features") or []
    has_form = "contact_form" in feats
    if has_form and "contact" not in pages:
        pages.append("contact")
    offer_label = {"startup": "Product", "portfolio": "Work"}.get(a.get("site_category") or "", "Services")
    nav = [{"page": p, "label": offer_label if p == "services" else PAGES[p],
            "href": "index.html" if p == "home" else f"{p}.html"} for p in pages]
    goal = a.get("primary_goal") or "enquiries"
    home_sections = ["hero", "services"]
    if a.get("differentiators"):
        home_sections.append("why")
    if a.get("proof_points"):
        home_sections.append("proof")
    if a.get("service_area"):
        home_sections.append("area")
    home_sections.append("form" if has_form and goal in {"enquiries", "waitlist", "bookings"} else "cta")

    return AgentResult(
        agent="frontend_design",
        summary=f"{style.title()} template, {len(pages)} pages, primary action “{cta_label(a)}”.",
        artifacts={"tokens": tokens, "template": f"sf-{style}-v1", "pages": pages, "nav": nav,
                   "home_sections": home_sections, "cta_label": cta_label(a),
                   "extra_pages": (["privacy", "thanks"] if has_form else []),
                   "breakpoints": {"mobile": 375, "desktop": 1280}},
        risks=risks,
        tests_required=["browser:responsive-375", "browser:responsive-1280", "a11y:contrast"],
    )


# ------------------------------------------------------------------ content & SEO

CLAIM_PATTERNS = [
    (re.compile(r"\b\d+\s*\+?\s*(years?|yrs)\b", re.I), "years of experience"),
    (re.compile(r"\b\d[\d,]*\s*\+?\s*(happy\s+)?(clients|customers|projects|jobs|reviews)\b", re.I), "customer counts"),
    (re.compile(r"\b(award[- ]winning|awarded|winner of)\b", re.I), "awards"),
    (re.compile(r"\b(certified|licensed|accredited|registered with)\b", re.I), "credentials"),
    (re.compile(r"\b(guarantee[sd]?|money[- ]back)\b", re.I), "guarantees"),
    (re.compile(r"(#\s?1\b|\bnumber one\b|\bbest in\b|\bleading\b|\btop[- ]rated\b)", re.I), "superlatives"),
    (re.compile(r"\b[1-5](\.\d)?\s*(stars?|★)|\brated\b", re.I), "ratings"),
    (re.compile(r"\b\d+\s?%", re.I), "statistics"),
    (re.compile(r"(₹|\$|£|€|rs\.?\s?)\s?\d", re.I), "prices"),
    (re.compile(r"\btestimonial|\bsays\b.*[\"“]", re.I), "testimonials"),
]


def check_claims(copy_strings: list[str], owner_text: str) -> list[dict]:
    """Flags claims in generated copy that are not backed by text the owner supplied."""
    owner = owner_text.lower()
    flags = []
    for s in copy_strings:
        for pat, kind in CLAIM_PATTERNS:
            for m in pat.finditer(s):
                if m.group(0).lower() not in owner:
                    flags.append({"text": s[:120], "match": m.group(0), "kind": kind})
    return flags


def _flatten(obj) -> list[str]:
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _flatten(v)]
    if isinstance(obj, list):
        return [s for v in obj for s in _flatten(v)]
    return []


def _trim(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


class _LLMCopy(BaseModel):
    hero_headline: str
    hero_sub: str
    about: str
    service_blurbs: list[str]


def run_content(a: dict[str, Any], business_type: str | None, provider: LLMProvider) -> AgentResult:
    name = a.get("business_name") or "Your business"
    btype = (business_type or "").strip()
    area = a.get("service_area")
    remote = a.get("remote_service") == "yes"
    services: list[str] = a.get("services") or []
    cat = a.get("site_category")
    diff = a.get("differentiators")
    audience = a.get("audience")
    proof = a.get("proof_points")
    goal = a.get("primary_goal") or "enquiries"
    feats = a.get("features") or []
    retention = a.get("lead_retention_days") or "180"

    btype_title = btype[:1].upper() + btype[1:] if btype else ""
    if cat in {"local_service", "professional_service"} and btype_title:
        headline = f"{btype_title}{' in ' + area if area else ''}"
        if audience and cat == "professional_service":
            headline = f"{btype_title} for {audience.rstrip('.').lower()}"
    elif cat == "startup":
        headline = diff.split(".")[0] if diff else name
    else:
        headline = name
    contact_line = {
        "calls": "Call us and tell us what you need.",
        "waitlist": "Join the waitlist to hear as soon as we launch.",
        "bookings": "Send a booking request and we'll confirm a time.",
    }.get(goal, "Tell us what you need and we'll get back to you.")
    sub = f"{name}. " + (diff.split(".")[0].strip() + ". " if diff and cat != "startup" else "") + contact_line
    about = f"{name} is a {btype or 'small'} business" + (f" serving {area}" if area else "") + "."
    if remote:
        about += " We also work with clients remotely."
    if audience:
        about += f" We work with {audience.rstrip('.').lower()}."
    if diff:
        about += f" {diff.strip().rstrip('.')}."
    def _lc(x: str) -> str:
        return x if x[:2].isupper() else x[:1].lower() + x[1:]
    blurbs = [f"Ask us about {_lc(s)} — tell us what you need and we'll reply with next steps."
              for s in services]

    owner_text = " ".join(str(a.get(k) or "") for k in
                          ("business_description", "differentiators", "proof_points", "services", "audience",
                           "business_name", "service_area"))
    notes: list[str] = []
    try:
        llm = provider.complete_json(
            "Write concise, plain website copy for this business. Use ONLY facts present in the content. Do not "
            "invent numbers, years, awards, certifications, ratings, prices, guarantees or testimonials. "
            f"Return hero_headline (≤ 8 words), hero_sub (≤ 25 words), about (≤ 80 words), and exactly "
            f"{len(services)} service_blurbs (≤ 25 words each) in the same order as the services.",
            str({k: a.get(k) for k in ("business_name", "services", "service_area", "audience", "differentiators",
                                       "proof_points", "primary_goal")}) + f" business_type={btype}",
            _LLMCopy)
        cand = [llm.hero_headline, llm.hero_sub, llm.about, *llm.service_blurbs]
        if len(llm.service_blurbs) == len(services) and not check_claims(cand, owner_text):
            headline, sub, about, blurbs = _trim(llm.hero_headline, 70), _trim(llm.hero_sub, 180), _trim(llm.about, 700), \
                [_trim(b, 200) for b in llm.service_blurbs]
            notes.append("Copy drafted by the language model and passed the unsupported-claims check.")
        else:
            notes.append("A model-drafted version was discarded because it contained claims you hadn't provided.")
    except ProviderUnavailable:
        notes.append("Copy drafted from your answers using templates.")

    faqs = []
    if area:
        faqs.append({"q": "Which areas do you cover?",
                     "a": f"We serve {area}." + (" We also work with clients remotely." if remote else "")})
    if "contact_form" in feats or "click_to_call" in feats:
        how = []
        if "click_to_call" in feats and a.get("phone"):
            how.append(f"call us on {a['phone']}")
        if "contact_form" in feats:
            how.append("send us a message using the enquiry form")
        faqs.append({"q": "How do I get in touch?", "a": "You can " + " or ".join(how) + "."})
    if "contact_form" in feats:
        faqs.append({"q": "What happens to the details I send?",
                     "a": f"We use them only to reply to your enquiry. They are stored securely and deleted after "
                          f"{int(retention) // 30} months. See our privacy notice for details."})
    if services:
        faqs.append({"q": "What services do you offer?", "a": ", ".join(services) + "."})

    city = f" in {area}" if area else ""
    meta = {
        "home": {"title": _trim(f"{name} — {btype_title or 'Home'}{city}", 60),
                 "description": _trim(f"{name}: {', '.join(services[:3]) or btype}{city}. {contact_line}", 155)},
        "services": {"title": _trim(f"{'Product' if cat == 'startup' else 'Services'} | {name}", 60),
                     "description": _trim(f"{', '.join(services) or 'What we offer'}{city}.", 155)},
        "about": {"title": _trim(f"About | {name}", 60), "description": _trim(about, 155)},
        "contact": {"title": _trim(f"Contact | {name}", 60), "description": _trim(f"Get in touch with {name}{city}.", 155)},
        "faq": {"title": _trim(f"FAQ | {name}", 60), "description": _trim(f"Answers to common questions about {name}.", 155)},
        "privacy": {"title": _trim(f"Privacy notice | {name}", 60), "description": "How we handle the details you send us."},
        "thanks": {"title": _trim(f"Thank you | {name}", 60), "description": "Your message has been received."},
    }
    proof_items = [p.strip(" -•\n") for p in re.split(r"[\n;]+", proof) if p.strip(" -•\n")] if proof else []
    copy = {"hero_headline": _trim(headline, 70), "hero_sub": _trim(sub, 200), "about": about, "contact_line": contact_line,
            "services": [{"name": s, "blurb": b} for s, b in zip(services, blurbs)],
            "why": [d.strip() for d in re.split(r"[.;\n]+|,\s(?=[A-Z0-9])", diff or "") if d.strip()][:4],
            "proof": proof_items[:6], "faqs": faqs, "meta": meta, "search_phrases": a.get("search_phrases") or []}

    flags = check_claims(_flatten({k: v for k, v in copy.items() if k not in {"proof", "meta"}}), owner_text)
    risks = [Risk(id=f"claim-{i}", severity="high", title=f"Unsupported claim: {f['match']}",
                  detail=f["text"], mitigation="Remove it or confirm it in your answers.", blocking=True)
             for i, f in enumerate(flags)]
    schema_org = {"@context": "https://schema.org",
                  "@type": "LocalBusiness" if cat in {"local_service", "professional_service"} else "Organization",
                  "name": name}
    if area:
        schema_org["areaServed"] = area
    if a.get("phone") and "click_to_call" in feats:
        schema_org["telephone"] = a["phone"]
    return AgentResult(
        agent="content_seo",
        status="blocked" if risks else "ok",
        summary="Drafted page copy, titles and descriptions using only facts you provided.",
        artifacts={"copy": copy, "schema_org": schema_org, "notes": notes},
        risks=risks,
        evidence=notes,
        requires_human_approval=True,
        risk_tier="medium",
    )
