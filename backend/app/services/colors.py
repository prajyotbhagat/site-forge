"""Colour recommendations from several sources, all normalised to {primary, accent} and checked for contrast.

Sources
  industry  – palettes that suit the trade (from the playbook)
  logo      – dominant colours extracted from the owner's uploaded logo
  website   – colours (and fonts) found on a site the owner likes, fetched SSRF-safely
  harmony   – colour-theory schemes around a seed (complementary, analogous, triadic, split, monochrome)
  thecolorapi / colormind – external colour APIs (https://www.thecolorapi.com, http://colormind.io), called with
              short timeouts, cached, and switched off for a while after a failure; output is validated
  custom    – exact brand colours the owner types or picks

Every palette is passed through `balance()` so white text on the main colour and body text stay ≥ 4.5:1.
"""
from __future__ import annotations

import colorsys
import ipaddress
import json
import logging
import os
import re
import socket
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin, urlparse

import httpx

from ..agents.design import contrast

log = logging.getLogger("siteforge.colors")
HEX = re.compile(r"^#[0-9a-f]{6}$")
CUSTOM = re.compile(r"^custom:(#[0-9a-f]{6}):(#[0-9a-f]{6})$")


# ---------------------------------------------------------------- colour maths

def _rgb(h: str) -> tuple[float, float, float]:
    return tuple(int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def _hex(r: float, g: float, b: float) -> str:
    return "#" + "".join(f"{max(0, min(255, round(x * 255))):02x}" for x in (r, g, b))


def hsl(h: str) -> tuple[float, float, float]:
    r, g, b = _rgb(h)
    hh, ll, ss = colorsys.rgb_to_hls(r, g, b)
    return hh, ss, ll


def from_hsl(h: float, s: float, l: float) -> str:
    return _hex(*colorsys.hls_to_rgb(h % 1, max(0, min(1, l)), max(0, min(1, s))))


def normalize_hex(v: str) -> str | None:
    v = (v or "").strip().lower()
    if re.fullmatch(r"#?[0-9a-f]{3}", v):
        v = v.lstrip("#")
        v = "#" + "".join(c * 2 for c in v)
    if re.fullmatch(r"[0-9a-f]{6}", v):
        v = "#" + v
    return v if HEX.match(v) else None


def balance(primary: str, accent: str) -> dict:
    """Adjust lightness only (keeps the hue the owner chose) until text is readable."""
    h, s, l = hsl(primary)
    p = primary
    steps = 0
    while contrast("#ffffff", p) < 4.6 and steps < 40:
        l -= 0.02
        p = from_hsl(h, s, l)
        steps += 1
    ah, as_, al = hsl(accent)
    a = accent
    if contrast(a, "#ffffff") < 1.4:  # accent too pale to see on white
        a = from_hsl(ah, max(as_, 0.45), 0.62)
    on_accent = "#111111" if contrast("#111111", a) >= contrast("#ffffff", a) else "#ffffff"
    return {"primary": p, "accent": a, "on_accent": on_accent, "adjusted": p != primary or a != accent,
            "contrast": round(contrast("#ffffff", p), 2)}


def make(pid: str, name: str, primary: str, accent: str, source: str, why: str) -> dict:
    b = balance(primary, accent)
    return {"id": pid, "name": name, "primary": b["primary"], "accent": b["accent"], "on_accent": b["on_accent"],
            "source": source, "why": why + (" (lightness adjusted so text stays readable)" if b["adjusted"] else ""),
            "contrast": b["contrast"], "value": f"custom:{b['primary']}:{b['accent']}"}


def parse_value(value: str | None) -> tuple[str, str] | None:
    """Palette answer → (primary, accent). Accepts preset keys or 'custom:#pppppp:#aaaaaa'."""
    from ..onboarding.schema import PALETTES
    if not value:
        return None
    if value in PALETTES:
        p = PALETTES[value][1]
        return p["primary"], p["accent"]
    m = CUSTOM.match(value)
    return (m.group(1), m.group(2)) if m else None


# ---------------------------------------------------------------- harmony

def harmonies(seed: str) -> list[dict]:
    h, s, l = hsl(seed)
    s = max(s, 0.35)
    base_l = min(max(l, 0.25), 0.42)
    primary = from_hsl(h, s, base_l)
    accent_l = 0.58
    out = [
        make("h-comp", "Complementary", primary, from_hsl(h + 0.5, max(s, 0.55), accent_l), "harmony",
             "Opposite colour on the wheel: maximum energy for buttons and highlights."),
        make("h-split", "Split complementary", primary, from_hsl(h + 0.42, max(s, 0.5), accent_l), "harmony",
             "A softer contrast than the direct opposite — lively but balanced."),
        make("h-analog", "Analogous", primary, from_hsl(h + 0.09, max(s, 0.5), accent_l), "harmony",
             "Neighbouring colours: calm and cohesive."),
        make("h-triad", "Triadic", primary, from_hsl(h + 1 / 3, max(s, 0.5), accent_l), "harmony",
             "Three-way balance: playful without clashing."),
        make("h-mono", "Monochrome", primary, from_hsl(h, max(s - 0.1, 0.3), 0.7), "harmony",
             "One hue in two strengths: understated and elegant."),
    ]
    return out


# ---------------------------------------------------------------- industry

INDUSTRY: dict[str, list[tuple[str, str, str, str]]] = {
    "plumbing": [("Trust blue & safety orange", "#1d4f91", "#f08a24", "Blue reads as clean water and reliability; orange makes the call button impossible to miss."),
                 ("Deep teal & sunshine", "#0f5c63", "#f2b134", "Fresh and dependable, with a warm, friendly highlight.")],
    "electrical": [("Charcoal & electric yellow", "#23272f", "#f5c518", "Classic trade colours: strong, safe and highly visible."),
                   ("Navy & spark orange", "#1b2f55", "#ff7a1a", "Professional with an energetic highlight.")],
    "cleaning": [("Aqua & lime", "#0f6e7a", "#9bd34b", "Feels fresh and hygienic.")],
    "salon": [("Plum & rose gold", "#5b2a4e", "#d9a38b", "Stylish and calm."), ("Black & blush", "#1f1a1d", "#e8a8b5", "Modern and chic.")],
    "fitness": [("Charcoal & lime", "#1f2326", "#b5e61d", "High energy and bold.")],
    "food": [("Tomato & cream", "#b23a2c", "#f2c14e", "Appetising and warm."), ("Olive & saffron", "#4f5d2f", "#e3a72f", "Earthy and homely.")],
    "tutoring": [("Scholar blue & sunflower", "#2849a3", "#ffc93c", "Confident and encouraging.")],
    "accounting": [("Navy & brass", "#1f3a5f", "#b08d57", "Signals precision and trust — the classic finance pairing."),
                   ("Forest & gold", "#20453b", "#c8a24a", "Steady and prosperous.")],
    "legal": [("Oxford green & brass", "#1e3d33", "#b5915a", "Authoritative and discreet."), ("Burgundy & stone", "#6b1f2a", "#c9b79c", "Traditional and serious.")],
    "health": [("Clinical teal & mint", "#0d6b6b", "#7fd1b9", "Calm, clean and caring."), ("Soft blue & coral", "#2a5c9a", "#f08a7e", "Reassuring and warm.")],
    "architecture": [("Charcoal & terracotta", "#2b2b2b", "#c2673f", "Refined with a material, crafted feel.")],
    "consulting": [("Ink & signal teal", "#1c2541", "#2bb3a3", "Clear, modern and credible.")],
    "startup": [("Indigo & mint", "#3b3f9a", "#2bb39a", "Modern product feel."), ("Midnight & electric violet", "#141a3a", "#8b5cf6", "Bold and techy.")],
    "portfolio": [("Black & coral", "#151515", "#ff6b4a", "Minimal with a confident pop.")],
}


def industry(playbook_id: str | None) -> list[dict]:
    rows = INDUSTRY.get(playbook_id or "", [])
    return [make(f"ind-{playbook_id}-{i}", n, p, a, "industry", why) for i, (n, p, a, why) in enumerate(rows)]


# ---------------------------------------------------------------- logo

def from_image(path: Path) -> list[dict]:
    """Dominant, usable brand colours from a logo (ignores transparent, near-white, near-black and grey pixels)."""
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover
        return []
    try:
        im = Image.open(path).convert("RGBA")
    except Exception:  # noqa: BLE001 - corrupt or unsupported image
        return []
    im.thumbnail((160, 160))
    px = [(r, g, b) for r, g, b, a in im.getdata() if a > 200]
    if not px:
        return []
    # bucket to reduce noise
    buckets = Counter((r // 16 * 16 + 8, g // 16 * 16 + 8, b // 16 * 16 + 8) for r, g, b in px)
    cands = []
    for (r, g, b), n in buckets.most_common(40):
        hx = _hex(r / 255, g / 255, b / 255)
        h, s, l = hsl(hx)
        if l > 0.93 or l < 0.06 or s < 0.18:
            continue
        cands.append((hx, n, h, s, l))
    if not cands:
        # a black/grey logo: use it as a neutral primary with a warm accent
        dark = _hex(*[x / 255 for x in buckets.most_common(1)[0][0]])
        return [make("logo-0", "From your logo (neutral)", dark if hsl(dark)[2] < 0.5 else "#26292e", "#e8642c", "logo",
                     "Your logo is mostly neutral, so it's paired with a warm highlight.")]
    primary = max(cands[:6], key=lambda c: c[1] * (1 - abs(c[4] - 0.4)))  # frequent and mid-dark
    others = [c for c in cands if min(abs(c[2] - primary[2]), 1 - abs(c[2] - primary[2])) > 0.08]
    out = []
    if others:
        accent = max(others[:6], key=lambda c: c[1])
        out.append(make("logo-0", "From your logo", primary[0], accent[0], "logo", "The two main colours in your logo."))
    out.append(make("logo-1", "Logo colour + complementary", primary[0], from_hsl(primary[2] + 0.5, 0.6, 0.58), "logo",
                    "Your main logo colour with its opposite on the colour wheel for buttons."))
    return out


# ---------------------------------------------------------------- external colour APIs

class _Breaker:
    """Skip a provider for a while after it fails, so a slow or blocked API never slows the product down."""

    def __init__(self, cooldown: float = 600) -> None:
        self.cooldown = cooldown
        self.until: dict[str, float] = {}
        self.lock = threading.Lock()

    def open(self, name: str) -> bool:
        with self.lock:
            return time.monotonic() < self.until.get(name, 0)

    def trip(self, name: str) -> None:
        with self.lock:
            self.until[name] = time.monotonic() + self.cooldown

    def reset(self) -> None:
        with self.lock:
            self.until.clear()


breaker = _Breaker()
_cache: dict[str, list[dict]] = {}
Transport = Callable[..., httpx.Response]


def enabled_apis() -> list[str]:
    raw = os.getenv("SITEFORGE_COLOR_APIS", "thecolorapi,colormind")
    return [x.strip() for x in raw.split(",") if x.strip()]


def _client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(3.0, connect=2.0), transport=transport, follow_redirects=False,
                        headers={"user-agent": "SiteForge/0.2 (+colour recommendations)"})


def thecolorapi(seed: str, transport: httpx.BaseTransport | None = None) -> list[dict]:
    out = []
    with _client(transport) as c:
        for mode, label in (("analogic-complement", "Analogic complement"), ("triad", "Triad"), ("complement", "Complement")):
            r = c.get("https://www.thecolorapi.com/scheme", params={"hex": seed.lstrip("#"), "mode": mode, "count": 4, "format": "json"})
            r.raise_for_status()
            hexes = [normalize_hex(x.get("hex", {}).get("value", "")) for x in r.json().get("colors", [])]
            hexes = [x for x in hexes if x]
            if len(hexes) >= 2:
                acc = max(hexes, key=lambda x: hsl(x)[1] * (1 - abs(hsl(x)[2] - 0.55)))
                out.append(make(f"tca-{mode}", f"The Color API · {label}", seed, acc, "thecolorapi",
                                f"'{mode}' scheme generated by The Color API from your main colour."))
    return out


def colormind(seed: str, transport: httpx.BaseTransport | None = None) -> list[dict]:
    r, g, b = (round(x * 255) for x in _rgb(seed))
    with _client(transport) as c:
        resp = c.post("http://colormind.io/api/", content=json.dumps({"model": "default", "input": [[r, g, b], "N", "N", "N", "N"]}))
        resp.raise_for_status()
        data = resp.json()
    result = data.get("result") if isinstance(data, dict) else data
    hexes = []
    for t in (result or [])[:5]:
        if isinstance(t, list) and len(t) == 3 and all(isinstance(v, int) and 0 <= v <= 255 for v in t):
            hexes.append(_hex(*(v / 255 for v in t)))
    if len(hexes) < 3:
        raise ValueError("unexpected Colormind response")
    acc = max(hexes[1:], key=lambda x: hsl(x)[1] * (1 - abs(hsl(x)[2] - 0.55)))
    return [make("cm-0", "Colormind suggestion", seed, acc, "colormind", "Palette suggested by Colormind's model from your main colour.")]


PROVIDERS: dict[str, Callable[..., list[dict]]] = {"thecolorapi": thecolorapi, "colormind": colormind}


def external(seed: str, transport: httpx.BaseTransport | None = None) -> tuple[list[dict], list[str]]:
    """Call enabled colour APIs. Returns (palettes, status notes). Never raises."""
    out, notes = [], []
    for name in enabled_apis():
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        key = f"{name}:{seed}"
        if key in _cache:
            out += _cache[key]
            continue
        if breaker.open(name):
            notes.append(f"{name}: paused after a recent failure")
            continue
        try:
            res = fn(seed, transport)
            _cache[key] = res
            out += res
        except Exception as exc:  # noqa: BLE001 - external services must never break the product
            breaker.trip(name)
            notes.append(f"{name}: unavailable ({type(exc).__name__})")
            log.info("colour API %s unavailable: %s", name, exc)
    return out, notes


# ---------------------------------------------------------------- reference website (SSRF-safe)

class UnsafeURL(ValueError):
    pass


def _check_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURL("That address couldn't be found.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise UnsafeURL("That address points to a private network, which isn't allowed.")


def validate_url(url: str) -> str:
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if u.scheme not in {"http", "https"} or not u.hostname or u.username or u.password:
        raise UnsafeURL("Please enter a normal website address like example.com.")
    if u.port not in (None, 80, 443):
        raise UnsafeURL("Only standard web ports are allowed.")
    return url


def safe_get(url: str, max_bytes: int = 1_500_000, transport: httpx.BaseTransport | None = None,
             check_host: Callable[[str], None] = _check_host) -> tuple[str, str]:
    """GET with redirect re-validation, private-IP blocking, size and time limits. Returns (final_url, text)."""
    url = validate_url(url)
    with _client(transport) as c:
        for _ in range(4):
            check_host(urlparse(url).hostname or "")
            with c.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = validate_url(urljoin(url, r.headers["location"]))
                    continue
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if not any(t in ctype for t in ("text/html", "text/css", "text/plain")):
                    raise UnsafeURL("That address isn't a web page.")
                buf = b""
                for chunk in r.iter_bytes():
                    buf += chunk
                    if len(buf) > max_bytes:
                        break
                return url, buf[:max_bytes].decode(r.encoding or "utf-8", errors="replace")
    raise UnsafeURL("Too many redirects.")


FONT_MAP = [
    (r"playfair|dm serif|bodoni|didot|cormorant|abril", "elegant", "dm-serif-display"),
    (r"fraunces|recoleta|cooper", "warm", "fraunces"),
    (r"merriweather|lora|garamond|georgia|times|source serif|libre baskerville|crimson|pt serif|noto serif|serif", "classic", "source-serif-4"),
    (r"space grotesk|ibm plex|jetbrains|roboto mono|mono", "tech", "space-grotesk"),
    (r"nunito|quicksand|varela|comfortaa|baloo|fredoka", "friendly", "nunito"),
    (r"poppins|montserrat|outfit|futura|gilroy|circular|sora|lexend|raleway", "bold_local", "outfit"),
    (r"plus jakarta|dm sans|manrope|satoshi|general sans", "fresh", "plus-jakarta-sans"),
    (r"inter|helvetica|arial|system-ui|segoe|roboto|open sans|lato|sans-serif", "studio", "inter"),
]


def analyse_site(url: str, fetch: Callable[[str], tuple[str, str]] | None = None) -> dict:
    """Borrow colours and type style from a site the owner likes. Content is never copied."""
    fetch = fetch or safe_get
    final, html = fetch(url)
    css_texts = re.findall(r"<style[^>]*>(.*?)</style>", html, re.S | re.I)
    styles = re.findall(r"""<link[^>]+rel=["']?stylesheet["']?[^>]*href=["']([^"']+)["']""", html, re.I)
    styles += re.findall(r"""<link[^>]+href=["']([^"']+\.css[^"']*)["'][^>]*rel=["']?stylesheet""", html, re.I)
    host = urlparse(final).hostname
    fetched = 0
    for href in dict.fromkeys(styles):
        if fetched >= 3:
            break
        full = urljoin(final, href)
        if urlparse(full).hostname != host:
            continue  # only the site's own CSS
        try:
            css_texts.append(fetch(full)[1])
            fetched += 1
        except Exception:  # noqa: BLE001
            continue
    css = "\n".join(css_texts)
    inline_styles = " ".join(re.findall(r'style="([^"]+)"', html))
    blob = css + "\n" + inline_styles
    theme_color = re.search(r"""<meta[^>]+name=["']theme-color["'][^>]+content=["']([^"']+)""", html, re.I)
    named: dict[str, str] = {}
    for var, val in re.findall(r"--([\w-]*(?:primary|brand|accent|main|secondary)[\w-]*)\s*:\s*(#[0-9a-fA-F]{3,6})\b", blob):
        v = normalize_hex(val)
        if v:
            named.setdefault(var.lower(), v)
    counts: Counter = Counter()
    for m in re.findall(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b", blob):
        v = normalize_hex(m)
        if v:
            counts[v] += 1
    for r, g, b in re.findall(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", blob):
        if max(int(r), int(g), int(b)) <= 255:
            counts[_hex(int(r) / 255, int(g) / 255, int(b) / 255)] += 1
    colourful = [(c, n) for c, n in counts.most_common(60) if hsl(c)[1] > 0.22 and 0.12 < hsl(c)[2] < 0.85]
    primary = None
    why = ""
    for key in ("primary", "brand", "main"):
        hit = next((v for k, v in named.items() if key in k and hsl(v)[1] > 0.12), None)
        if hit:
            primary, why = hit, f"the site's '--{key}' colour variable"
            break
    if not primary and theme_color and normalize_hex(theme_color.group(1)):
        primary, why = normalize_hex(theme_color.group(1)), "the site's theme colour"
    if not primary and colourful:
        primary, why = colourful[0][0], "the most-used colour in its styles"
    accent = next((v for k, v in named.items() if ("accent" in k or "secondary" in k) and v != primary), None)
    if not accent and primary:
        ph = hsl(primary)[0]
        accent = next((c for c, _ in colourful if min(abs(hsl(c)[0] - ph), 1 - abs(hsl(c)[0] - ph)) > 0.08), None)
    fonts = []
    for fam in re.findall(r"font-family\s*:\s*([^;}{]+)", blob, re.I):
        first = fam.split(",")[0].strip().strip("'\"").lower()
        if first and not first.startswith(("var(", "inherit", "-apple", "icon", "fontawesome")):
            fonts.append(first)
    font_counts = Counter(fonts)
    template = None
    font_note = None
    for fam, _ in font_counts.most_common(5):
        for pat, theme_id, ours in FONT_MAP:
            if re.search(pat, fam):
                template, font_note = theme_id, f"Their headings/body use “{fam}”; closest style here uses {ours.replace('-', ' ')}."
                break
        if template:
            break
    palettes = []
    if primary:
        palettes.append(make("web-0", f"From {host}", primary, accent or from_hsl(hsl(primary)[0] + 0.5, 0.6, 0.58), "website",
                             f"Based on {why}."))
        if accent:
            palettes.append(make("web-1", f"From {host} (swapped)", accent, primary, "website",
                                 "The same two colours with the roles swapped."))
    return {"url": final, "host": host, "palettes": palettes, "template": template, "font_note": font_note,
            "colours_found": [c for c, _ in colourful[:8]], "fonts_found": [f for f, _ in font_counts.most_common(4)]}


# ---------------------------------------------------------------- aggregation

def dedupe(palettes: list[dict]) -> list[dict]:
    out: list[dict] = []
    for p in palettes:
        if any(_close(p["primary"], q["primary"]) and _close(p["accent"], q["accent"]) for q in out):
            continue
        out.append(p)
    return out


def _close(a: str, b: str) -> bool:
    return sum(abs(x - y) for x, y in zip(_rgb(a), _rgb(b))) < 0.08
