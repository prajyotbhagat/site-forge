"""Industry knowledge base used by the strategist, architect and copywriter.

Nothing here is ever published as a claim about the business. Playbooks supply:
  - what customers in this industry typically worry about and look for (messaging priorities),
  - which follow-up questions are worth asking the owner (their answers *become* publishable facts),
  - neutral descriptions of what a service is, and the usual way working together goes,
  - layout and art-direction tendencies.
"""
from __future__ import annotations

import re
from typing import Any

# Follow-up question templates. `unlocks` documents which parts of the site the answer improves.
Q = {
    "emergency": {"prompt": "Do you take urgent or emergency call-outs?", "kind": "choice",
                  "options": [["yes_247", "Yes, 24/7"], ["yes_hours", "Yes, during working hours"], ["no", "No"]],
                  "why": "People with urgent problems look for this first; it changes your headline and call button.",
                  "unlocks": ["hero", "trust_bar", "faq"]},
    "quote": {"prompt": "How do you price a job?", "kind": "choice",
              "options": [["free_quote", "Free quote before starting"], ["visit_fee", "Small visit/inspection fee"],
                          ["fixed_fee", "Fixed fees"], ["hourly", "Hourly rates"], ["varies", "It depends"]],
              "why": "Uncertainty about cost is the #1 reason people don't get in touch.",
              "unlocks": ["trust_bar", "faq", "process"]},
    "hours": {"prompt": "What are your working hours?", "kind": "text", "placeholder": "e.g. Mon–Sat, 8am–8pm",
              "why": "Shown in the footer and contact page so people know when to call.",
              "unlocks": ["contact", "faq", "footer"]},
    "since": {"prompt": "Which year did you start the business? (optional)", "kind": "text", "placeholder": "e.g. 2014",
              "why": "Experience builds trust — I'll only mention it if you tell me.", "unlocks": ["trust_bar", "about"]},
    "languages": {"prompt": "Which languages do you speak with customers?", "kind": "text",
                  "placeholder": "e.g. Hindi, English, Garhwali", "why": "Helps people feel comfortable getting in touch.",
                  "unlocks": ["trust_bar", "faq"]},
    "credentials": {"prompt": "Any licence, registration or membership customers should know about?", "kind": "text",
                    "placeholder": "e.g. ICAI member, registered with Bar Council",
                    "why": "In regulated work, verified credentials matter more than anything else on the page.",
                    "unlocks": ["trust_bar", "about"]},
    "first_meeting": {"prompt": "Is the first conversation free?", "kind": "choice",
                      "options": [["free", "Yes, free"], ["paid", "No, it's a paid consultation"], ["varies", "It depends"]],
                      "why": "Removes hesitation for people who aren't sure they need you yet.",
                      "unlocks": ["hero", "faq", "process"]},
    "online": {"prompt": "Can clients work with you fully online?", "kind": "choice",
               "options": [["yes", "Yes, fully online"], ["partly", "Partly"], ["no", "In person only"]],
               "why": "Widens who you can serve; changes the 'Where we work' section.", "unlocks": ["area", "faq"]},
    "walkins": {"prompt": "Do you accept walk-ins, or appointments only?", "kind": "choice",
                "options": [["walkins", "Walk-ins welcome"], ["appointments", "Appointments only"], ["both", "Both"]],
                "why": "Sets expectations before people travel to you.", "unlocks": ["faq", "contact"]},
    "who_for": {"prompt": "Who is your ideal customer, in a few words?", "kind": "text",
                "placeholder": "e.g. seed-stage B2B founders", "why": "Your headline speaks directly to them.",
                "unlocks": ["hero"]},
    "problem": {"prompt": "What problem does your product solve, in one sentence?", "kind": "text",
                "placeholder": "e.g. Clinics lose 20% of bookings to no-shows",
                "why": "A clear problem statement is what makes a launch page convert.", "unlocks": ["hero", "problem"]},
    "warranty": {"prompt": "Do you stand behind your work with any guarantee or warranty?", "kind": "text",
                 "placeholder": "e.g. 90-day workmanship warranty — leave blank if none",
                 "why": "Only published exactly as you write it.", "unlocks": ["trust_bar", "faq"]},
}

PLAYBOOKS: list[dict[str, Any]] = [
    {
        "id": "plumbing", "match": r"plumb", "label": "plumbing", "icon": "droplet",
        "personality": ["dependable", "practical", "responsive"], "theme": "bold_local",
        "audience": "homeowners, landlords and small offices",
        "pains": ["water damage getting worse while they wait", "not knowing what a repair will cost",
                  "plumbers who don't turn up", "mess left behind"],
        "motivators": ["quick response", "clear pricing", "tidy, reliable work"],
        "questions": ["emergency", "quote", "hours", "since"],
        "services": {"leak": ("droplet", "Dripping taps, burst pipes and hidden leaks traced and repaired."),
                     "pipe": ("pipe", "Pipe repairs, re-routing and new connections."),
                     "bath": ("bath", "Bathroom and kitchen plumbing, from taps and basins to full fittings."),
                     "kitchen": ("bath", "Sinks, taps, filters and appliance connections."),
                     "heater": ("flame", "Geyser and water-heater installation, servicing and repair."),
                     "geyser": ("flame", "Geyser installation, servicing and repair."),
                     "drain": ("drain", "Blocked drains and slow sinks cleared."),
                     "tank": ("tank", "Water tank installation, cleaning and pump connections.")},
        "default_services": ["Leak & pipe repairs", "Bathroom & kitchen plumbing", "Water heater installation", "Drain cleaning"],
        "process": [("Tell us the problem", "Call or send a message with what's happening — photos help."),
                    ("We confirm a time", "We'll let you know when we can come and what to expect."),
                    ("Fixed properly", "We do the work and talk you through what we did."),
                    ("Follow-up", "If anything isn't right, let us know.")],
        "sections": ["hero", "trust_bar", "services", "process", "why", "area", "faq", "cta"],
    },
    {
        "id": "electrical", "match": r"electric", "label": "electrical services", "icon": "bolt",
        "personality": ["safe", "dependable", "precise"], "theme": "bold_local",
        "audience": "homeowners, shops and offices",
        "pains": ["safety risks from faulty wiring", "power cuts and tripping breakers", "unclear costs"],
        "motivators": ["safety", "quick response", "clear pricing"],
        "questions": ["emergency", "quote", "credentials", "hours"],
        "services": {"wiring": ("bolt", "New wiring and rewiring done safely to code."),
                     "fault": ("search", "Fault finding for tripping, flickering and dead sockets."),
                     "light": ("bulb", "Indoor and outdoor lighting installation."),
                     "inverter": ("battery", "Inverter, battery and backup power installation."),
                     "appliance": ("plug", "Fan, AC and appliance fitting.")},
        "default_services": ["Wiring & rewiring", "Fault finding & repairs", "Lighting installation", "Inverter & backup power"],
        "process": [("Describe the issue", "Tell us what's happening and where."), ("Safety check", "We find the cause before fixing anything."),
                    ("Repair or install", "Work done with proper materials."), ("Test & handover", "Everything tested before we leave.")],
        "sections": ["hero", "trust_bar", "services", "process", "why", "area", "faq", "cta"],
    },
    {
        "id": "cleaning", "match": r"clean(ing|ers?)\b|housekeep", "label": "cleaning services", "icon": "sparkle",
        "personality": ["fresh", "trustworthy", "thorough"], "theme": "fresh",
        "audience": "busy households and offices",
        "pains": ["trusting strangers in their home", "inconsistent quality", "hidden extra charges"],
        "motivators": ["trust", "consistency", "clear pricing"],
        "questions": ["quote", "hours", "languages"],
        "services": {"deep": ("sparkle", "A top-to-bottom clean of kitchens, bathrooms and living spaces."),
                     "office": ("building", "Regular office and shop cleaning on your schedule."),
                     "move": ("box", "Move-in and move-out cleaning so the space is ready."),
                     "sofa": ("sofa", "Sofa, carpet and upholstery cleaning.")},
        "default_services": ["Home deep cleaning", "Office cleaning", "Move-in / move-out cleaning"],
        "process": [("Tell us about the space", "Size, rooms and what needs attention."), ("Get a quote", "We confirm price and timing."),
                    ("We clean", "Our team arrives with supplies."), ("Walk-through", "Check everything before we leave.")],
        "sections": ["hero", "trust_bar", "services", "process", "why", "faq", "cta"],
    },
    {
        "id": "salon", "match": r"salon|barber|beauty|parlou?r|spa\b|makeup|nail", "label": "salon & beauty", "icon": "scissors",
        "personality": ["stylish", "welcoming", "relaxing"], "theme": "elegant",
        "audience": "people looking for a trusted place for hair and beauty",
        "pains": ["bad results", "long waits", "unclear prices"],
        "motivators": ["skill", "hygiene", "a relaxing visit"],
        "questions": ["walkins", "hours", "quote"],
        "services": {"hair": ("scissors", "Cuts, styling and blow-dries."), "colour": ("palette", "Colour, highlights and treatments."),
                     "color": ("palette", "Colour, highlights and treatments."), "skin": ("leaf", "Facials and skin treatments."),
                     "bridal": ("heart", "Bridal and occasion makeup."), "nail": ("sparkle", "Manicures and pedicures.")},
        "default_services": ["Haircuts & styling", "Colour & treatments", "Skin care", "Bridal & occasion makeup"],
        "process": [("Book or drop in", "Choose a time that suits you."), ("Consultation", "We talk through what you'd like."),
                    ("Relax", "Sit back while we take care of you.")],
        "sections": ["hero", "services", "why", "process", "faq", "cta"],
    },
    {
        "id": "fitness", "match": r"\bgym\b|fitness|yoga|pilates|personal train", "label": "fitness", "icon": "dumbbell",
        "personality": ["energetic", "motivating", "supportive"], "theme": "tech",
        "audience": "people who want to get fitter with guidance",
        "pains": ["feeling intimidated", "not seeing progress", "long contracts"],
        "motivators": ["supportive coaching", "flexible plans", "results"],
        "questions": ["hours", "first_meeting", "quote"],
        "services": {"personal": ("user", "One-to-one coaching built around your goals."), "group": ("users", "Group classes with a friendly crowd."),
                     "yoga": ("leaf", "Yoga for strength, flexibility and calm."), "diet": ("apple", "Nutrition guidance to support training.")},
        "default_services": ["Personal training", "Group classes", "Strength & conditioning"],
        "process": [("Free chat", "Tell us your goals."), ("Plan", "We suggest the right programme."), ("Train", "Start with support from day one.")],
        "sections": ["hero", "services", "why", "process", "faq", "cta"],
    },
    {
        "id": "food", "match": r"bakery|cafe|café|restaurant|caterer|catering|tiffin|cloud kitchen|sweets?", "label": "food & hospitality", "icon": "cup",
        "personality": ["warm", "inviting", "local"], "theme": "warm",
        "audience": "locals and visitors looking for good food",
        "pains": ["not knowing the menu or prices", "unclear opening hours"], "motivators": ["taste", "atmosphere", "convenience"],
        "questions": ["hours", "walkins"],
        "services": {"cater": ("users", "Catering for events and offices."), "cake": ("cake", "Cakes made to order."),
                     "delivery": ("bike", "Delivery to your door."), "breakfast": ("cup", "Breakfast and coffee.")},
        "default_services": ["Dine-in", "Takeaway", "Catering"],
        "process": [],
        "sections": ["hero", "services", "why", "area", "faq", "cta"],
    },
    {
        "id": "tutoring", "match": r"tuition|tutor|coaching|classes|academy|teach", "label": "tutoring & coaching", "icon": "book",
        "personality": ["encouraging", "structured", "patient"], "theme": "friendly",
        "audience": "parents and students", "pains": ["falling behind", "exam pressure", "large impersonal classes"],
        "motivators": ["personal attention", "clear progress", "convenient timings"],
        "questions": ["online", "first_meeting", "hours"],
        "services": {"math": ("calculator", "Concept-first maths support."), "science": ("flask", "Physics, chemistry and biology made clear."),
                     "english": ("book", "Reading, writing and spoken English."), "exam": ("target", "Focused exam preparation and practice tests.")},
        "default_services": ["One-to-one tutoring", "Small group classes", "Exam preparation"],
        "process": [("Trial session", "Meet and understand where the student is."), ("Learning plan", "We agree goals and a schedule."),
                    ("Regular updates", "Parents hear how things are going.")],
        "sections": ["hero", "services", "process", "why", "faq", "cta"],
    },
    {
        "id": "accounting", "match": r"account(ing|ant)|bookkeep|chartered|\bca\b|\btax\b|\bgst\b|audit", "label": "accounting and tax", "icon": "calculator",
        "personality": ["trustworthy", "precise", "approachable"], "theme": "classic",
        "audience": "small businesses, professionals and families", "regulated": "financial",
        "pains": ["missing deadlines and penalties", "confusing tax rules", "messy books before filing"],
        "motivators": ["accuracy", "on-time filing", "clear advice"],
        "questions": ["credentials", "first_meeting", "online", "quote"],
        "services": {"income tax": ("file", "Preparing and filing income-tax returns, with help on notices."),
                     "itr": ("file", "Preparing and filing income-tax returns."),
                     "gst": ("receipt", "GST registration, monthly and quarterly returns, and reconciliations."),
                     "book": ("ledger", "Monthly bookkeeping so your accounts stay ready."),
                     "audit": ("search", "Statutory and internal audits."),
                     "payroll": ("users", "Payroll processing and compliance."),
                     "advis": ("compass", "Practical advice on structure, planning and growth."),
                     "registration": ("briefcase", "Company, LLP and firm registration.")},
        "default_services": ["Income tax filing", "GST registration & returns", "Bookkeeping", "Business advisory"],
        "process": [("Initial conversation", "Tell us what you need help with."), ("Share documents", "We send a simple checklist."),
                    ("We prepare & review", "Everything checked with you before filing."), ("Filed & on record", "You get copies and reminders for next time.")],
        "sections": ["hero", "trust_bar", "services", "process", "why", "about", "faq", "cta"],
    },
    {
        "id": "legal", "match": r"\blaw(yer)?\b|legal|advocate|attorney|solicitor", "label": "legal services", "icon": "scale",
        "personality": ["authoritative", "discreet", "clear"], "theme": "classic",
        "audience": "individuals and businesses who need legal help", "regulated": "legal",
        "pains": ["not knowing where they stand", "fear of high costs", "slow responses"],
        "motivators": ["expertise", "discretion", "clear communication"],
        "questions": ["credentials", "first_meeting", "languages"],
        "services": {"property": ("home", "Property documentation, verification and disputes."),
                     "family": ("users", "Family and matrimonial matters handled with care."),
                     "civil": ("scale", "Civil disputes and recovery."), "criminal": ("shield", "Criminal defence and bail matters."),
                     "corporate": ("briefcase", "Contracts, compliance and company matters.")},
        "default_services": ["Consultations", "Documentation & drafting", "Representation"],
        "process": [("Confidential consultation", "Explain your situation."), ("Options explained", "We outline the routes available."),
                    ("Act on your behalf", "We take it forward and keep you informed.")],
        "sections": ["hero", "trust_bar", "services", "process", "about", "faq", "cta"],
    },
    {
        "id": "health", "match": r"clinic|dentist|dental|doctor|physio|therap|counsell|hospital|diagnostic", "label": "healthcare", "icon": "heart",
        "personality": ["caring", "calm", "professional"], "theme": "fresh",
        "audience": "patients and families", "regulated": "health",
        "pains": ["anxiety about treatment", "long waits", "unclear costs"], "motivators": ["care", "expertise", "convenience"],
        "questions": ["credentials", "hours", "walkins"],
        "services": {"dental": ("tooth", "Check-ups, cleaning and treatment."), "physio": ("activity", "Physiotherapy for pain and recovery."),
                     "consult": ("chat", "Consultations to understand your concern."), "therap": ("heart", "Therapy sessions in a safe space.")},
        "default_services": ["Consultations", "Treatment", "Follow-up care"],
        "process": [("Book a visit", "Choose a convenient time."), ("Consultation", "We listen and examine."), ("Care plan", "Clear next steps.")],
        "sections": ["hero", "trust_bar", "services", "process", "about", "faq", "cta"],
    },
    {
        "id": "architecture", "match": r"architect|interior", "label": "architecture & interiors", "icon": "ruler",
        "personality": ["refined", "creative", "considered"], "theme": "studio",
        "audience": "homeowners and businesses planning a space", "pains": ["budget overruns", "designs that don't fit how they live"],
        "motivators": ["taste", "practicality", "clear process"], "questions": ["first_meeting", "quote", "since"],
        "services": {"interior": ("sofa", "Interior design from concept to finish."), "architect": ("ruler", "Architectural design and drawings."),
                     "renovat": ("hammer", "Renovation planning and supervision.")},
        "default_services": ["Architectural design", "Interior design", "Renovation"],
        "process": [("Brief", "Understand your needs and budget."), ("Concept", "Ideas and layouts to react to."),
                    ("Detailed design", "Drawings and materials."), ("Delivery", "Support through execution.")],
        "sections": ["hero", "services", "process", "about", "faq", "cta"],
    },
    {
        "id": "consulting", "match": r"consult|advis|coach(?!ing cent)|agency|marketing", "label": "consulting", "icon": "compass",
        "personality": ["insightful", "credible", "results-focused"], "theme": "classic",
        "audience": "businesses that want expert help", "pains": ["unclear next steps", "wasted spend", "lack of in-house expertise"],
        "motivators": ["expertise", "clarity", "measurable outcomes"], "questions": ["first_meeting", "online", "who_for"],
        "services": {"strategy": ("compass", "Strategy work to decide what to focus on."), "market": ("chart", "Marketing that brings in the right customers."),
                     "brand": ("palette", "Brand and identity."), "web": ("code", "Websites and digital tools.")},
        "default_services": ["Strategy", "Implementation support", "Ongoing advisory"],
        "process": [("Discovery call", "Understand your goals."), ("Proposal", "A clear scope and plan."), ("Delivery", "Work in short, visible steps.")],
        "sections": ["hero", "services", "process", "why", "about", "faq", "cta"],
    },
    {
        "id": "startup", "match": r"start-?up|\bsaas\b|\bapp\b|platform|software|\bai\b|product", "label": "startup", "icon": "rocket",
        "personality": ["bold", "clear", "modern"], "theme": "tech",
        "audience": "early adopters", "pains": [], "motivators": ["solves a real problem", "easy to start"],
        "questions": ["problem", "who_for"],
        "services": {}, "default_services": [],
        "process": [("Join the waitlist", "Leave your email."), ("Early access", "We invite people in batches."), ("Shape the product", "Tell us what you need.")],
        "sections": ["hero", "problem", "services", "process", "faq", "cta"],
    },
    {
        "id": "portfolio", "match": r"portfolio|freelanc|photograph|illustrat|\bartist\b|designer|musician|writer", "label": "creative portfolio", "icon": "camera",
        "personality": ["distinctive", "confident", "personal"], "theme": "studio",
        "audience": "clients looking for creative work", "pains": [], "motivators": ["style", "reliability"],
        "questions": ["who_for", "online"], "services": {}, "default_services": [],
        "process": [("Brief", "Tell me what you have in mind."), ("Proposal", "Scope, timeline and price."), ("Create", "Work with check-ins along the way.")],
        "sections": ["hero", "services", "about", "process", "cta"],
    },
]

GENERIC = {
    "local_service": {"id": "local", "label": "local services", "icon": "home", "personality": ["dependable", "friendly", "local"],
                      "theme": "bold_local", "audience": "local customers", "pains": ["unreliable providers", "unclear costs"],
                      "motivators": ["reliability", "clear pricing", "local knowledge"], "questions": ["quote", "hours", "since"],
                      "services": {}, "default_services": [], "process": [("Get in touch", "Tell us what you need."), ("Quote & timing", "We confirm details."), ("Job done", "Work completed and checked.")],
                      "sections": ["hero", "trust_bar", "services", "process", "why", "area", "faq", "cta"]},
    "professional_service": {"id": "pro", "label": "professional services", "icon": "briefcase", "personality": ["credible", "clear", "approachable"],
                             "theme": "classic", "audience": "clients who need expert help", "pains": ["uncertainty", "cost"],
                             "motivators": ["expertise", "clear advice"], "questions": ["first_meeting", "online", "credentials"],
                             "services": {}, "default_services": [], "process": [("Initial conversation", "Tell us what you need."), ("Plan", "We agree scope and fees."), ("Delivery", "We do the work and keep you updated.")],
                             "sections": ["hero", "services", "process", "why", "about", "faq", "cta"]},
}

ICON_KEYWORDS = [
    (r"leak|water|plumb", "droplet"), (r"pipe", "pipe"), (r"bath|basin|toilet|kitchen", "bath"), (r"heat|geyser|boiler", "flame"),
    (r"drain|sewer", "drain"), (r"wir|electric|power", "bolt"), (r"light|lamp", "bulb"), (r"remind|notif|alert", "clock"), (r"whatsapp|sms|message|chat", "chat"), (r"analytic|report|insight|dashboard", "chart"), (r"waitlist|queue|backfill|schedul|calendar|booking", "calendar"),
    (r"tax|itr|return|file|filing", "file"),
    (r"gst|invoice|bill", "receipt"), (r"book|ledger|account", "ledger"), (r"audit|inspect|check|fault", "search"),
    (r"advis|consult|strategy|plan", "compass"), (r"register|company|business|corporate", "briefcase"), (r"payroll|team|staff|group", "users"),
    (r"clean", "sparkle"), (r"hair|cut|style", "scissors"), (r"colou?r|paint|brand|design", "palette"), (r"skin|facial|yoga|organic", "leaf"),
    (r"law|legal|court|civil", "scale"), (r"property|home|house|real estate", "home"), (r"tooth|dental", "tooth"), (r"physio|rehab", "activity"),
    (r"market|seo|growth|sales", "chart"), (r"web|app|software|code|it\b", "code"), (r"photo|camera|shoot", "camera"),
    (r"train|fitness|gym", "dumbbell"), (r"math|calcul", "calculator"), (r"science|lab", "flask"), (r"exam|test", "target"),
    (r"deliver|transport|move|shift", "truck"), (r"\bcars?\b|vehicle|\bbikes?\b|\bauto\b|garage", "car"), (r"cake|bake", "cake"), (r"coffee|cafe|tea|food|meal", "cup"),
    (r"insur|safe|secur|protect|defen", "shield"), (r"event|wedding|bridal", "heart"), (r"consult|talk|session|therapy", "chat"),
]


def pick_playbook(business_type: str | None, description: str, category: str | None) -> dict:
    text = f"{business_type or ''} {description or ''}".lower()
    # the business model outranks topic keywords: a SaaS *for clinics* is a startup, not a clinic
    if category in {"startup", "portfolio"}:
        return next(pb for pb in PLAYBOOKS if pb["id"] == category)
    for pb in PLAYBOOKS:
        if re.search(pb["match"], text):
            return pb
    return GENERIC.get(category or "", GENERIC["professional_service"])


def icon_for(text: str, fallback: str = "check") -> str:
    low = text.lower()
    for pat, icon in ICON_KEYWORDS:
        if re.search(pat, low):
            return icon
    return fallback


def describe_service(pb: dict, name: str) -> tuple[str, str] | None:
    low = name.lower()
    for kw, (icon, desc) in pb.get("services", {}).items():
        if kw in low:
            return icon, desc
    return None
