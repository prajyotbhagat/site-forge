"""The creative team: Strategist → Information Architect → Copywriter → Art Director.

Each agent:
  1. reads the shared knowledge board (facts from onboarding + everything earlier agents concluded),
  2. reasons — with the language model when one is configured, otherwise with the industry playbooks,
  3. returns typed output plus `knowledge` entries (decision + rationale + what it was based on),
  4. is checked by deterministic guards (allowed values, no unsupported claims) before anything is used.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..onboarding.schema import PALETTES
from ..sitegen.spec import SECTION_VARIANTS
from .contracts import AgentResult, Risk
from .design import check_claims, contrast
from .llm import LLMProvider, ProviderUnavailable
from .playbooks import describe_service, icon_for, pick_playbook

Facts = dict[str, Any]


def K(kind: str, key: str, content: Any, rationale: str, based_on: list[str] | None = None, confidence: float = 1.0) -> dict:
    return {"kind": kind, "key": key, "content": content, "rationale": rationale, "based_on": based_on or [],
            "confidence": confidence}


def owner_text(f: Facts) -> str:
    """Everything the owner actually said — the only source of publishable claims."""
    parts = []
    for k, v in f.items():
        if k in {"business_description", "differentiators", "proof_points", "services", "audience", "business_name",
                 "service_area", "phone"} or k.startswith("kb_"):
            parts.append(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v)
    return " ".join(parts)


def _first_sentence(s: str | None) -> str:
    return re.split(r"(?<=[.!?])\s", (s or "").strip())[0].rstrip(". ") if s else ""


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _lc(s: str) -> str:
    return s if s[:2].isupper() else s[:1].lower() + s[1:]


# ====================================================================== 1. Strategist

class _LLMStrategy(BaseModel):
    positioning: str
    audience: str
    audience_pains: list[str] = Field(max_length=5)
    motivators: list[str] = Field(max_length=5)
    personality: list[str] = Field(min_length=2, max_length=4)
    voice: str
    primary_message: str


def run_strategist(f: Facts, business_type: str | None, provider: LLMProvider) -> AgentResult:
    pb = pick_playbook(business_type, f.get("business_description") or "", f.get("site_category"))
    goal = f.get("primary_goal") or "enquiries"
    emergency = f.get("kb_emergency", "")
    urgency = "high" if emergency.startswith("yes") or (goal == "calls" and f.get("site_category") == "local_service") else \
        "low" if goal in {"credibility", "portfolio", "waitlist"} else "medium"
    action = "call" if goal == "calls" and f.get("phone") else "waitlist" if goal == "waitlist" else "form"
    name = f.get("business_name") or "the business"
    btype = business_type or pb["label"]
    audience = f.get("audience") or f.get("kb_who_for") or pb["audience"]
    area = f.get("service_area")

    proof = []  # owner-confirmed trust facts the copy may use
    if area:
        proof.append({"key": "area", "text": f"Serving {area}", "icon": "pin"})
    if f.get("remote_service") == "yes" or f.get("kb_online") in {"yes", "partly"}:
        proof.append({"key": "remote", "text": "Online / remote available", "icon": "globe"})
    if emergency == "yes_247":
        proof.append({"key": "emergency", "text": "24/7 urgent call-outs", "icon": "clock"})
    elif emergency == "yes_hours":
        proof.append({"key": "emergency", "text": "Urgent call-outs in working hours", "icon": "clock"})
    q = f.get("kb_quote")
    if q:
        proof.append({"key": "pricing", "icon": "rupee", "text": {"free_quote": "Free quote before work starts",
                     "visit_fee": "Small visit fee, agreed upfront", "fixed_fee": "Fixed fees", "hourly": "Clear hourly rates",
                     "varies": "Price agreed before work starts"}[q]})
    if f.get("kb_first_meeting") == "free":
        proof.append({"key": "first_meeting", "text": "First conversation is free", "icon": "chat"})
    if f.get("kb_since") and re.search(r"(19|20)\d\d", f["kb_since"]):
        proof.append({"key": "since", "text": f"Since {re.search(r'(19|20)\d\d', f['kb_since']).group(0)}", "icon": "calendar"})
    if f.get("kb_languages"):
        proof.append({"key": "languages", "text": f"We speak {f['kb_languages']}", "icon": "languages"})
    if f.get("kb_credentials"):
        proof.append({"key": "credentials", "text": f["kb_credentials"], "icon": "badge"})
    if f.get("kb_warranty"):
        proof.append({"key": "warranty", "text": f["kb_warranty"], "icon": "shield"})
    if f.get("kb_hours"):
        proof.append({"key": "hours", "text": f["kb_hours"], "icon": "clock"})
    if f.get("kb_walkins"):
        proof.append({"key": "walkins", "icon": "home", "text": {"walkins": "Walk-ins welcome", "appointments": "By appointment",
                                                                 "both": "Walk-ins & appointments"}[f["kb_walkins"]]})
    gaps = []
    if not f.get("proof_points"):
        gaps.append("No verified reviews or credentials yet — adding even one real review would help a lot.")
    if not f.get("differentiators"):
        gaps.append("No stated differentiator — the site leans on clarity and convenience instead.")

    strategy = {
        "playbook": pb.get("id"),
        "business_type": btype,
        "positioning": f"{name}: {btype} for {audience}" + (f" in {area}" if area else ""),
        "audience": audience,
        "audience_pains": pb.get("pains", [])[:4],
        "motivators": pb.get("motivators", [])[:4],
        "personality": pb.get("personality", ["clear", "friendly", "reliable"]),
        "voice": {"high": "direct and reassuring — short sentences, lead with the action",
                  "medium": "clear and confident, plain words, no jargon",
                  "low": "considered and personal, a little more storytelling"}[urgency],
        "primary_message": _first_sentence(f.get("differentiators")) or f"{_cap(btype)} you can rely on" if not f.get("differentiators") else _first_sentence(f.get("differentiators")),
        "urgency": urgency,
        "conversion": action,
        "proof": proof,
        "gaps": gaps,
        "regulated": pb.get("regulated"),
    }
    # "you can rely on" is a soft promise; keep the message neutral unless the owner supplied one
    if not f.get("differentiators"):
        strategy["primary_message"] = f"Clear, straightforward {btype}" + (f" in {area}" if area else "")
    notes = ["Strategy reasoned from the industry playbook and your answers."]
    try:
        llm = provider.complete_json(
            "You are a brand strategist for a small-business website. Using ONLY the facts provided, produce a "
            "positioning statement, the audience, their main worries, what makes them choose a provider, 3 personality "
            "adjectives, a voice description and the single primary message. Do not invent facts or claims.",
            json.dumps({"facts": f, "business_type": btype, "playbook_hints": {k: pb.get(k) for k in ("pains", "motivators", "personality")}}),
            _LLMStrategy)
        if not check_claims([llm.positioning, llm.primary_message], owner_text(f)):
            strategy.update({k: getattr(llm, k) for k in ("positioning", "audience", "audience_pains", "motivators",
                                                           "personality", "voice", "primary_message")})
            notes = ["Strategy reasoned by the language model from the shared board, checked for unsupported claims."]
        else:
            notes.append("A model draft was discarded because it made claims you hadn't provided.")
    except ProviderUnavailable:
        pass

    knowledge = [
        K("insight", "strategy.positioning", strategy["positioning"], "Who it's for and where, from your answers.",
          ["business_name", "audience", "service_area"]),
        K("insight", "strategy.audience_pains", strategy["audience_pains"],
          f"What {btype} customers typically worry about — the copy answers these.", ["playbook"]),
        K("decision", "strategy.urgency", urgency,
          {"high": "Customers usually need help now, so the phone/primary action must be visible everywhere.",
           "medium": "Customers compare options, so clarity and reassurance matter most.",
           "low": "Visitors browse before deciding, so story and credibility come first."}[urgency],
          ["primary_goal", "kb_emergency", "site_category"]),
        K("decision", "strategy.conversion", action, f"Main goal is {goal}.", ["primary_goal", "phone"]),
        K("insight", "strategy.proof", [p["text"] for p in proof],
          "Only facts you confirmed are used as trust signals.", [f"kb_{p['key']}" for p in proof]),
        K("insight", "strategy.voice", strategy["voice"], f"Matches urgency ({urgency}) and personality "
          f"({', '.join(strategy['personality'])}).", ["strategy.urgency"]),
    ]
    if gaps:
        knowledge.append(K("question", "strategy.gaps", gaps, "Things that would make the site stronger.", []))
    return AgentResult(agent="brand_strategist", summary=f"Positioned as {strategy['positioning']}; urgency {urgency}.",
                       artifacts={"strategy": strategy, "knowledge": knowledge}, evidence=notes)


# ====================================================================== 2. Information architect

def run_architect(f: Facts, board: dict, business_type: str | None) -> AgentResult:
    strat = board["strategy"]
    pb = pick_playbook(business_type, f.get("business_description") or "", f.get("site_category"))
    feats = f.get("features") or []
    has_form = "contact_form" in feats
    goal = f.get("primary_goal") or "enquiries"
    services = f.get("services") or []
    wanted_pages = [p for p in (f.get("pages") or ["home", "contact"])]
    reasons: list[dict] = []

    def have(section: str) -> tuple[bool, str]:
        if section == "trust_bar":
            return len(strat["proof"]) >= 2, f"{len(strat['proof'])} confirmed trust facts (need 2+)"
        if section == "services":
            return bool(services), f"{len(services)} services"
        if section == "process":
            return bool(pb.get("process")), "typical way of working for this trade (marked for your review)"
        if section == "why":
            items = _why_items(f, strat)
            return len(items) >= 2, f"{len(items)} owner-stated reasons to choose you"
        if section == "area":
            return bool(f.get("service_area")), "service area known" if f.get("service_area") else "no service area"
        if section == "about":
            if "about" in wanted_pages:
                return False, "has its own About page — not repeated on home"
            return f.get("site_category") in {"professional_service", "portfolio"}, "credibility matters for this category"
        if section == "faq":
            return len(_faqs(f, strat)) >= 3, f"{len(_faqs(f, strat))} answerable questions from your facts"
        if section == "problem":
            return bool(f.get("kb_problem")), "you described the problem you solve"
        if section == "proof":
            return bool(f.get("proof_points")), "you supplied reviews/credentials"
        return True, ""

    order = list(pb.get("sections") or ["hero", "services", "faq", "cta"])
    if f.get("proof_points") and "proof" not in order:
        order.insert(order.index("services") + 1 if "services" in order else 2, "proof")
    home: list[str] = []
    for s in order:
        ok, why = have(s)
        reasons.append({"section": s, "included": ok, "why": why})
        if ok and (s != "cta" or not home or home[-1] != "cta"):
            home.append(s)
    if has_form and goal in {"enquiries", "waitlist", "bookings"}:
        if home and home[-1] == "cta":
            home[-1] = "contact"
        else:
            home.append("contact")
        reasons.append({"section": "contact", "included": True, "why": "the goal is enquiries, so the form sits on the home page"})
    if "faq" in home and "faq" in wanted_pages and home.index("faq") > 0:
        pass  # keep a short FAQ on home too; full list on the FAQ page

    pages = [{"slug": "index", "sections": home}]
    if "services" in wanted_pages and len(services) >= 2:
        pages.append({"slug": "services", "sections": ["page_head", "services", "process" if pb.get("process") else None, "cta"]})
    if "about" in wanted_pages:
        pages.append({"slug": "about", "sections": ["page_head", "about", "why" if len(_why_items(f, strat)) >= 2 else None, "cta"]})
    if "faq" in wanted_pages:
        pages.append({"slug": "faq", "sections": ["page_head", "faq", "cta"]})
    if "contact" in wanted_pages or has_form:
        pages.append({"slug": "contact", "sections": ["page_head", "contact"] if has_form else ["page_head", "cta"]})
    if has_form:
        pages += [{"slug": "privacy", "sections": ["page_head", "prose"]}, {"slug": "thanks", "sections": ["thanks"]}]
    for p in pages:
        p["sections"] = [s for s in p["sections"] if s]

    knowledge = [K("decision", "ia.home_sections", home,
                   f"Order follows how {pb['label']} customers decide, adjusted for urgency ({strat['urgency']}); "
                   "sections without real content were left out rather than padded.",
                   ["strategy.urgency", "strategy.proof", "services", "differentiators"]),
                 K("decision", "ia.pages", [p["slug"] for p in pages], "Pages you chose, plus privacy and thank-you pages for the form.",
                   ["pages", "features"]),
                 K("insight", "ia.considered", reasons, "Every candidate section and why it was kept or dropped.", [])]
    return AgentResult(agent="information_architect", summary=f"Home: {' → '.join(home)}",
                       artifacts={"pages": pages, "knowledge": knowledge})


def _why_items(f: Facts, strat: dict, exclude_trust: bool = True) -> list[dict]:
    """Reasons to choose you. Facts already shown in the trust bar are not repeated here."""
    items = []
    in_trust = {p["text"] for p in strat["proof"]} if exclude_trust and len(strat["proof"]) >= 2 else set()
    for d in re.split(r"[.;\n]+|,\s(?=[A-Z0-9])", f.get("differentiators") or ""):
        d = d.strip()
        if d:
            items.append({"title": _cap(d), "text": "", "icon": icon_for(d, "check")})
    for p in strat["proof"]:
        if p["key"] in {"pricing", "emergency", "first_meeting", "languages", "warranty", "credentials", "since"} and \
                p["text"] not in in_trust and not any(p["text"].lower() in i["title"].lower() for i in items):
            items.append({"title": p["text"], "text": "", "icon": p["icon"]})
    return items[:6]


def _faqs(f: Facts, strat: dict) -> list[dict]:
    out = []
    area = f.get("service_area")
    feats = f.get("features") or []
    remote = f.get("remote_service") == "yes" or f.get("kb_online") in {"yes", "partly"}
    if area:
        out.append({"q": "Which areas do you cover?", "a": f"We work across {area}." + (" We also work with clients online." if remote else "")})
    q = f.get("kb_quote")
    if q:
        out.append({"q": "How much will it cost?", "a": {
            "free_quote": "We'll give you a free quote before any work starts, so there are no surprises.",
            "visit_fee": "There's a small visit or inspection fee, which we'll tell you upfront. You'll get a price before any work starts.",
            "fixed_fee": "We work on fixed fees, agreed before we start.",
            "hourly": "We charge by the hour and agree an estimate with you first.",
            "varies": "It depends on the job — tell us what you need and we'll explain the cost before starting."}[q]})
    em = f.get("kb_emergency")
    if em and em != "no":
        out.append({"q": "Can you help urgently?", "a": "Yes — " + ("we take urgent call-outs 24/7." if em == "yes_247" else
                                                                     "we take urgent call-outs during working hours.")
                    + (f" Call {f['phone']}." if f.get("phone") else "")})
    if f.get("kb_hours"):
        out.append({"q": "When are you open?", "a": f["kb_hours"] + "."})
    if f.get("kb_first_meeting") == "free":
        out.append({"q": "Is the first conversation free?", "a": "Yes. Tell us what you need and we'll explain how we can help, with no obligation."})
    if f.get("kb_walkins"):
        out.append({"q": "Do I need an appointment?", "a": {"walkins": "No — walk-ins are welcome.", "appointments": "Yes, we work by appointment. Get in touch to book a time.",
                                                             "both": "Walk-ins are welcome, and you can also book ahead."}[f["kb_walkins"]]})
    if f.get("kb_languages"):
        out.append({"q": "Which languages do you speak?", "a": f"{f['kb_languages']}."})
    how = []
    if f.get("phone") and ({"click_to_call", "whatsapp"} & set(feats) or f.get("primary_goal") == "calls"):
        how.append(f"call {f['phone']}")
    if "contact_form" in feats:
        how.append("send a message using the form on this site")
    if how:
        out.append({"q": "How do I get in touch?", "a": "You can " + " or ".join(how) + "."})
    if f.get("services"):
        q = f"What does {f.get('business_name') or 'it'} do?" if f.get("site_category") == "startup" else "What services do you offer?"
        out.append({"q": q, "a": ", ".join(f["services"]) + "."})
    if "contact_form" in feats:
        days = int(f.get("lead_retention_days") or 180)
        out.append({"q": "What happens to the details I send?", "a": f"We use them only to reply to you. They're stored securely and deleted after {days // 30} months."})
    return out


# ====================================================================== 3. Copywriter

class _LLMCopy(BaseModel):
    hero_headline: str
    hero_sub: str
    service_blurbs: list[str]
    about: list[str]
    cta_heading: str
    cta_text: str


def _headline_candidates(f: Facts, strat: dict, btype: str) -> list[dict]:
    area = f.get("service_area")
    short_area = re.split(r"\s+(and|&|,)\s+", area)[0] if area else None
    services = f.get("services") or []
    name = f.get("business_name") or ""
    em = f.get("kb_emergency", "")
    cands = []
    T = _cap(btype)
    if short_area:
        cands.append(f"{T} in {short_area}")
        if em == "yes_247":
            cands.append(f"24/7 {btype} in {short_area}")
        if services:
            cands.append(f"{_cap(services[0])} in {short_area}")
    if f.get("kb_problem"):
        cands.append(_first_sentence(f["kb_problem"]))
    if f.get("differentiators"):
        cands.append(_first_sentence(f["differentiators"]))
    if f.get("audience") or f.get("kb_who_for"):
        aud = (f.get("kb_who_for") or f.get("audience")).rstrip(".")
        subject = name if f.get("site_category") in {"startup", "portfolio"} and name else T
        cands.append(f"{subject} for {_lc(aud)}")
    cands.append(name or T)
    scored = []
    for c in dict.fromkeys(c for c in cands if c):
        score = 0
        L = len(c)
        score += 3 if 14 <= L <= 42 else 1 if L <= 60 else -3
        score += 2 if short_area and short_area.lower() in c.lower() else 0                  # local search intent
        score += 2 if any(s.lower()[:6] in c.lower() for s in services[:3]) or (btype.lower() in c.lower() and f.get("site_category") not in {"startup", "portfolio"}) else 0
        score += 2 if strat["urgency"] == "high" and "24/7" in c else 0
        score += 1 if f.get("differentiators") and c == _first_sentence(f["differentiators"]) and strat["urgency"] != "high" else 0
        score += 2 if f.get("kb_problem") and c == _first_sentence(f.get("kb_problem")) else 0
        score -= 4 if check_claims([c], owner_text(f)) else 0
        scored.append({"text": c, "score": score})
    return sorted(scored, key=lambda x: -x["score"])


def run_copywriter(f: Facts, board: dict, pages: list[dict], provider: LLMProvider) -> AgentResult:
    strat = board["strategy"]
    pb = pick_playbook(strat.get("business_type"), f.get("business_description") or "", f.get("site_category"))
    btype = strat["business_type"]
    name = f.get("business_name") or "Our business"
    services = f.get("services") or []
    area = f.get("service_area")
    goal = f.get("primary_goal") or "enquiries"
    phone = f.get("phone")
    feats = f.get("features") or []
    urgency = strat["urgency"]

    cands = _headline_candidates(f, strat, btype)
    headline = cands[0]["text"]
    pains = strat.get("audience_pains") or []
    if urgency == "high" and services:
        lead = f"{_cap(services[0].split('&')[0].strip())}, {_lc(services[1]) if len(services) > 1 else 'and more'}?"
        lead = f"Need help with {_lc(services[0])}?"
    else:
        lead = ""
    action_line = {"call": f"Call {name} and tell us what's wrong — we'll take it from there." if phone else "Tell us what you need.",
                   "waitlist": "Join the waitlist to hear the moment we launch.",
                   "form": "Tell us what you need and we'll get back to you with next steps."}[strat["conversion"]]
    diff = _first_sentence(f.get("differentiators"))
    sub = " ".join(x for x in [lead, (diff + ".") if diff and diff != headline else "", action_line] if x)
    if f.get("kb_problem") and headline != _first_sentence(f["kb_problem"]):
        sub = _first_sentence(f["kb_problem"]) + ". " + sub

    blurbs = []
    for s in services:
        d = describe_service(pb, s)
        # no neutral description known → leave the text empty rather than pad with filler
        blurbs.append({"name": s, "text": d[1] if d else "",
                       "icon": d[0] if d else icon_for(s, pb.get("icon", "check"))})
    art_ = "an" if btype[:1].lower() in "aeiou" else "a"
    about = [f"{name} is {art_} {btype} business" + (f" serving {area}" if area else "") + "."]
    if f.get("kb_since") and re.search(r"(19|20)\d\d", f["kb_since"]):
        about.append(f"We've been working since {re.search(r'(19|20)\d\d', f['kb_since']).group(0)}.")
    if f.get("audience"):
        about.append(f"Most of our customers are {_lc(f['audience'].rstrip('.'))}.")
    if f.get("kb_credentials"):
        about.append(f"{f['kb_credentials'].rstrip('.')}.")
    cta_heading = {"high": f"Need {btype} help now?", "medium": "Ready to get started?", "low": "Let's talk"}[urgency]
    if goal == "waitlist":
        cta_heading = "Be first in line"
    cta_text = action_line
    notes = ["Copy drafted from your answers, the strategy and the playbook."]

    try:
        llm = provider.complete_json(
            "You are the copywriter on a website team. Read the team's shared board (strategy, audience worries, voice) and "
            "the owner's facts, then write: hero_headline (≤ 8 words), hero_sub (≤ 30 words), one service_blurb (≤ 22 "
            f"words) for each of these services in order: {services}, about (2–4 short paragraphs), cta_heading (≤ 6 "
            "words), cta_text (≤ 20 words). Use ONLY facts on the board. Never invent numbers, years, ratings, awards, "
            "guarantees, prices or testimonials. Speak to the audience's worries in the brand voice.",
            json.dumps({"board": board, "facts": f}, default=str), _LLMCopy)
        drafted = [llm.hero_headline, llm.hero_sub, *llm.service_blurbs, *llm.about, llm.cta_heading, llm.cta_text]
        if len(llm.service_blurbs) == len(services) and not check_claims(drafted, owner_text(f)):
            headline, sub, about, cta_heading, cta_text = llm.hero_headline, llm.hero_sub, llm.about, llm.cta_heading, llm.cta_text
            for b, t in zip(blurbs, llm.service_blurbs):
                b["text"] = t
            notes = ["Copy written by the language model from the shared board; passed the unsupported-claims check."]
        else:
            notes.append("Discarded a model draft that contained claims you hadn't provided.")
    except ProviderUnavailable:
        pass

    primary_cta = {"label": board.get("cta_label") or "Get in touch", "href": (f"tel:{_tel(phone)}" if strat["conversion"] == "call" and phone
                   else "contact.html#enquiry" if "contact_form" in feats else "contact.html"),
                   "event": "call_click" if strat["conversion"] == "call" and phone else "cta_click"}
    secondary = ({"label": f"See {'our ' if True else ''}{'product' if f.get('site_category') == 'startup' else 'services'}", "href": "services.html", "event": "cta_click"}
                 if any(p["slug"] == "services" for p in pages) else
                 {"label": "Send a message", "href": "contact.html#enquiry", "event": "cta_click"} if strat["conversion"] == "call" and "contact_form" in feats else None)
    faqs = _faqs(f, strat)
    content: dict[str, dict] = {
        "hero": {"eyebrow": ("Early access" if goal == "waitlist" else f"{_cap(btype)}" + (f" · {re.split(r'\s+(and|&|,)\s+', area)[0]}" if area else "")),
                 "headline": headline, "sub": sub, "primary_cta": primary_cta, "secondary_cta": secondary,
                 "chips": ([] if any("trust_bar" in p["sections"] for p in pages) else
                           [p["text"] for p in strat["proof"] if p["key"] in {"emergency", "pricing", "since", "first_meeting"}][:3]),
                 "art_icon": pb.get("icon", "sparkle"), "art_icons": [b["icon"] for b in blurbs[:3]]},
        "trust_bar": {"items": [{"icon": p["icon"], "text": p["text"]} for p in strat["proof"]][:5]},
        "services": {"heading": "Product" if f.get("site_category") == "startup" else "What we do",
                     "intro": "" if f.get("site_category") in {"startup", "portfolio"} else
                              f"Not sure which you need? {'Call us' if phone and strat['conversion'] == 'call' else 'Ask us'} and we'll point you the right way.",
                     "items": blurbs},
        "process": {"heading": "How it works", "steps": [{"title": t, "text": x} for t, x in _process(pb, f)]},
        "why": {"heading": f"Why people choose {name}", "items": _why_items(f, strat)},
        "problem": {"heading": "The problem", "problem": _first_sentence(f.get("kb_problem")) + ".", "solution": diff + "." if diff else f"{name} makes it simple.",
                    "points": [b["name"] for b in blurbs][:4]},
        "proof": {"heading": "Credentials & reviews", "items": [p.strip(" -•\n") for p in re.split(r"[\n;]+", f.get("proof_points") or "") if p.strip(" -•\n")][:6]},
        "area": {"heading": "Where we work", "text": f"We serve {area}." + (" We also work with clients online." if strat and any(p['key'] == 'remote' for p in strat['proof']) else "") if area else "",
                 "places": [p.strip() for p in re.split(r",|\band\b|&", area or "") if p.strip() and "nearby" not in p.lower()][:6],
                 "remote": any(p["key"] == "remote" for p in strat["proof"])},
        "about": {"heading": f"About {name}", "paragraphs": about,
                  "facts": [{"label": l, "value": v} for l, v in [("Based in", area), ("Started", (re.search(r"(19|20)\d\d", f.get("kb_since") or "") or [None])[0] if f.get("kb_since") else None),
                                                                    ("Languages", f.get("kb_languages")), ("Hours", f.get("kb_hours"))] if v]},
        "faq": {"heading": "Common questions", "items": faqs},
        "cta": {"heading": cta_heading, "text": cta_text, "primary_cta": primary_cta, "phone": phone if phone and strat["conversion"] == "call" else None},
        "contact": {"heading": {"waitlist": "Join the waitlist", "bookings": "Request a booking"}.get(goal, "Send us a message"),
                    "intro": {"waitlist": "Leave your details and we'll let you know as soon as we launch.",
                              "bookings": "Tell us when suits you and we'll confirm a time."}.get(goal, "Tell us a little about what you need and we'll get back to you."),
                    "details": [d for d in [
                        {"icon": "phone", "label": "Phone", "value": phone, "href": f"tel:{_tel(phone)}"} if phone and ({"click_to_call", "whatsapp"} & set(feats) or goal == "calls") else None,
                        {"icon": "chat", "label": "WhatsApp", "value": "Message us", "href": f"https://wa.me/{re.sub(r'\D', '', phone)}"} if phone and "whatsapp" in feats else None,
                        {"icon": "pin", "label": "Area", "value": area, "href": None} if area else None,
                        {"icon": "clock", "label": "Hours", "value": f.get("kb_hours"), "href": None} if f.get("kb_hours") else None] if d],
                    "submit": {"waitlist": "Join the waitlist", "bookings": "Send booking request"}.get(goal, "Send message")},
    }
    city = f" in {re.split(r'\s+(and|&|,)\s+', area)[0]}" if area else ""
    meta = {
        "index": {"title": _trim(f"{name} — {_cap(btype)}{city}", 60), "description": _trim(f"{name}: {', '.join(services[:3]) or btype}{city}. {action_line}", 155)},
        "services": {"title": _trim(f"Services | {name}", 60), "description": _trim(f"{', '.join(services) or 'What we offer'}{city}.", 155)},
        "about": {"title": _trim(f"About | {name}", 60), "description": _trim(" ".join(about), 155)},
        "faq": {"title": _trim(f"FAQ | {name}", 60), "description": _trim(f"Answers to common questions about {name}.", 155)},
        "contact": {"title": _trim(f"Contact | {name}", 60), "description": _trim(f"Get in touch with {name}{city}.", 155)},
        "privacy": {"title": _trim(f"Privacy notice | {name}", 60), "description": "How we handle the details you send us."},
        "thanks": {"title": _trim(f"Thank you | {name}", 60), "description": "Your message has been received."},
    }
    flags = check_claims([headline, sub, cta_heading, cta_text, *about, *[b["text"] for b in blurbs]], owner_text(f))
    risks = [Risk(id=f"claim-{i}", severity="high", title=f"Unsupported claim: {x['match']}", detail=x["text"],
                  mitigation="Removed or confirm it in your answers.", blocking=True) for i, x in enumerate(flags)]
    knowledge = [
        K("decision", "copy.headline", headline, "Best-scoring of the headline options (local search intent, clarity, "
          "length, urgency, no unsupported claims).", ["strategy.urgency", "service_area", "services"]),
        K("insight", "copy.headline_candidates", cands[:5], "All options the copywriter considered, with scores.", []),
        K("decision", "copy.voice_applied", strat["voice"], "Applied the strategist's voice to every section.", ["strategy.voice"]),
        K("insight", "copy.faq_count", len(faqs), "FAQs written only where your answers give a real answer.",
          ["kb_quote", "kb_hours", "kb_emergency", "service_area"]),
    ]
    return AgentResult(agent="copywriter", status="blocked" if risks else "ok",
                       summary=f"Headline “{headline}”; {len(blurbs)} service descriptions, {len(faqs)} FAQs.",
                       artifacts={"content": content, "meta": meta, "knowledge": knowledge}, risks=risks, evidence=notes,
                       requires_human_approval=True, risk_tier="medium")


def _process(pb: dict, f: Facts) -> list[tuple[str, str]]:
    steps = list(pb.get("process") or [])
    q = f.get("kb_quote")
    if steps and q == "free_quote" and len(steps) > 1:
        steps[1] = ("Free quote", "We tell you the price before any work starts.")
    if steps and f.get("kb_first_meeting") == "free":
        steps[0] = (steps[0][0] + " (free)", steps[0][1])
    return steps


def _tel(phone: str | None) -> str:
    if not phone:
        return ""
    return ("+" if phone.strip().startswith("+") else "") + re.sub(r"\D", "", phone)


def _trim(s: str, n: int) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


# ====================================================================== 4. Art director

THEME_BASE: dict[str, dict] = {
    "classic": dict(name="Classic & trustworthy", font_heading="source-serif-4", font_body="inter", heading_weight=600, radius=6,
                    button="rounded", pattern="grid", shadow="soft", tracking=-0.01, bg="#fbfaf7", mood="editorial"),
    "bold_local": dict(name="Bold & local", font_heading="outfit", font_body="inter", heading_weight=800, radius=14, button="pill",
                       pattern="dots", shadow="lifted", tracking=-0.025, bg="#ffffff", mood="punchy"),
    "fresh": dict(name="Fresh & clean", font_heading="plus-jakarta-sans", font_body="plus-jakarta-sans", heading_weight=800, radius=18,
                  button="pill", pattern="waves", shadow="soft", tracking=-0.02, bg=None, mood="airy"),
    "warm": dict(name="Warm & welcoming", font_heading="fraunces", font_body="nunito", heading_weight=600, radius=22, button="pill",
                 pattern="waves", shadow="soft", tracking=-0.01, bg="#fff8f1", mood="cosy"),
    "elegant": dict(name="Elegant", font_heading="dm-serif-display", font_body="manrope", heading_weight=400, radius=2, button="square",
                    pattern="rings", shadow="none", tracking=0, bg="#faf8f5", mood="refined"),
    "tech": dict(name="Modern product", font_heading="space-grotesk", font_body="inter", heading_weight=700, radius=12, button="rounded",
                 pattern="grid", shadow="lifted", tracking=-0.03, bg="#f7f8fb", mood="sharp"),
    "studio": dict(name="Minimal studio", font_heading="manrope", font_body="manrope", heading_weight=800, radius=0, button="square",
                   pattern="none", shadow="none", tracking=-0.035, bg="#ffffff", mood="minimal"),
    "friendly": dict(name="Friendly", font_heading="nunito", font_body="nunito", heading_weight=800, radius=20, button="pill",
                     pattern="dots", shadow="soft", tracking=-0.01, bg=None, mood="playful"),
}
STYLE_COMPAT = {"formal": ["classic", "elegant"], "modern": ["tech", "bold_local", "fresh", "studio"],
                "warm": ["warm", "friendly", "fresh"]}


def _rgb(h): return [int(h[i:i + 2], 16) for i in (1, 3, 5)]
def _hex(c): return "#" + "".join(f"{max(0, min(255, round(x))):02x}" for x in c)
def mix(a: str, b: str, t: float) -> str:
    return _hex([x * (1 - t) + y * t for x, y in zip(_rgb(a), _rgb(b))])


def build_theme(theme_id: str, palette: str, style: str | None) -> dict:
    from ..services.colors import parse_value
    base = dict(THEME_BASE[theme_id])
    primary, accent = parse_value(palette) or parse_value("navy")
    bg = base.pop("bg") or mix(primary, "#ffffff", 0.965)
    base.pop("mood")
    text = mix("#14171c", primary, 0.12)
    while contrast("#ffffff", primary) < 4.6:
        primary = mix(primary, "#000000", 0.08)
    dark = primary
    while contrast("#ffffff", dark) < 10:
        dark = mix(dark, "#000000", 0.12)
    muted = mix(text, bg, 0.32)
    while contrast(muted, bg) < 4.6:
        muted = mix(muted, text, 0.2)
    surface = mix(primary, bg, 0.93)
    on_accent = "#111111" if contrast("#111111", accent) >= contrast("#ffffff", accent) else "#ffffff"
    return {"id": theme_id, **base, "primary": primary, "accent": accent, "bg": bg, "surface": surface, "text": text,
            "muted": muted, "on_primary": "#ffffff", "on_accent": on_accent, "dark": dark, "scale": 1.0,
            "density": "airy" if theme_id in {"elegant", "studio", "warm"} else "normal"}


def run_art_director(f: Facts, board: dict, pages: list[dict]) -> AgentResult:
    strat = board["strategy"]
    pb = pick_playbook(strat.get("business_type"), f.get("business_description") or "", f.get("site_category"))
    style = f.get("style") or "modern"
    compat = STYLE_COMPAT.get(style, THEME_BASE.keys())
    suggested = pb.get("theme", "classic")
    theme_id = suggested if suggested in compat else list(compat)[0]
    if strat["urgency"] == "high" and "bold_local" in compat:
        theme_id = "bold_local"
    picked = f.get("template") if f.get("template") in THEME_BASE else None
    if picked:  # the owner's explicit choice wins over the director's preference
        theme_id = picked
    theme = build_theme(theme_id, f.get("palette") or "navy", style)
    n_services = len(f.get("services") or [])

    def variant_for(sec: str, page: str) -> tuple[str, dict, str]:
        if sec == "hero":
            v = {"classic": "editorial", "bold_local": "banner", "fresh": "split", "warm": "split", "elegant": "centered",
                 "tech": "centered", "studio": "editorial", "friendly": "split"}[theme_id]
            return v, {"tone": "dark" if v in {"banner", "centered"} and theme_id in {"bold_local", "tech"} else "light"}, \
                f"{THEME_BASE[theme_id]['name']} direction → {v} hero"
        if sec == "services":
            if theme_id == "tech" and n_services >= 4:
                return "bento", {}, "4+ items fit a bento grid in the product style"
            if n_services <= 2 or n_services > 8:
                return "list", {}, f"{n_services} services read better as a list"
            if theme_id in {"studio", "elegant"} and page == "services":
                return "alternating", {}, "editorial alternating rows suit the refined style"
            cols = 2 if n_services in {2, 4} else 3
            return "cards", {"columns": cols}, f"{n_services} services → {cols}-column cards so no row is left with a lone card"
        if sec == "process":
            return ("timeline" if theme_id in {"classic", "elegant", "studio"} else "steps"), {}, "numbered steps make the next action obvious"
        if sec == "why":
            return ("split" if theme_id in {"classic", "elegant", "warm"} else "grid"), {}, ""
        if sec == "faq":
            n = len(_faqs(f, strat))
            return ("two_col" if n >= 6 and page == "faq" else "accordion"), {}, ""
        if sec == "cta":
            return ("band" if strat["urgency"] != "low" else "card"), {"tone": "dark" if theme_id in {"bold_local", "tech"} else "primary"}, ""
        if sec == "trust_bar":
            return ("strip" if theme_id in {"bold_local", "tech"} else "chips"), {}, ""
        if sec == "area":
            return ("map" if f.get("service_area") else "simple"), {}, ""
        if sec == "about":
            return ("split" if page == "about" else "simple"), {}, ""
        if sec == "contact":
            return "split", {}, ""
        if sec == "page_head":
            return ("tinted" if theme_id in {"fresh", "friendly", "warm"} else "simple"), {}, ""
        return SECTION_VARIANTS[sec][0], {}, ""

    layout = {}
    choices = []
    for p in pages:
        for s in p["sections"]:
            v, props, why = variant_for(s, p["slug"])
            layout[f"{p['slug']}:{s}"] = {"variant": v, "props": props, "why": why}
            if why:
                choices.append(f"{p['slug']}/{s}: {why}")
    knowledge = [
        K("decision", "art.theme", {"id": theme_id, "name": THEME_BASE[theme_id]["name"], "fonts": [theme["font_heading"], theme["font_body"]]},
          (f"You picked “{THEME_BASE[theme_id]['name']}” — your choice wins; layouts were tuned to it."
           if picked else
           f"You chose a {style} style; the {pb['label']} playbook suggests '{suggested}'; urgency is {strat['urgency']} → "
           f"'{THEME_BASE[theme_id]['name']}' with {theme['font_heading']} headings."),
          ["template", "style", "strategy.urgency", "strategy.personality"]),
        K("decision", "art.palette", {k: theme[k] for k in ("primary", "accent", "bg", "dark")},
          "Your colour choice, adjusted until text contrast passes WCAG AA (4.5:1) everywhere.", ["palette"]),
        K("decision", "art.layouts", choices, "Layout per section, chosen from the content each one actually has.",
          ["ia.home_sections", "services"]),
    ]
    return AgentResult(agent="art_director", summary=f"{THEME_BASE[theme_id]['name']} theme, {theme['font_heading']} + {theme['font_body']}.",
                       artifacts={"theme": theme, "layout": layout, "knowledge": knowledge})


# ====================================================================== assembly

def assemble_spec(f: Facts, slug: str, pages: list[dict], copy: dict, art: dict, cta_label: str) -> dict:
    content, meta = copy["content"], copy["meta"]
    nav_labels = {"index": "Home", "services": "Product" if f.get("site_category") == "startup" else "Services",
                  "about": "About", "faq": "FAQ", "contact": "Contact"}
    phone = f.get("phone")
    feats = f.get("features") or []
    out_pages = []
    for p in pages:
        secs = []
        for i, s in enumerate(p["sections"]):
            lay = art["layout"].get(f"{p['slug']}:{s}", {"variant": SECTION_VARIANTS[s][0], "props": {}, "why": ""})
            c = dict(content.get(s, {}))
            if s == "page_head":
                c = {"title": {"services": "What we do", "about": nav_labels["about"] + " us", "faq": "Common questions",
                               "contact": "Get in touch", "privacy": "Privacy notice"}.get(p["slug"], p["slug"].title()),
                     "sub": {"services": content["services"]["intro"], "contact": content["contact"]["intro"],
                             "faq": "Can't find your answer? Just ask.", "about": "", "privacy": ""}.get(p["slug"], "")}
            if s == "prose":
                c = {"paragraphs": [], "kind": "privacy"}
            if s == "thanks":
                c = {"heading": "Thank you — we've got your message", "text": f"{f.get('business_name') or 'We'} will get back to you soon."}
            if s == "faq" and p["slug"] == "index":
                c = {**c, "items": c["items"][:4]}
            secs.append({"id": f"{p['slug']}-{s}-{i}", "type": s, "variant": lay["variant"], "props": dict(lay["props"]),
                         "content": c, "why": lay.get("why", "")})
        out_pages.append({"slug": p["slug"], "nav_label": nav_labels.get(p["slug"]), "meta": meta.get(p["slug"], meta["index"]),
                          "sections": secs})
    nav = [{"label": nav_labels[p["slug"]], "href": "index.html" if p["slug"] == "index" else f"{p['slug']}.html", "page": p["slug"]}
           for p in pages if p["slug"] in nav_labels]
    return {
        "theme": art["theme"],
        "site": {"name": f.get("business_name") or "Our business", "slug": slug, "language": f.get("language") or "en",
                 "phone": phone if phone and ({"click_to_call", "whatsapp"} & set(feats) or f.get("primary_goal") == "calls") else None,
                 "phone_tel": _tel(phone), "sticky_call": bool(phone) and ("click_to_call" in feats or f.get("primary_goal") == "calls"),
                 "area": f.get("service_area"), "hours": f.get("kb_hours"), "logo": f.get("logo"),
                 "cta": content["hero"]["primary_cta"] | {"label": cta_label}, "nav": nav, "has_form": "contact_form" in feats},
        "pages": out_pages,
    }
