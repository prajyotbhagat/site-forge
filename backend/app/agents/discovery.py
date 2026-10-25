"""Business Discovery agent: turns free text into draft facts with confidence and provenance."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..onboarding.schema import CATEGORIES, EMAIL_RE, PHONE_RE
from .contracts import AgentResult, ProposedChange, Risk
from .llm import LLMProvider, ProviderUnavailable

# (pattern, category, business-type label, regulated-domain or None)
_KEYWORDS: list[tuple[str, str, str, str | None]] = [
    (r"plumb", "local_service", "plumbing", None),
    (r"electric", "local_service", "electrical services", None),
    (r"clean(ing|ers?)\b", "local_service", "cleaning services", None),
    (r"carpent", "local_service", "carpentry", None),
    (r"paint(ing|ers?)\b", "local_service", "painting", None),
    (r"pest control", "local_service", "pest control", None),
    (r"salon|barber|beauty parlou?r", "local_service", "salon", None),
    (r"\b(spa)\b", "local_service", "spa", None),
    (r"garage|mechanic|car repair", "local_service", "vehicle repair", None),
    (r"landscap|garden(ing|er)", "local_service", "landscaping", None),
    (r"\bac\b|air[- ]condition", "local_service", "AC service", None),
    (r"tuition|tutor|coaching (centre|center|classes)", "local_service", "tutoring", None),
    (r"bakery|cafe|café|restaurant", "local_service", "food & hospitality", None),
    (r"\bgym\b|fitness|yoga", "local_service", "fitness", None),
    (r"tailor|boutique", "local_service", "tailoring", None),
    (r"account(ing|ant)|bookkeep|chartered|\bca firm|\btax\b|\bgst\b", "professional_service", "accounting and tax", "financial"),
    (r"\blaw(yer)?\b|legal|advocate|attorney", "professional_service", "legal services", "legal"),
    (r"clinic|dentist|dental|doctor|physio|therap|counsell", "professional_service", "healthcare", "health"),
    (r"financial advis|wealth|investment|insurance", "professional_service", "financial advice", "financial"),
    (r"architect|interior design", "professional_service", "architecture & interiors", None),
    (r"consult", "professional_service", "consulting", None),
    (r"marketing agency|design agency|\bagency\b", "professional_service", "agency", None),
    (r"start-?up|\bsaas\b|\bapp\b|platform|software|\bai tool|b2b|launch(ing)? (a|our) product", "startup", "startup", None),
    (r"portfolio|freelanc|photograph|illustrat|\bartist\b|musician|personal brand|\bwriter\b", "portfolio", "creative portfolio", None),
    (r"sell (things |products )?online|online (shop|store)|e-?commerce|\bshop\b|\bstore\b", "shop", "retail", None),
]

_GOALS = [
    (r"\bcalls?\b|ring us|phone", "calls"),
    (r"book(ing|ings)?\b|appointment|reservation", "bookings"),
    (r"waitlist|wait-list|sign[- ]?ups?|early access|beta users|demo requests?", "waitlist"),
    (r"enquir|inquir|\bleads?\b|quotes?\b|consultation", "enquiries"),
    (r"showcase|show (off )?my work|portfolio", "portfolio"),
    (r"credib|look professional|online presence|trust", "credibility"),
]

_SERVICE_PRESETS = {
    "plumbing": ["Leak & pipe repairs", "Bathroom & kitchen plumbing", "Water heater installation", "Drain cleaning"],
    "electrical services": ["Wiring & rewiring", "Fault finding & repairs", "Lighting installation", "Inverter & appliance fitting"],
    "accounting and tax": ["Income tax filing", "GST registration & returns", "Bookkeeping", "Business advisory"],
    "legal services": ["Consultations", "Documentation & drafting", "Representation"],
    "cleaning services": ["Home deep cleaning", "Office cleaning", "Move-in / move-out cleaning"],
    "salon": ["Haircuts & styling", "Colouring", "Skin & beauty treatments"],
    "tutoring": ["One-to-one tutoring", "Group classes", "Exam preparation"],
}

_STOP_PLACES = {"I", "We", "My", "Our", "The", "A", "An", "India", "English", "Hindi"}
_PLACE_RE = re.compile(r"\b(?:in|at|near|around|based in|serving|serve|cover|covering|across)\s+((?:[A-Z][a-zA-Z]+)(?:[\s-](?:[A-Z][a-zA-Z]+)){0,2})")
_NAME_RE = re.compile(r"(?:called|named|name is)\s+[\"“']?([A-Z0-9][\w&'-]*(?:\s+(?!I\b|We\b|My\b|Our\b)(?:[A-Z0-9&][\w&'-]*|of|&)){0,5})[\"”']?")
_QUOTED_RE = re.compile(r"[\"“]([^\"”]{2,60})[\"”]")
_PHONE_FIND = re.compile(r"(?<![\w])(\+?\d[\d \-()]{7,17}\d)(?![\w])")
_EMAIL_FIND = re.compile(r"[^@\s,;:<>()]{1,64}@[^@\s,;:<>()]{1,255}\.[A-Za-z]{2,}")


class _LLMExtraction(BaseModel):
    site_category: str | None = None
    business_type: str | None = None
    business_name: str | None = None
    service_area: str | None = None
    primary_goal: str | None = None
    services: list[str] | None = None
    audience: str | None = None


@dataclass
class Extraction:
    changes: list[ProposedChange] = field(default_factory=list)
    business_type: str | None = None
    regulated: str | None = None
    evidence: list[str] = field(default_factory=list)


def _rules_extract(text: str) -> Extraction:
    ex = Extraction()
    low = text.lower()

    for pat, cat, label, reg in _KEYWORDS:
        m = re.search(pat, low)
        if m:
            ex.business_type = label
            ex.regulated = reg
            ex.changes.append(ProposedChange(field_key="site_category", value=cat, confidence=0.85,
                                             reason=f"You mentioned “{m.group(0)}”."))
            ex.evidence.append(f"category keyword: {m.group(0)}")
            preset = _SERVICE_PRESETS.get(label)
            if preset:
                ex.changes.append(ProposedChange(field_key="services", value=preset, confidence=0.5,
                                                 reason=f"Typical services for {label} — please edit to match what you actually offer."))
            break

    for pat, goal in _GOALS:
        m = re.search(pat, low)
        if m:
            ex.changes.append(ProposedChange(field_key="primary_goal", value=goal, confidence=0.85,
                                             reason=f"You said “{m.group(0)}”."))
            break

    m = _PLACE_RE.search(text)
    if m and m.group(1).split()[0] not in _STOP_PLACES:
        place = m.group(1).strip()
        nearby = re.search(re.escape(place) + r"\s+(and|&)\s+(nearby|surrounding)\s+(towns|areas|villages)", text, re.I)
        if nearby:
            place = f"{place} and nearby {nearby.group(3).lower()}"
        ex.changes.append(ProposedChange(field_key="service_area", value=place, confidence=0.85,
                                         reason=f"You mentioned “{m.group(0)}”."))
    if re.search(r"\bremote(ly)?\b|online consult|across india|anywhere", low):
        ex.changes.append(ProposedChange(field_key="remote_service", value="yes", confidence=0.85,
                                         reason="You mentioned working remotely."))

    m = _NAME_RE.search(text) or _QUOTED_RE.search(text)
    if m:
        ex.changes.append(ProposedChange(field_key="business_name", value=m.group(1).strip(" .,"), confidence=0.9,
                                         reason="You told me the name."))

    for pm in _PHONE_FIND.finditer(text):
        cand = pm.group(1).strip()
        if PHONE_RE.match(cand) and sum(c.isdigit() for c in cand) >= 10:
            ex.changes.append(ProposedChange(field_key="phone", value=cand, source="extracted", confidence=0.95,
                                             reason="Found a phone number in your message."))
            break
    em = _EMAIL_FIND.search(text)
    if em and EMAIL_RE.match(em.group(0)):
        ex.changes.append(ProposedChange(field_key="contact_email", value=em.group(0).lower(), source="extracted",
                                         confidence=0.9, reason="Found an email address in your message."))
    return ex


def run(text: str, known: dict[str, Any], provider: LLMProvider) -> AgentResult:
    """Interpret a free-text answer. Proposes only fields that are not already known."""
    ex = _rules_extract(text)
    provider_used = "rules"
    try:
        llm = provider.complete_json(
            "Extract facts about this small business for website planning. Use null when not stated. "
            f"site_category must be one of {list(CATEGORIES)}; primary_goal one of "
            "[calls, enquiries, bookings, waitlist, credibility, portfolio].",
            text, _LLMExtraction)
        provider_used = provider.name
        rule_keys = {c.field_key for c in ex.changes if c.confidence >= 0.8}
        for key in ("site_category", "business_name", "service_area", "primary_goal", "services", "audience"):
            val = getattr(llm, key)
            if val and key not in rule_keys:
                if key == "site_category" and val not in CATEGORIES:
                    continue
                ex.changes.append(ProposedChange(field_key=key, value=val, confidence=0.7,
                                                 reason="Inferred from your description."))
        ex.business_type = ex.business_type or llm.business_type
    except ProviderUnavailable:
        pass

    changes = [c for c in ex.changes if c.field_key not in known]
    risks = []
    if ex.regulated:
        risks.append(Risk(id="regulated-category", severity="medium",
                          title=f"Regulated field ({ex.regulated})",
                          detail="Claims about qualifications, outcomes or fees in this field may be regulated.",
                          mitigation="Only owner-verified credentials are published; you review all copy before launch."))
    cat = next((c.value for c in changes if c.field_key == "site_category"), known.get("site_category"))
    if cat == "shop":
        risks.append(Risk(id="ecommerce-out-of-scope", severity="info", title="Online shop is a later release",
                          detail="Payments, carts and inventory aren't in the first release.",
                          mitigation="We can build a product showcase with enquiry form now."))
    return AgentResult(
        agent="business_discovery",
        summary=f"Found {len(changes)} new detail(s)" + (f"; looks like {ex.business_type}." if ex.business_type else "."),
        proposed_changes=changes,
        evidence=ex.evidence + [f"provider: {provider_used}"],
        confidence=max([c.confidence for c in changes], default=0.0),
        risks=risks,
        artifacts={"business_type": ex.business_type, "regulated": ex.regulated},
    )
