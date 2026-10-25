"""Generative SVG art: hero illustrations, maps, patterns and icons, all derived from the theme palette.

Output contains only numbers, colours from the validated theme and whitelisted icon paths — never user
text — so it is safe to inline.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path
from urllib.parse import quote

from markupsafe import Markup

ICONS: dict[str, str] = json.loads((Path(__file__).parent / "icons.json").read_text())


def icon(name: str, cls: str = "ic", size: int = 24) -> Markup:
    inner = ICONS.get(name) or ICONS["check"]
    return Markup(f'<svg class="{cls}" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  f'stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">{inner}</svg>')


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:12], 16))


def blob(cx: float, cy: float, r: float, rng: random.Random, points: int = 7, wobble: float = 0.22) -> str:
    pts = []
    for i in range(points):
        a = 2 * math.pi * i / points
        rr = r * (1 + rng.uniform(-wobble, wobble))
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d = []
    n = len(pts)
    for i in range(n):
        p0, p1, p2, p3 = pts[i - 1], pts[i], pts[(i + 1) % n], pts[(i + 2) % n]
        c1 = (p1[0] + (p2[0] - p0[0]) / 6, p1[1] + (p2[1] - p0[1]) / 6)
        c2 = (p2[0] - (p3[0] - p1[0]) / 6, p2[1] - (p3[1] - p1[1]) / 6)
        if i == 0:
            d.append(f"M{p1[0]:.1f},{p1[1]:.1f}")
        d.append(f"C{c1[0]:.1f},{c1[1]:.1f} {c2[0]:.1f},{c2[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}")
    return " ".join(d) + "Z"


def _icon_at(name: str, x: float, y: float, size: float, color: str, sw: float = 1.9) -> str:
    s = size / 24
    return (f'<g transform="translate({x:.1f},{y:.1f}) scale({s:.3f})" fill="none" stroke="{color}" stroke-width="{sw}" '
            f'stroke-linecap="round" stroke-linejoin="round">{ICONS.get(name, ICONS["check"])}</g>')


def hero_art(t: dict, main_icon: str, icons: list[str], seed: str) -> Markup:
    """A composed illustration: soft blob, rings, a main 'card' with the trade icon and floating chips."""
    rng = _rng(seed)
    W, H = 560, 470
    p, a, s, bg = t["primary"], t["accent"], t["surface"], t["bg"]
    r = max(4, t["radius"])
    parts = [f'<svg class="hero-art" viewBox="0 0 {W} {H}" role="img" aria-hidden="true" focusable="false" xmlns="http://www.w3.org/2000/svg">']
    parts.append(f'<path d="{blob(290, 240, 190, rng)}" fill="{s}"/>')
    parts.append(f'<path d="{blob(410, 120, 70, rng, 6, 0.3)}" fill="{a}" opacity=".9"/>')
    for i in range(3):
        parts.append(f'<circle cx="{110 + i * 4}" cy="{360 - i * 2}" r="{40 + i * 22}" fill="none" stroke="{p}" stroke-opacity="{0.18 - i * 0.04:.2f}" stroke-width="2"/>')
    for i in range(6):
        for j in range(4):
            parts.append(f'<circle cx="{430 + i * 16}" cy="{330 + j * 16}" r="2.2" fill="{p}" opacity=".35"/>')
    # main card
    cx, cy, cw, ch = 170, 110, 250, 250
    parts.append(f'<rect x="{cx + 10}" y="{cy + 14}" width="{cw}" height="{ch}" rx="{r + 6}" fill="{p}" opacity=".18"/>')
    parts.append(f'<rect x="{cx}" y="{cy}" width="{cw}" height="{ch}" rx="{r + 6}" fill="#ffffff"/>')
    parts.append(f'<circle cx="{cx + cw / 2}" cy="{cy + 105}" r="62" fill="{p}"/>')
    parts.append(_icon_at(main_icon, cx + cw / 2 - 34, cy + 71, 68, "#ffffff", 1.7))
    for k, w in enumerate((150, 110)):
        parts.append(f'<rect x="{cx + (cw - w) / 2}" y="{cy + 190 + k * 20}" width="{w}" height="9" rx="4.5" fill="{p}" opacity="{0.22 - k * 0.08:.2f}"/>')
    # floating chips with service icons
    spots = [(60, 70), (440, 250), (330, 395)]
    for (x, y), ic in zip(spots, (icons or [main_icon])[:3]):
        parts.append(f'<rect x="{x}" y="{y}" width="64" height="64" rx="{min(r + 4, 32)}" fill="#ffffff" stroke="{p}" stroke-opacity=".12"/>')
        parts.append(_icon_at(ic, x + 18, y + 18, 28, p))
    parts.append(f'<circle cx="{486}" cy="{56}" r="10" fill="{p}"/><circle cx="72" cy="420" r="7" fill="{a}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def map_art(t: dict, seed: str) -> Markup:
    rng = _rng(seed + "map")
    p, a, s = t["primary"], t["accent"], t["surface"]
    W, H = 480, 380
    parts = [f'<svg class="map-art" viewBox="0 0 {W} {H}" aria-hidden="true" focusable="false" xmlns="http://www.w3.org/2000/svg">',
             f'<rect width="{W}" height="{H}" rx="{t["radius"] + 6}" fill="{s}"/>']
    for i in range(7):  # roads
        y = rng.randint(20, H - 20)
        parts.append(f'<path d="M0,{y} C{W * .3},{y + rng.randint(-60, 60)} {W * .6},{y + rng.randint(-60, 60)} {W},{y + rng.randint(-40, 40)}" '
                     f'stroke="#ffffff" stroke-width="{rng.choice([6, 10, 14])}" fill="none" opacity=".9"/>')
    for i in range(4):
        x = rng.randint(30, W - 30)
        parts.append(f'<path d="M{x},0 C{x + rng.randint(-50, 50)},{H * .4} {x + rng.randint(-50, 50)},{H * .7} {x + rng.randint(-40, 40)},{H}" '
                     f'stroke="#ffffff" stroke-width="8" fill="none" opacity=".8"/>')
    cx, cy = W / 2, H / 2
    for i, rr in enumerate((150, 105, 60)):
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="{rr}" fill="{p}" fill-opacity="{0.06 + i * 0.05:.2f}" stroke="{p}" stroke-opacity=".25" stroke-dasharray="4 6"/>')
    parts.append(f'<path d="M{cx},{cy + 34} C{cx - 30},{cy} {cx - 26},{cy - 34} {cx},{cy - 36} C{cx + 26},{cy - 34} {cx + 30},{cy} {cx},{cy + 34}Z" fill="{p}"/>')
    parts.append(f'<circle cx="{cx}" cy="{cy - 10}" r="10" fill="{a}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def mesh(t: dict, seed: str) -> Markup:
    """Soft gradient-mesh background for dark heroes."""
    rng = _rng(seed + "mesh")
    p, a = t["primary"], t["accent"]
    blobs = "".join(f'<circle cx="{rng.randint(0, 1200)}" cy="{rng.randint(0, 700)}" r="{rng.randint(180, 360)}" fill="{c}" opacity="{o}"/>'
                    for c, o in ((p, .55), (a, .35), (p, .45), (a, .2)))
    return Markup(f'<svg class="mesh" viewBox="0 0 1200 700" preserveAspectRatio="xMidYMid slice" aria-hidden="true" focusable="false" '
                  f'xmlns="http://www.w3.org/2000/svg"><defs><filter id="b" x="-50%" y="-50%" width="200%" height="200%">'
                  f'<feGaussianBlur stdDeviation="90"/></filter></defs><g filter="url(#b)">{blobs}</g></svg>')


def pattern_url(kind: str, color: str, opacity: float = 0.14) -> str:
    c = color
    svgs = {
        "dots": f'<svg xmlns="http://www.w3.org/2000/svg" width="22" height="22"><circle cx="2" cy="2" r="1.6" fill="{c}" fill-opacity="{opacity}"/></svg>',
        "grid": f'<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><path d="M40 0H0V40" fill="none" stroke="{c}" stroke-opacity="{opacity * .8}"/></svg>',
        "waves": f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="24"><path d="M0 12 Q20 0 40 12 T80 12" fill="none" stroke="{c}" stroke-opacity="{opacity}" stroke-width="1.5"/></svg>',
        "rings": f'<svg xmlns="http://www.w3.org/2000/svg" width="60" height="60"><circle cx="30" cy="30" r="20" fill="none" stroke="{c}" stroke-opacity="{opacity * .8}"/></svg>',
    }
    if kind not in svgs:
        return "none"
    return f'url("data:image/svg+xml,{quote(svgs[kind])}")'
