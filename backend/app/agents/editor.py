"""Editor agent: turns an owner's plain-English change request into validated spec patch operations.

"Make the colours darker and move the FAQ above services" →
    [set_theme(primary=…, dark=…), move_section(faq, before=services)]

With a language model configured the request is interpreted by the model (constrained to the same op
vocabulary); otherwise a rule-based interpreter handles the common requests. Either way every op goes
through `spec.apply` validation, headline/text edits go through the unsupported-claims check, and colour
edits are re-balanced for contrast.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any

from pydantic import BaseModel, Field

from ..onboarding.schema import PALETTES
from ..sitegen import spec as specmod
from .design import check_claims, contrast
from .llm import LLMProvider, ProviderUnavailable
from .team import THEME_BASE, build_theme, mix

SECTION_ALIASES = [
    (r"\b(hero|top section|top of the page|header banner|banner at the top|headline area|first section)\b", "hero"),
    (r"\b(services?|what we do|products?|features?)\b", "services"),
    (r"\b(process|how it works|steps)\b", "process"),
    (r"\b(faqs?|questions)\b", "faq"),
    (r"\b(area|where we work|map|locations?)\b", "area"),
    (r"\b(why( choose)?( us)?|reasons)\b", "why"),
    (r"\b(trust( bar)?|highlights|badges)\b", "trust_bar"),
    (r"\b(about)\b", "about"),
    (r"\b(reviews?|testimonials?|credentials|proof)\b", "proof"),
    (r"\b(contact|form|enquiry form)\b", "contact"),
    (r"\b(call to action|cta|bottom banner|call band)\b", "cta"),
    (r"\b(problem)\b", "problem"),
]
COLOURS = {"navy": "#1f3a5f", "blue": "#1d4ed8", "sky": "#0369a1", "teal": "#0f766e", "green": "#166534", "forest": "#2f5d50",
           "olive": "#4d5a1f", "red": "#b91c1c", "maroon": "#7f1d1d", "orange": "#c2410c", "terracotta": "#a4492f",
           "brown": "#6b4423", "purple": "#6d28d9", "violet": "#5b21b6", "indigo": "#3b3f9a", "pink": "#be185d",
           "magenta": "#a21caf", "black": "#18181b", "charcoal": "#26292e", "grey": "#475569", "gray": "#475569",
           "gold": "#b08d57", "yellow": "#ca8a04", "mustard": "#b7791f"}
STYLE_WORDS = [(r"elegan|luxur|premium|sophisticat", "elegant"), (r"classic|formal|traditional|corporate|professional", "classic"),
               (r"modern|tech|sleek|futur", "tech"), (r"bold|punchy|strong|loud", "bold_local"), (r"minimal|simple|clean", "studio"),
               (r"friendly|playful|fun", "friendly"), (r"warm|cosy|cozy|welcoming", "warm"), (r"fresh|airy|light and", "fresh")]


def _home(spec: dict) -> dict:
    return next(p for p in spec["pages"] if p["slug"] == "index")


def _find(spec: dict, typ: str, page_slug: str = "index") -> dict | None:
    page = next((p for p in spec["pages"] if p["slug"] == page_slug), _home(spec))
    return next((s for s in page["sections"] if s["type"] == typ), None)


def _sections_in(text: str) -> list[str]:
    hits = []
    for pat, typ in SECTION_ALIASES:
        for m in re.finditer(pat, text):
            hits.append((m.start(), typ))
    return [t for _, t in sorted(hits)]


def _balanced_primary(hex_: str) -> str:
    while contrast("#ffffff", hex_) < 4.6:
        hex_ = mix(hex_, "#000000", 0.08)
    return hex_


def _theme_with(theme: dict, **kw) -> dict:
    vals = dict(kw)
    if "primary" in vals:
        p = _balanced_primary(vals["primary"])
        vals["primary"] = p
        dark = p
        while contrast("#ffffff", dark) < 10:
            dark = mix(dark, "#000000", 0.12)
        vals["dark"] = dark
        vals["surface"] = mix(p, theme["bg"], 0.93)
    return vals


def interpret_rules(text: str, spec: dict, library: dict) -> tuple[list[dict], list[str], list[str]]:
    ops: list[dict] = []
    said: list[str] = []
    unknown: list[str] = []
    theme = spec["theme"]
    # protect quoted text, then split on sentence breaks, commas and "and"
    quotes: list[str] = []
    def _keep(m):
        quotes.append(m.group(0))
        return f"\x00{len(quotes) - 1}\x00"
    protected = re.sub(r"[\"“'‘][^\"”'’]+[\"”'’]", _keep, text)
    parts = re.split(r"(?:\.\s+|;|\n|,|\s+and\s+|\s+also\s+|\s+then\s+)", protected, flags=re.I)
    clauses = [re.sub(r"\x00(\d+)\x00", lambda m: quotes[int(m.group(1))], p_).strip() for p_ in parts if p_ and p_.strip(" .")]
    for raw in clauses:
        n0, s0 = len(ops), len(said)
        _clause(raw, ops, said, unknown, spec, library, theme)
        if len(said) > s0 and len(ops) > n0:
            ops[n0]["_msg"] = said[-1]
    return ops, said, unknown


def _clause(raw: str, ops: list, said: list, unknown: list, spec: dict, library: dict, theme: dict) -> None:
    if True:
        c = raw.lower().strip(" .!")
        handled = True
        quoted = re.search(r"[\"“'‘](.+?)[\"”'’]", raw)
        secs = _sections_in(c)
        # ---- text edits
        if re.search(r"\b(headline|title|heading)\b", c) and (quoted or re.search(r"\bto\b\s+(.+)$", raw)):
            new = (quoted.group(1) if quoted else re.search(r"\bto\b\s+(.+)$", raw).group(1)).strip(" .\"'")
            hero = _find(spec, "hero")
            if len(new) > 140:
                unknown.append(f"That headline is {len(new)} characters — please keep it under 140 (ideally under 60)")
                return
            ops.append({"op": "set_content", "section": hero["id"], "key": "headline", "value": new})
            ops.append({"op": "set_section_prop", "section": hero["id"], "key": "owner_locked",
                        "value": sorted(set(hero["props"].get("owner_locked", [])) | {"headline"})})
            said.append(f"Headline → “{new}”" + (" (long — the layout critic will check it fits on phones)" if len(new) > 60 else ""))
        elif re.search(r"\b(button|cta)\b.*\b(text|label|say|read)", c) and quoted:
            hero = _find(spec, "hero")
            cta = dict(hero["content"]["primary_cta"], label=quoted.group(1)[:40])
            ops.append({"op": "set_content", "section": hero["id"], "key": "primary_cta", "value": cta})
            ops.append({"op": "set_site", "values": {"cta": {"label": quoted.group(1)[:40]}}})
            said.append(f"Main button → “{quoted.group(1)[:40]}”")
        # ---- layout variants
        elif secs and not re.search(r"\b(move|remove|hide|delete|add)\b", c) and re.search(r"\b(list|grid|cards?|bento|centred|centered|split|banner|editorial|timeline|accordion|two column|2 column)\b", c):
            s = _find(spec, secs[0])
            want = re.search(r"\b(list|grid|cards?|bento|centred|centered|split|banner|editorial|timeline|accordion|two column|2 column)\b", c).group(1)
            want = {"grid": "cards", "card": "cards", "centred": "centered", "two column": "two_col", "2 column": "two_col"}.get(want, want)
            if s and want in specmod.SECTION_VARIANTS[s["type"]]:
                ops.append({"op": "set_section_variant", "section": s["id"], "variant": want})
                ops.append({"op": "set_section_prop", "section": s["id"], "key": "owner_locked",
                            "value": sorted(set(s["props"].get("owner_locked", [])) | {"variant"})})
                said.append(f"{secs[0].replace('_', ' ').title()} as {want.replace('_', ' ')}")
            else:
                unknown.append(raw + f" (available layouts: {', '.join(specmod.SECTION_VARIANTS.get(secs[0], []))})")
                return
        # ---- structure
        elif re.search(r"\b(move|put|place)\b", c) and secs:
            target = _find(spec, secs[0])
            if not target:
                unknown.append(raw)
                return
            if re.search(r"\b(first|top|start|beginning|higher|up)\b", c) and len(secs) == 1:
                ops.append({"op": "move_section", "section": target["id"], "index": 1})
                said.append(f"Moved {secs[0]} near the top")
            elif re.search(r"\b(last|bottom|end|lower|down)\b", c) and len(secs) == 1:
                ops.append({"op": "move_section", "section": target["id"], "index": len(_home(spec)["sections"])})
                said.append(f"Moved {secs[0]} further down")
            elif len(secs) >= 2 and _find(spec, secs[1]):
                rel = "before" if re.search(r"\b(above|before|ahead of|over)\b", c) else "after"
                ops.append({"op": "move_section", "section": target["id"], rel: _find(spec, secs[1])["id"]})
                said.append(f"Moved {secs[0]} {('above' if rel == 'before' else 'below')} {secs[1]}")
            else:
                handled = False
        elif re.search(r"\b(remove|hide|delete|drop|get rid of)\b", c) and secs:
            s = _find(spec, secs[0])
            if s and secs[0] not in {"hero", "contact"}:
                ops.append({"op": "remove_section", "section": s["id"]})
                said.append(f"Removed the {secs[0]} section")
            else:
                unknown.append(raw + " (that section is required)")
                return
        elif re.search(r"\b(add|include|show)\b", c) and secs:
            typ = secs[0]
            if _find(spec, typ):
                said.append(f"The {typ} section is already on the page")
            elif typ in library and library[typ] and all(library[typ].get(k) for k in ("items", "steps", "paragraphs", "places") if k in library[typ]):
                variant = specmod.SECTION_VARIANTS[typ][0]
                ops.append({"op": "add_section", "page": "index", "index": max(1, len(_home(spec)["sections"]) - 1),
                            "section": {"id": f"index-{typ}-added", "type": typ, "variant": variant, "props": {},
                                        "content": library[typ], "why": "Added at your request"}})
                said.append(f"Added a {typ} section")
            else:
                unknown.append(raw + " (I don't have real content for that yet — answer the related questions first)")
                return
        elif re.search(r"\bdark\b", c) and secs and secs[0] in {"hero", "cta"}:
            s = _find(spec, secs[0])
            if secs[0] == "hero" and s["variant"] not in {"banner", "centered"}:
                ops.append({"op": "set_section_variant", "section": s["id"], "variant": "banner"})
            ops.append({"op": "set_section_prop", "section": s["id"], "key": "tone", "value": "dark"})
            said.append(f"Dark {secs[0]}")
        # ---- theme
        elif re.search(r"\b(darker|deeper)\b", c):
            ops.append({"op": "set_theme", "values": _theme_with(theme, primary=mix(theme["primary"], "#000000", 0.22))})
            said.append("Deeper main colour")
        elif re.search(r"\b(lighter|brighter|more vibrant|more colou?rful)\b", c):
            ops.append({"op": "set_theme", "values": _theme_with(theme, primary=mix(theme["primary"], "#ffffff", 0.18))})
            said.append("Brighter main colour (kept readable)")
        elif re.search(r"#[0-9a-f]{6}\b", c) or any(re.search(rf"\b{k}\b", c) for k in COLOURS) or any(re.search(rf"\b{k}\b", c) for k in PALETTES):
            m = re.search(r"#[0-9a-f]{6}\b", c)
            col = m.group(0) if m else next((v for k, v in COLOURS.items() if re.search(rf"\b{k}\b", c)), None)
            pal = next((k for k in PALETTES if re.search(rf"\b{k}\b", c)), None)
            if re.search(r"\baccent|highlight|secondary\b", c) and col:
                on = "#111111" if contrast("#111111", col) >= contrast("#ffffff", col) else "#ffffff"
                ops.append({"op": "set_theme", "values": {"accent": col, "on_accent": on}})
                said.append(f"Accent colour → {col}")
            elif pal and not m:
                p = PALETTES[pal][1]
                vals = _theme_with(theme, primary=p["primary"])
                vals.update({"accent": p["accent"], "on_accent": "#111111" if contrast("#111111", p["accent"]) >= contrast("#ffffff", p["accent"]) else "#ffffff"})
                ops.append({"op": "set_theme", "values": vals})
                said.append(f"Colour scheme → {PALETTES[pal][0]}")
            elif col:
                ops.append({"op": "set_theme", "values": _theme_with(theme, primary=col)})
                said.append(f"Main colour → {col} (adjusted for readability)")
            else:
                handled = False
        elif re.search(r"\b(bigger|larger|increase)\b.*\b(text|font|heading|type|headline)s?\b|\b(text|font|headings?)\b.*\b(bigger|larger)\b", c):
            ops.append({"op": "set_theme", "values": {"scale": round(min(1.25, theme["scale"] + 0.08), 2)}})
            said.append("Larger headings")
        elif re.search(r"\b(smaller|reduce|decrease)\b.*\b(text|font|heading|type|headline)s?\b|\b(text|font|headings?)\b.*\bsmaller\b", c):
            ops.append({"op": "set_theme", "values": {"scale": round(max(0.85, theme["scale"] - 0.08), 2)}})
            said.append("Smaller headings")
        elif re.search(r"\b(round|rounder|rounded|softer|pill)\b", c):
            ops.append({"op": "set_theme", "values": {"radius": 20, "button": "pill"}})
            said.append("Rounder corners and pill buttons")
        elif re.search(r"\b(sharp|square|straight)\b", c):
            ops.append({"op": "set_theme", "values": {"radius": 0, "button": "square"}})
            said.append("Square corners")
        elif re.search(r"\b(more (white ?)?space|spacious|airy|breathing room|less cramped)\b", c):
            ops.append({"op": "set_theme", "values": {"density": "airy"}})
            said.append("More space between sections")
        elif re.search(r"\b(tighter|compact|less (white ?)?space|denser|shorter page)\b", c):
            ops.append({"op": "set_theme", "values": {"density": "compact"}})
            said.append("Tighter spacing")
        elif re.search(r"\b(no|remove|without)\b.*\bpattern|plain background\b", c):
            ops.append({"op": "set_theme", "values": {"pattern": "none"}})
            said.append("Removed background pattern")
        elif any(re.search(p, c) for p, _ in STYLE_WORDS) and re.search(r"\b(more|look|feel|style|make it|design)\b", c):
            tid = next(t for p, t in STYLE_WORDS if re.search(p, c))
            pal_key = next((k for k, (_, v) in PALETTES.items() if v["primary"] == theme["primary"]), "navy")
            nt = build_theme(tid, pal_key, None)
            nt.update({k: theme[k] for k in ("primary", "accent", "on_accent", "dark", "surface")})
            ops.append({"op": "set_theme", "values": {k: v for k, v in nt.items() if k in specmod.THEME_EDITABLE}})
            said.append(f"Restyled as “{THEME_BASE[tid]['name']}” (kept your colours)")
        else:
            handled = False
        if not handled:
            unknown.append(raw)


class _LLMEdit(BaseModel):
    ops: list[dict] = Field(default_factory=list, max_length=12)
    summary: list[str] = Field(default_factory=list)
    not_possible: list[str] = Field(default_factory=list)


def interpret(text: str, spec: dict, library: dict, owner_text: str, provider: LLMProvider) -> dict:
    ops, said, unknown = [], [], []
    try:
        out = provider.complete_json(
            "You are the editor on a website team. Convert the owner's change request into patch ops ONLY from this "
            f"vocabulary: {sorted(specmod.OPS)}. Editable theme fields: {sorted(specmod.THEME_EDITABLE)}; section props: "
            f"{sorted(specmod.PROPS_EDITABLE)}; variants: {specmod.SECTION_VARIANTS}. Use existing section ids from the outline. "
            "Never invent facts in text you write. List anything you can't do in not_possible.",
            json.dumps({"request": text, "outline": specmod.outline(spec), "theme": spec["theme"],
                        "hero": _find(spec, "hero")["content"]}), _LLMEdit)
        ops, said, unknown = out.ops, out.summary, out.not_possible
    except ProviderUnavailable:
        ops, said, unknown = interpret_rules(text, spec, library)

    # validate one by one so a single bad op doesn't sink the whole request
    good, cur = [], copy.deepcopy(spec)
    tagged = any("_msg" in o for o in ops)
    done = [] if tagged else list(said)  # model path: use its own summary
    if tagged:  # notes that came without an op (e.g. "already on the page")
        msgs = {o.get("_msg") for o in ops}
        done = [m for m in said if m not in msgs]
    rejected_sections: set = set()
    for op in ops:
        msg = op.pop("_msg", None)
        if op.get("op") == "set_section_prop" and op.get("key") == "owner_locked" and op.get("section") in rejected_sections:
            continue  # companion of a refused text edit
        if op.get("op") == "set_content" and isinstance(op.get("value"), str) and check_claims([op["value"]], owner_text):
            unknown.append(f"“{op['value']}” contains a claim you haven't confirmed (like years, awards or guarantees)")
            rejected_sections.add(op.get("section"))
            continue
        if op.get("op") == "set_theme" and "primary" in op.get("values", {}):
            op = {"op": "set_theme", "values": {**op["values"], **_theme_with(cur["theme"], primary=op["values"]["primary"])}}
        try:
            cur, _ = specmod.apply(cur, [op])
            good.append(op)
            if msg:
                done.append(msg)
        except (ValueError, KeyError, TypeError) as exc:
            unknown.append(f"Couldn't apply one change: {str(exc)[:120]}")
    return {"ops": good, "spec": cur, "summary": done, "not_understood": unknown}
