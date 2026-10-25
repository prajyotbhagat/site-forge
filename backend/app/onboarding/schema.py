"""Onboarding requirement schema (roadmap sections 3 and 6).

Every question maps to exactly one field. A field is asked only when its condition holds and the value
is not already known with enough confidence. Each field carries a plain-language "why" and the
technical translation that is only shown in Advanced settings.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

Answers = dict[str, Any]

STEPS = [
    {"id": "welcome", "label": "Welcome"},
    {"id": "business", "label": "Your business"},
    {"id": "goal", "label": "Website goal"},
    {"id": "brand", "label": "Brand & content"},
    {"id": "frontend", "label": "Look & pages"},
    {"id": "backend", "label": "Enquiries & features"},
    {"id": "platform", "label": "Hosting & domain"},
    {"id": "trust", "label": "Trust & privacy"},
    {"id": "seo", "label": "Search & success"},
    {"id": "review", "label": "Review & approve"},
    {"id": "build", "label": "Build & preview"},
    {"id": "launch", "label": "Launch"},
]

CATEGORIES = {
    "local_service": "Local service business",
    "professional_service": "Professional or consulting practice",
    "startup": "Startup or product launch",
    "portfolio": "Portfolio or personal brand",
    "shop": "Online shop",
}

SUPPORTED_CATEGORIES = {"local_service", "professional_service", "startup", "portfolio"}

GOALS = {
    "calls": ("Phone calls", "Big call buttons, especially on phones."),
    "enquiries": ("Enquiries / quote requests", "A short enquiry form on every page."),
    "bookings": ("Bookings", "Online booking arrives in a later release; we'll start with booking requests by form."),
    "waitlist": ("Waitlist or demo sign-ups", "A focused sign-up form above the fold."),
    "credibility": ("Look credible", "Clear story, services and proof you can verify."),
    "portfolio": ("Show my work", "A gallery-style layout with an enquiry form."),
}

STYLES = {
    "formal": ("Formal & traditional", "Serif headings, restrained colour, structured layout."),
    "modern": ("Clean & modern", "Bold sans-serif type, generous whitespace, crisp edges."),
    "warm": ("Warm & approachable", "Rounded shapes, friendly type, softer colours."),
}

PALETTES = {
    "navy": ("Navy & brass", {"primary": "#1f3a5f", "accent": "#b08d57"}),
    "forest": ("Forest & sand", {"primary": "#2f5d50", "accent": "#c8a96a"}),
    "terracotta": ("Terracotta & cream", {"primary": "#a4492f", "accent": "#e3b35b"}),
    "indigo": ("Indigo & mint", {"primary": "#3b3f9a", "accent": "#2bb39a"}),
    "charcoal": ("Charcoal & signal orange", {"primary": "#26292e", "accent": "#e8642c"}),
}

PAGES = {
    "home": "Home",
    "services": "Services",
    "about": "About",
    "contact": "Contact",
    "faq": "FAQ",
}

FEATURES = {
    "contact_form": ("Enquiry form", "mvp"),
    "click_to_call": ("Click-to-call button", "mvp"),
    "whatsapp": ("WhatsApp chat link", "mvp"),
    "booking": ("Online booking calendar", "later"),
    "newsletter": ("Newsletter sign-up", "later"),
    "accounts": ("Customer logins", "later"),
    "payments": ("Online payments", "later"),
}

FORM_FIELDS = {
    "name": "Name",
    "email": "Email",
    "phone": "Phone",
    "service": "Service needed",
    "message": "Message",
    "company": "Company",
}

SENSITIVE_HINTS = re.compile(
    r"(?i)\b(aadhaar|pan card|passport|ssn|social security|medical|diagnos|health record|bank account|card number|religion|caste|biometric)"
)


@dataclass
class Option:
    value: str
    label: str
    description: str = ""
    available: bool = True


@dataclass
class Field:
    key: str
    step: str
    prompt: str
    kind: str  # text | longtext | choice | multi | email | phone | upload | list
    why: str
    technical: str
    required: bool = True
    allow_skip: bool = False
    options: Callable[[Answers], list[Option]] | None = None
    condition: Callable[[Answers], bool] = lambda a: True
    recommend: Callable[[Answers], tuple[Any, str] | None] | None = None
    placeholder: str = ""
    impact: str = "quality"  # scope | security | quality | cost

    def is_relevant(self, answers: Answers) -> bool:
        try:
            return bool(self.condition(answers))
        except Exception:
            return False

    def option_list(self, answers: Answers) -> list[Option]:
        return self.options(answers) if self.options else []


def _cat(a: Answers) -> str | None:
    return a.get("site_category")


def _label_for_offer(a: Answers) -> str:
    return {"startup": "product", "portfolio": "work"}.get(_cat(a) or "", "services")


def _rec_goal(a: Answers):
    rec = {"local_service": "calls", "professional_service": "enquiries", "startup": "waitlist",
           "portfolio": "portfolio"}.get(_cat(a) or "", "enquiries")
    return rec, f"Most {CATEGORIES.get(_cat(a) or '', 'small business').lower()} websites win customers through {GOALS[rec][0].lower()}."


def _rec_style(a: Answers):
    rec = {"professional_service": "formal", "local_service": "warm", "startup": "modern",
           "portfolio": "modern"}.get(_cat(a) or "", "modern")
    return rec, f"{STYLES[rec][0]} tends to build trust quickly for this kind of business."


def _rec_palette(a: Answers):
    rec = {"formal": "navy", "warm": "terracotta", "modern": "indigo"}.get(a.get("style") or "", "navy")
    return rec, f"{PALETTES[rec][0]} pairs well with the {STYLES.get(a.get('style') or 'modern', STYLES['modern'])[0].lower()} style and keeps text readable."


def _rec_pages(a: Answers):
    pages = ["home", "services", "about", "contact"]
    if _cat(a) in {"professional_service", "local_service", "startup"}:
        pages.append("faq")
    return pages, "A small, focused set of pages that covers what visitors look for before they get in touch."


def _rec_features(a: Answers):
    feats = ["contact_form"]
    if a.get("primary_goal") == "calls" or _cat(a) == "local_service":
        feats.append("click_to_call")
    return feats, "An enquiry form works for everyone; a call button helps when customers prefer to phone."


def _rec_form_fields(a: Answers):
    fields = ["name", "email", "phone"]
    if _cat(a) == "startup":
        fields.append("company")
    elif a.get("services"):
        fields.append("service")
    fields.append("message")
    return fields, "Just enough to reply properly. Collecting less means less to protect."


def _rec_retention(a: Answers):
    return "180", "Six months covers follow-ups for most enquiries; older ones are deleted automatically."


def _rec_language(a: Answers):
    return "en", "Start in one language; more languages are planned for a later release."


def _rec_search(a: Answers):
    services = a.get("services") or []
    area = a.get("service_area") or ""
    phrases = []
    for s in services[:3]:
        phrases.append(f"{s} {area}".strip().lower() if area else str(s).lower())
    if not phrases:
        return None
    return phrases, "Based on your services and service area."


def _template_options(_a: Answers) -> list[Option]:
    from ..agents.team import THEME_BASE
    return [Option(k, v["name"], f"{v['font_heading'].replace('-', ' ').title()} + {v['font_body'].replace('-', ' ').title()}")
            for k, v in THEME_BASE.items()]


def _rec_template(a: Answers):
    from ..agents.team import THEME_BASE, STYLE_COMPAT
    from ..agents.playbooks import pick_playbook
    pb = pick_playbook(None, a.get("business_description") or "", a.get("site_category"))
    compat = STYLE_COMPAT.get(a.get("style") or "modern", list(THEME_BASE))
    t = pb.get("theme") if pb.get("theme") in compat else compat[0]
    return t, f"“{THEME_BASE[t]['name']}” suits a {pb['label']} business with a {a.get('style') or 'modern'} style."


def _choices(d: dict, available: Callable[[str], bool] | None = None) -> Callable[[Answers], list[Option]]:
    def build(_a: Answers) -> list[Option]:
        out = []
        for k, v in d.items():
            label, desc = (v[0], v[1]) if isinstance(v, tuple) else (v, "")
            out.append(Option(k, label, desc, available(k) if available else True))
        return out
    return build


def _feature_options(_a: Answers) -> list[Option]:
    return [Option(k, label, "Available now" if tier == "mvp" else "Planned for a later release — we'll note it in your plan",
                   tier == "mvp") for k, (label, tier) in FEATURES.items()]


def _palette_options(_a: Answers) -> list[Option]:
    return [Option(k, label, f"{v['primary']} / {v['accent']}") for k, (label, v) in PALETTES.items()]


FIELDS: list[Field] = [
    Field("business_description", "welcome",
          "What are you building? Tell me about your business in a sentence or two.",
          "longtext",
          "A short description lets me fill in a lot of the setup for you, so you answer fewer questions.",
          "Free text → Business Discovery agent extracts category, name, location, goal and services.",
          placeholder="e.g. I run a two-person plumbing business in Dehradun and want more calls.",
          impact="scope"),
    Field("site_category", "welcome", "Which of these best describes you?", "choice",
          "This decides which template and features I recommend.",
          "Template family + feature presets.",
          options=_choices({k: (v, "" if k in SUPPORTED_CATEGORIES else "Full online shops come in a later release")
                            for k, v in CATEGORIES.items()}),
          impact="scope"),
    Field("business_name", "business", "What's the name of your business?", "text",
          "It appears in your site header, page titles and search results.",
          "Site title, <title> suffix, Organization/LocalBusiness name."),
    Field("services", "business",
          "What do you offer? List your main services or products, separated by commas.",
          "list", "Each one gets a clear section so visitors can find what they need.",
          "Services collection → service cards, service select in the form, search phrases.",
          placeholder="e.g. Leak repairs, Bathroom fitting, Water heater installation",
          options=None),
    Field("audience", "business", "Who are your typical customers?", "text",
          "Helps me write copy that speaks to them.",
          "Copy tone and hero messaging.", required=False, allow_skip=True,
          placeholder="e.g. Homeowners and small offices"),
    Field("service_area", "business", "Where do you serve customers — a city, a region, or remotely?", "text",
          "Local customers search with place names, so this improves local search visibility.",
          "Local SEO: areaServed, service-area copy, title tags.",
          condition=lambda a: _cat(a) in {"local_service", "professional_service"},
          placeholder="e.g. Roorkee and nearby towns"),
    Field("remote_service", "business", "Do you also work with customers remotely?", "choice",
          "If yes, I'll mention remote consultations so people outside your area still get in touch.",
          "Copy variant + areaServed includes 'Remote'.",
          options=_choices({"yes": ("Yes", ""), "no": ("No", "")}),
          condition=lambda a: _cat(a) == "professional_service", required=False, allow_skip=True),
    Field("differentiators", "business", "What makes you different from others nearby?", "longtext",
          "Specific reasons to choose you convert better than generic claims.",
          "Hero sub-heading and 'Why choose us' section.", required=False, allow_skip=True,
          placeholder="e.g. Same-day visits, 15 years of experience, upfront pricing"),
    Field("primary_goal", "goal", "What should a visitor do first when they land on your site?", "choice",
          "Everything on the page is arranged around this one action.",
          "Primary CTA, CTA placement, conversion event.",
          options=_choices(GOALS), recommend=_rec_goal, impact="scope"),
    Field("proof_points", "brand",
          "Do you have real reviews, certifications or awards we can mention? I'll only use what you give me.",
          "longtext",
          "Verified proof builds trust. I never invent testimonials, ratings or credentials.",
          "Content & SEO agent: proof section only rendered from owner-supplied facts.",
          required=False, allow_skip=True,
          placeholder="e.g. Licensed by Uttarakhand Jal Sansthan; 'Fixed our leak in an hour' — R. Sharma"),
    Field("logo", "brand", "Do you have a logo? Upload a PNG, JPG or WebP (max 2 MB), or skip and I'll use your name as a text logo.",
          "upload", "Your logo appears in the header and when people share your site.",
          "Asset upload → magic-byte validation → copied into the build.", required=False, allow_skip=True),
    Field("reference_site", "brand", "Is there a website whose look you like? Paste its address and I'll borrow its colours and style — never its content.",
          "url", "A quick way to show me your taste. I only read its colours and fonts.",
          "Brand scout: SSRF-safe fetch → CSS colours, theme-color, font families → palette + template suggestion.",
          required=False, allow_skip=True, placeholder="e.g. www.a-site-you-like.com"),
    Field("style", "frontend", "Which style feels right?", "choice",
          "Sets fonts, spacing and overall feel. You can change it later.",
          "Template family, typography, radius and spacing tokens.",
          options=_choices(STYLES), recommend=_rec_style),
    Field("palette", "frontend", "Pick your colours — from your logo, your industry, colour-theory suggestions, or your exact brand colours.", "palette",
          "Every option is checked so text stays easy to read.",
          "Design tokens: --primary, --accent. Sources: logo extraction, reference site, industry palettes, harmonies, The Color API, Colormind.",
          options=_palette_options, recommend=_rec_palette),
    Field("template", "frontend", "Pick a starting design. Each one changes fonts, layout and feel — you can switch later.", "template",
          "This is the overall look. Colours and content stay yours.",
          "Art-direction theme: typography pairing, hero/section layouts, radius, patterns.",
          options=lambda a: _template_options(a), recommend=lambda a: _rec_template(a)),
    Field("pages", "frontend", "Which pages do you need? I've picked a starter set.", "multi",
          "Fewer, clearer pages are easier to keep up to date.",
          "Sitemap, navigation labels, route definitions.",
          options=_choices(PAGES), recommend=_rec_pages),
    Field("language", "frontend", "Which language should the site be in?", "choice",
          "More languages are planned for a later release.",
          "html lang, localization strategy.",
          options=_choices({"en": ("English", ""), "hi": ("Hindi (later release)", "")},
                           available=lambda k: k == "en"),
          recommend=_rec_language),
    Field("features", "backend", "How should customers reach you? Pick everything that applies.", "multi",
          "I'll only build what you choose. Features marked 'later' go into your plan for a future release.",
          "Contact form API, tel: links, deferred integrations.",
          options=_feature_options, recommend=_rec_features, impact="scope"),
    Field("phone", "backend", "What phone number should the call button use?", "phone",
          "Shown as a tap-to-call button on phones.",
          "tel: link, LocalBusiness.telephone.",
          condition=lambda a: "click_to_call" in (a.get("features") or []) or "whatsapp" in (a.get("features") or [])
          or a.get("primary_goal") == "calls",
          placeholder="+91 98xxxxxx10"),
    Field("contact_email", "backend", "Which email address should new enquiries be sent to?", "email",
          "You'll get an email each time someone fills in the form. It is not shown on the website.",
          "Notification routing (email adapter); never rendered in page HTML.",
          condition=lambda a: "contact_form" in (a.get("features") or []), impact="security"),
    Field("form_fields", "trust", "What should the enquiry form ask for?", "multi",
          "Collecting only what you need keeps your visitors' data safer.",
          "Lead entity schema, server-side validation, data classification.",
          options=_choices(FORM_FIELDS), recommend=_rec_form_fields,
          condition=lambda a: "contact_form" in (a.get("features") or []), impact="security"),
    Field("lead_retention_days", "trust", "How long should we keep enquiries before deleting them?", "choice",
          "Keeping data only as long as you need it is good privacy practice.",
          "Retention policy: leads.delete_after; purge job.",
          options=_choices({"90": ("3 months", ""), "180": ("6 months", ""), "365": ("1 year", "")}),
          recommend=_rec_retention,
          condition=lambda a: "contact_form" in (a.get("features") or []), impact="security"),
    Field("lead_access", "trust", "Who on your team should be able to see enquiries?", "choice",
          "Enquiries contain people's contact details, so access is limited by default.",
          "RBAC: leads.read permission.",
          options=_choices({"owners": ("Only owners and admins", "Recommended"),
                            "team": ("Everyone on the project (later release)", "")},
                           available=lambda k: k == "owners"),
          recommend=lambda a: ("owners", "Least access by default; you can add admins anytime."),
          condition=lambda a: "contact_form" in (a.get("features") or []), impact="security"),
    Field("hosting", "platform", "Where should the website be hosted?", "choice",
          "Managed hosting means we handle security updates, backups and rollbacks for you.",
          "Hosting adapter selection.",
          options=_choices({"managed": ("Managed by us (recommended)", "Simplest; includes previews and one-click rollback"),
                            "own_account": ("My own hosting account (later release)", "")},
                           available=lambda k: k == "managed"),
          recommend=lambda a: ("managed", "Easiest to run, with built-in previews, backups and rollback."),
          impact="cost"),
    Field("domain_status", "platform", "Do you already have a web address (domain)?", "choice",
          "We never buy domains or change DNS without your explicit go-ahead.",
          "Custom domain mapping (manual, high-risk change).",
          options=_choices({"have": ("Yes, I have one", ""), "need": ("No, I need one", "We'll explain options; nothing is purchased automatically"),
                            "later": ("Decide later", "Your site gets a free address first")}),
          recommend=lambda a: ("later", "Launch on the free address first; connect a domain when you're ready."),
          impact="cost"),
    Field("domain_name", "platform", "What is your domain?", "text",
          "Recorded for the launch checklist. Connecting it is a separate step you approve.",
          "DNS records required (manual).",
          condition=lambda a: a.get("domain_status") == "have", placeholder="e.g. sharmaplumbing.in"),
    Field("search_phrases", "seo", "What might customers type into Google to find you? (comma-separated)", "list",
          "Used for page titles and descriptions. I won't stuff keywords or promise rankings.",
          "Title/meta plan, headings.", required=False, allow_skip=True, recommend=_rec_search),
    Field("monthly_lead_target", "seo", "Roughly how many enquiries a month would feel like success?", "text",
          "Gives us a baseline to measure against after launch.",
          "Analytics goal for form_submit / call_click events.", required=False, allow_skip=True,
          placeholder="e.g. 10"),
]

FIELD_MAP = {f.key: f for f in FIELDS}

SHORT_LABELS = {
    "business_description": "About your business", "site_category": "Type of business", "business_name": "Business name",
    "services": "Services", "audience": "Customers", "service_area": "Service area", "remote_service": "Remote work",
    "differentiators": "What makes you different", "primary_goal": "Main goal", "proof_points": "Proof & credentials",
    "logo": "Logo", "reference_site": "Site you like", "template": "Starting design", "style": "Style", "palette": "Colours", "pages": "Pages", "language": "Language",
    "features": "Contact options", "phone": "Phone", "contact_email": "Enquiries go to", "form_fields": "Form asks for",
    "lead_retention_days": "Keep enquiries for", "lead_access": "Who sees enquiries", "hosting": "Hosting",
    "domain_status": "Domain", "domain_name": "Domain name", "search_phrases": "Search phrases",
    "monthly_lead_target": "Monthly enquiry goal",
}

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[A-Za-z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()\-]{6,18}[0-9]$")


class ValidationError(ValueError):
    pass


def normalize(field: Field, value: Any, answers: Answers) -> Any:
    """Deterministic validation of a single answer. Raises ValidationError with a friendly message."""
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError("Please enter an answer, or choose Skip / Recommend for me.")
    if field.kind in {"text", "longtext"}:
        if not isinstance(value, str):
            raise ValidationError("Please enter text.")
        limit = 2000 if field.kind == "longtext" else 200
        v = value.strip()
        if len(v) > limit:
            raise ValidationError(f"Please keep this under {limit} characters.")
        return v
    if field.kind == "list":
        items = value if isinstance(value, list) else str(value).split(",")
        items = [str(i).strip()[:80] for i in items if str(i).strip()]
        if not items:
            raise ValidationError("Please list at least one item.")
        return items[:12]
    if field.kind == "email":
        v = str(value).strip()
        if not EMAIL_RE.match(v):
            raise ValidationError("That doesn't look like an email address.")
        return v.lower()
    if field.kind == "phone":
        v = str(value).strip()
        if not PHONE_RE.match(v) or sum(c.isdigit() for c in v) < 7:
            raise ValidationError("That doesn't look like a phone number. Include the country code if you can.")
        return v
    if field.kind == "choice":
        opts = {o.value: o for o in field.option_list(answers)}
        v = str(value)
        if v not in opts:
            raise ValidationError("Please pick one of the options.")
        if not opts[v].available:
            raise ValidationError(f"“{opts[v].label}” isn't available yet. Please pick another option.")
        return v
    if field.kind == "multi":
        opts = {o.value: o for o in field.option_list(answers)}
        vals = value if isinstance(value, list) else [value]
        vals = [str(v) for v in vals]
        bad = [v for v in vals if v not in opts]
        if bad or not vals:
            raise ValidationError("Please pick at least one of the options.")
        # unavailable options are allowed here: they are recorded and deferred by the planner
        return list(dict.fromkeys(vals))
    if field.kind == "url":
        from ..services.colors import UnsafeURL, validate_url
        try:
            return validate_url(str(value))
        except UnsafeURL as exc:
            raise ValidationError(str(exc)) from exc
    if field.kind == "palette":
        from ..services.colors import CUSTOM, normalize_hex
        v = str(value).strip().lower()
        if v in PALETTES or CUSTOM.match(v):
            return v
        parts = [normalize_hex(x) for x in re.split(r"[\s,/]+", v) if x]
        if len(parts) == 2 and all(parts):
            return f"custom:{parts[0]}:{parts[1]}"
        raise ValidationError("Please pick a colour option, or give two colours like #1f3a5f and #f08a24.")
    if field.kind == "template":
        if str(value) not in {o.value for o in field.option_list(answers)}:
            raise ValidationError("Please pick one of the designs.")
        return str(value)
    if field.kind == "upload":
        v = str(value)
        if not re.fullmatch(r"upload://[a-f0-9]{32}\.(png|jpg|webp)", v):
            raise ValidationError("Please upload the file using the upload button.")
        return v
    raise ValidationError("Unsupported field")


# ---------------------------------------------------------------- adaptive follow-up questions
FOLLOWUP_LABELS = {"emergency": "Urgent call-outs", "quote": "Pricing", "hours": "Working hours", "since": "Started in",
                   "languages": "Languages", "credentials": "Credentials", "first_meeting": "First conversation",
                   "online": "Works online", "walkins": "Walk-ins", "who_for": "Ideal customer",
                   "problem": "Problem you solve", "warranty": "Guarantee"}
MAX_FOLLOWUPS = 3


def choose_followups(a: Answers) -> list[str]:
    """The strategist's choice of which trade-specific questions are worth the owner's time."""
    from ..agents.playbooks import pick_playbook  # local import: playbooks has no schema dependency
    if not a.get("primary_goal"):
        return []
    text = " ".join(str(a.get(k) or "") for k in ("business_description", "differentiators", "proof_points")).lower()
    pb = pick_playbook(None, a.get("business_description") or "", a.get("site_category"))
    chosen = []
    for q in pb.get("questions", []):
        if q == "since" and re.search(r"\b(19|20)\d\d\b|\d+\s*(\+\s*)?(years|yrs)", text):
            continue
        if q == "online" and a.get("remote_service"):
            continue
        if q == "languages" and re.search(r"hindi|english|language|speak", text):
            continue
        if q == "credentials" and re.search(r"licen|regist|member|certif|icai|bar council", text):
            continue
        if q == "emergency" and a.get("primary_goal") not in {"calls", "enquiries"}:
            continue
        chosen.append(q)
    return chosen[:MAX_FOLLOWUPS]


def followup_fields(a: Answers) -> list[Field]:
    from ..agents.playbooks import Q, pick_playbook
    pb = pick_playbook(None, a.get("business_description") or "", a.get("site_category"))
    out = []
    for qid in choose_followups(a):
        q = Q[qid]
        opts = q.get("options")
        out.append(Field(
            f"kb_{qid}", "goal", q["prompt"], q["kind"], q["why"],
            f"Follow-up chosen by the strategist from the {pb['label']} playbook → improves {', '.join(q['unlocks'])}",
            required=False, allow_skip=True, placeholder=q.get("placeholder", ""),
            options=(lambda _a, o=opts: [Option(v, l) for v, l in o]) if opts else None,
            condition=lambda a, qid=qid: qid in choose_followups(a) or f"kb_{qid}" in a,
        ))
        SHORT_LABELS[f"kb_{qid}"] = FOLLOWUP_LABELS[qid]
    return out


def all_fields(a: Answers) -> list[Field]:
    out: list[Field] = []
    for f in FIELDS:
        out.append(f)
        if f.key == "primary_goal":
            out += followup_fields(a)
    return out


def get_field(key: str, a: Answers) -> Field | None:
    if key in FIELD_MAP:
        return FIELD_MAP[key]
    return next((f for f in followup_fields(a) if f.key == key), None)
