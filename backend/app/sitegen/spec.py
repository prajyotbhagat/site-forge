"""Site specification: the contract between the reasoning agents and the renderer.

pages → sections (type + variant + props + content) + a theme. Every agent, the visual critic and the
owner's change requests edit the spec through a small, validated set of patch operations, so no agent can
emit arbitrary HTML or code.
"""
from __future__ import annotations

import copy
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SECTION_VARIANTS: dict[str, list[str]] = {
    "hero": ["split", "centered", "banner", "editorial"],
    "trust_bar": ["chips", "strip"],
    "services": ["cards", "list", "alternating", "bento"],
    "process": ["steps", "timeline"],
    "why": ["grid", "split"],
    "problem": ["contrast"],
    "proof": ["quotes"],
    "area": ["map", "simple"],
    "about": ["split", "simple"],
    "faq": ["accordion", "two_col"],
    "cta": ["band", "card"],
    "contact": ["split", "stacked"],
    "page_head": ["simple", "tinted"],
    "prose": ["simple"],
    "thanks": ["simple"],
}
THEMES = ["classic", "bold_local", "fresh", "warm", "elegant", "tech", "studio", "friendly"]
FONTS = {"inter", "manrope", "space-grotesk", "plus-jakarta-sans", "nunito", "outfit", "fraunces", "source-serif-4",
         "dm-serif-display"}
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    primary: str
    accent: str
    bg: str
    surface: str
    text: str
    muted: str
    on_primary: str
    on_accent: str
    dark: str
    font_heading: str
    font_body: str
    heading_weight: int = Field(ge=400, le=800)
    radius: int = Field(ge=0, le=28)
    scale: float = Field(ge=0.8, le=1.3)
    density: Literal["compact", "normal", "airy"] = "normal"
    button: Literal["pill", "rounded", "square"] = "rounded"
    pattern: Literal["none", "dots", "grid", "waves", "rings"] = "dots"
    shadow: Literal["none", "soft", "lifted"] = "soft"
    tracking: float = Field(default=0, ge=-0.05, le=0.05)

    @field_validator("primary", "accent", "bg", "surface", "text", "muted", "on_primary", "on_accent", "dark")
    @classmethod
    def _hex(cls, v: str) -> str:
        if not HEX.match(v):
            raise ValueError("colour must be #rrggbb")
        return v.lower()

    @field_validator("font_heading", "font_body")
    @classmethod
    def _font(cls, v: str) -> str:
        if v not in FONTS:
            raise ValueError(f"font must be one of {sorted(FONTS)}")
        return v


class Section(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    type: str
    variant: str
    props: dict[str, Any] = {}
    content: dict[str, Any] = {}
    why: str = ""  # which agent decided this and why

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in SECTION_VARIANTS:
            raise ValueError(f"unknown section type {v}")
        return v


class Page(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    nav_label: str | None = None
    meta: dict[str, str]
    sections: list[Section]


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Theme
    site: dict[str, Any]
    pages: list[Page]

    def model_post_init(self, _ctx: Any) -> None:
        for p in self.pages:
            for s in p.sections:
                if s.variant not in SECTION_VARIANTS[s.type]:
                    raise ValueError(f"variant {s.variant!r} not valid for {s.type}")


def validate(spec: dict) -> dict:
    return Spec.model_validate(spec).model_dump()


# ---------------------------------------------------------------- patch operations

OPS = {"set_theme", "set_section_variant", "set_section_prop", "set_content", "move_section", "remove_section",
       "add_section", "set_site"}
THEME_EDITABLE = {"primary", "accent", "bg", "surface", "text", "muted", "on_primary", "on_accent", "dark", "font_heading",
                  "font_body", "heading_weight", "radius", "scale", "density", "button", "pattern", "shadow", "tracking",
                  "id", "name"}
SITE_EDITABLE = {"cta"}
PROPS_EDITABLE = {"columns", "compact", "tone", "align", "illustration", "hide_mobile_art", "max_items", "size", "owner_locked"}


class PatchError(ValueError):
    pass


def _find(spec: dict, section_id: str) -> tuple[dict, int]:
    for page in spec["pages"]:
        for i, s in enumerate(page["sections"]):
            if s["id"] == section_id:
                return page, i
    raise PatchError(f"no section {section_id}")


def apply(spec: dict, ops: list[dict]) -> tuple[dict, list[str]]:
    """Apply validated patch ops; returns the new spec and a human-readable change list."""
    out = copy.deepcopy(spec)
    notes: list[str] = []
    for op in ops:
        kind = op.get("op")
        if kind not in OPS:
            raise PatchError(f"operation {kind!r} is not allowed")
        if kind == "set_theme":
            for k, v in op["values"].items():
                if k not in THEME_EDITABLE:
                    raise PatchError(f"theme field {k} is not editable")
                out["theme"][k] = v
            notes.append(f"Theme: {', '.join(f'{k}→{v}' for k, v in op['values'].items())}")
        elif kind == "set_site":
            for k, v in op["values"].items():
                if k not in SITE_EDITABLE:
                    raise PatchError(f"site field {k} is not editable")
                out["site"][k] = {**out["site"].get(k, {}), **v} if isinstance(v, dict) else v
            notes.append("Updated site settings")
        elif kind == "set_section_variant":
            page, i = _find(out, op["section"])
            sec = page["sections"][i]
            if op["variant"] not in SECTION_VARIANTS[sec["type"]]:
                raise PatchError(f"{op['variant']} is not a {sec['type']} layout")
            sec["variant"] = op["variant"]
            notes.append(f"{sec['type']} layout → {op['variant']}")
        elif kind == "set_section_prop":
            page, i = _find(out, op["section"])
            if op["key"] not in PROPS_EDITABLE:
                raise PatchError(f"property {op['key']} is not editable")
            page["sections"][i]["props"][op["key"]] = op["value"]
            notes.append(f"{page['sections'][i]['type']}: {op['key']} → {op['value']}")
        elif kind == "set_content":
            page, i = _find(out, op["section"])
            content = page["sections"][i]["content"]
            if op["key"] not in content:
                raise PatchError(f"{op['key']} is not editable in this section")
            if not isinstance(op["value"], type(content[op["key"]])):
                raise PatchError("value has the wrong shape")
            content[op["key"]] = op["value"]
            notes.append(f"{page['sections'][i]['type']}: changed {op['key']}")
        elif kind == "move_section":
            page, i = _find(out, op["section"])
            sec = page["sections"].pop(i)
            if op.get("before"):
                _, j = _find(out, op["before"])
                page["sections"].insert(j, sec)
            elif op.get("after"):
                _, j = _find(out, op["after"])
                page["sections"].insert(j + 1, sec)
            else:
                page["sections"].insert(max(1, min(int(op.get("index", 1)), len(page["sections"]))), sec)
            notes.append(f"Moved {sec['type']}")
        elif kind == "remove_section":
            page, i = _find(out, op["section"])
            sec = page["sections"][i]
            if sec["type"] in {"hero", "contact", "page_head", "thanks", "prose"}:
                raise PatchError(f"the {sec['type']} section is required")
            page["sections"].pop(i)
            notes.append(f"Removed {sec['type']}")
        elif kind == "add_section":
            page = next((p for p in out["pages"] if p["slug"] == op.get("page", "index")), None)
            if page is None:
                raise PatchError("no such page")
            sec = op["section"]
            Section.model_validate(sec)
            page["sections"].insert(max(1, min(int(op.get("index", len(page["sections"]) - 1)), len(page["sections"]))), sec)
            notes.append(f"Added {sec['type']}")
    validate(out)
    return out, notes


def outline(spec: dict) -> list[dict]:
    """Compact description for agents, the model and the UI."""
    return [{"page": p["slug"], "sections": [{"id": s["id"], "type": s["type"], "variant": s["variant"],
                                              "props": s["props"]} for s in p["sections"]]} for p in spec["pages"]]
