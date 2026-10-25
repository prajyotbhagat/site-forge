"""Security & Privacy agent: a required reviewer and deterministic policy gate.

Plan review runs before implementation; artifact review runs on every build before release.
These checks are code, not model opinions.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from ..onboarding.schema import SENSITIVE_HINTS
from ..security import find_secrets
from .contracts import AgentResult, Risk

ALLOWED_FORM_ACTION = re.compile(r"^/api/public/sites/[a-z0-9-]+/leads$")


def review_plan(a: dict[str, Any], regulated: str | None, lead_form: dict | None) -> AgentResult:
    risks: list[Risk] = []
    controls = [
        "HTTPS everywhere; strict security headers and a Content Security Policy on every page",
        "No third-party scripts, fonts or trackers",
        "Preview builds are private and marked noindex",
        "Publishing requires your approval of the exact build, plus password confirmation",
        "Automatic health check after publishing, with rollback",
    ]
    data_classes: dict[str, str] = {}
    if lead_form:
        controls += [
            "Server-side validation of every form field",
            "Spam controls: hidden honeypot field, minimum fill time, per-visitor rate limit",
            f"Enquiries deleted automatically after {lead_form['retention_days']} days",
            "Only owners and admins can read enquiries; every access is audited",
            "Your notification email is never published in the page source",
        ]
        data_classes = {f["key"]: f["classification"] for f in lead_form["fields"]}
    feats = a.get("features") or []
    for f in ("accounts", "payments"):
        if f in feats:
            risks.append(Risk(id=f"deferred-{f}", severity="high", title=f"{f.title()} need a dedicated security review",
                              detail="Not included in this release.",
                              mitigation="Recorded in your plan for a later release with its own review."))
    if regulated:
        risks.append(Risk(id="regulated", severity="medium", title=f"Regulated profession ({regulated})",
                          detail="Advertising rules may apply to claims about qualifications, results or fees.",
                          mitigation="We publish only facts you supply; please review copy before approving."))
    text = " ".join(str(a.get(k) or "") for k in ("business_description", "services", "differentiators"))
    if SENSITIVE_HINTS.search(text):
        risks.append(Risk(id="sensitive-data", severity="high", title="Possible sensitive personal data",
                          detail="Your description mentions categories of sensitive data.",
                          mitigation="The form will not collect these. Sensitive collection needs human review.",
                          blocking=False))
    score = sum({"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 5}[r.severity] for r in risks)
    return AgentResult(
        agent="security_privacy",
        summary=f"Plan reviewed: {len(controls)} controls required, {len(risks)} risk(s) noted.",
        artifacts={"required_controls": controls, "data_classification": data_classes,
                   "privacy_notice_required": bool(lead_form), "risk_score": score},
        risks=risks,
        status="blocked" if any(r.blocking for r in risks) else "ok",
        tests_required=["security:secret-scan", "security:external-resources", "security:form-endpoint",
                        "security:tenant-isolation"],
        risk_tier="medium" if lead_form else "low",
    )


class _Scan(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.findings: list[tuple[str, str, str]] = []  # severity, id, detail
        self.has_csp = False
        self.inline_scripts = 0

    def handle_starttag(self, tag, attrs):
        at = {k: (v or "") for k, v in attrs}
        for k, v in at.items():
            if k.startswith("on"):
                self.findings.append(("high", "inline-handler", f"<{tag} {k}=…> inline event handler"))
            if k in {"src", "href", "action"} and v.lower().startswith("javascript:"):
                self.findings.append(("critical", "javascript-url", f"<{tag} {k}='javascript:…'>"))
        if tag == "meta" and at.get("http-equiv", "").lower() == "content-security-policy":
            self.has_csp = True
        if tag == "script":
            src = at.get("src", "")
            if at.get("type") == "application/ld+json":
                pass  # structured data is not executed and is allowed by CSP
            elif not src:
                self.inline_scripts += 1
            elif re.match(r"^(https?:)?//", src):
                self.findings.append(("high", "third-party-script", f"external script {src}"))
        if tag in {"iframe", "embed", "object"}:
            self.findings.append(("high", "embedded-content", f"<{tag}> is not allowed in templates"))
        if tag == "link" and "stylesheet" in at.get("rel", "") and re.match(r"^(https?:)?//", at.get("href", "")):
            self.findings.append(("medium", "third-party-style", f"external stylesheet {at['href']}"))
        if tag == "img" and re.match(r"^http://", at.get("src", "")):
            self.findings.append(("medium", "mixed-content", f"insecure image {at['src']}"))
        if tag == "form":
            action, method = at.get("action", ""), at.get("method", "get").lower()
            if not ALLOWED_FORM_ACTION.match(action):
                self.findings.append(("critical", "form-endpoint", f"form posts to non-allowlisted endpoint {action!r}"))
            if method != "post":
                self.findings.append(("high", "form-method", "form must use POST"))
        if tag == "a" and at.get("target") == "_blank" and "noopener" not in at.get("rel", ""):
            self.findings.append(("low", "noopener", f"link to {at.get('href')} opens without rel=noopener"))


def review_artifact(build_dir: Path, private_values: list[str]) -> dict:
    """Pre-release security scan of the generated site. Critical/high findings block release."""
    findings: list[dict] = []
    files = sorted(p for p in build_dir.rglob("*") if p.is_file())
    for f in files:
        rel = str(f.relative_to(build_dir))
        if f.suffix in {".html", ".js", ".css", ".txt", ".xml", ".json"}:
            text = f.read_text(errors="replace")
            for hit in find_secrets(text):
                findings.append({"severity": "critical", "id": "secret-in-artifact", "file": rel, "detail": hit})
            for pv in private_values:
                if pv and pv.lower() in text.lower():
                    findings.append({"severity": "high", "id": "private-value-exposed", "file": rel,
                                     "detail": "A value that must stay server-side (e.g. notification email) appears in the page."})
            if f.suffix == ".html":
                scan = _Scan()
                scan.feed(text)
                for sev, fid, detail in scan.findings:
                    findings.append({"severity": sev, "id": fid, "file": rel, "detail": detail})
                if not scan.has_csp:
                    findings.append({"severity": "high", "id": "missing-csp", "file": rel, "detail": "No Content Security Policy"})
                if scan.inline_scripts:
                    findings.append({"severity": "medium", "id": "inline-script", "file": rel,
                                     "detail": f"{scan.inline_scripts} inline script(s); CSP blocks them"})
        elif f.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff2"}:
            findings.append({"severity": "high", "id": "unexpected-file", "file": rel, "detail": "File type not allowed in site output"})
    blocking = [f for f in findings if f["severity"] in {"critical", "high"}]
    return {"passed": not blocking, "files_scanned": len(files), "findings": findings,
            "blocking": len(blocking), "checks": ["secret-scan", "private-value-exposure", "third-party-resources",
                                                  "form-endpoint-allowlist", "inline-handlers", "csp-present",
                                                  "file-type-allowlist"]}
