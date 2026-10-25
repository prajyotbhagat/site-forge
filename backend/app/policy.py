"""Deterministic policy engine (roadmap section 9, autonomy and approval policy).

Agents may *propose* actions; this module alone decides whether an action is permitted and whether
human approval is required. LLM output never changes these rules.
"""
from __future__ import annotations

from dataclasses import dataclass, field

ROLES = ["owner", "admin", "editor", "reviewer", "viewer"]

PERMISSIONS: dict[str, set[str]] = {
    "project.read": {"owner", "admin", "editor", "reviewer", "viewer"},
    "project.create": {"owner", "admin", "editor"},
    "onboarding.write": {"owner", "admin", "editor"},
    "build.create": {"owner", "admin", "editor"},
    "blueprint.approve": {"owner", "admin", "reviewer"},
    "release.approve": {"owner", "admin"},
    "deploy": {"owner", "admin"},
    "leads.read": {"owner", "admin"},
    "members.manage": {"owner", "admin"},
    "integrations.manage": {"owner", "admin"},
    "project.delete": {"owner"},
    "audit.read": {"owner", "admin", "reviewer"},
}

# Tier per action. "prohibited" actions have no API in this product and are always refused.
ACTION_TIERS: dict[str, str] = {
    "draft_copy": "low",
    "generate_preview": "low",
    "run_qa": "low",
    "run_seo_audit": "low",
    "create_branch": "low",
    "approve_scope": "medium",
    "edit_public_copy": "medium",
    "add_integration": "medium",
    "deploy_production": "high",
    "rollback_production": "high",
    "change_retention": "high",
    "change_auth": "high",
    "change_dns": "high",
    "delete_project": "high",
    "spend_money": "prohibited",
    "buy_domain": "prohibited",
    "send_bulk_marketing": "prohibited",
    "expose_secret": "prohibited",
    "delete_production_data": "prohibited",
    "expand_access_scopes": "prohibited",
}


@dataclass
class PolicyDecision:
    action: str
    tier: str
    allowed: bool
    requires_approval: bool
    requires_reauth: bool
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def has_permission(role: str | None, permission: str) -> bool:
    return role is not None and role in PERMISSIONS.get(permission, set())


def evaluate(action: str, *, role: str | None, context: dict | None = None) -> PolicyDecision:
    ctx = context or {}
    tier = ACTION_TIERS.get(action, "prohibited")  # unknown actions are denied by default
    reasons: list[str] = []
    if tier == "prohibited":
        return PolicyDecision(action, tier, False, True, True,
                              ["This action is never taken automatically. It needs a separate, explicit, auditable request."])
    allowed = True
    requires_approval = tier in {"medium", "high"}
    requires_reauth = tier == "high"

    if action in {"deploy_production", "rollback_production"}:
        if not has_permission(role, "deploy"):
            allowed = False
            reasons.append("Only an Owner or Admin can publish or roll back.")
        if action == "deploy_production":
            if not ctx.get("gates_passed"):
                allowed = False
                reasons.append("Release gates (QA + security) have not all passed for this build.")
            if not ctx.get("approval_valid"):
                allowed = False
                reasons.append("No valid release approval matches this exact build checksum.")
            if not ctx.get("artifact_intact"):
                allowed = False
                reasons.append("Build artifact does not match the approved checksum.")
    elif action == "approve_scope" and not has_permission(role, "blueprint.approve"):
        allowed = False
        reasons.append("Your role cannot approve scope.")
    elif action in {"generate_preview", "run_qa", "create_branch", "draft_copy"} and not has_permission(role, "build.create"):
        allowed = False
        reasons.append("Your role cannot start builds.")
    elif action == "add_integration" and not has_permission(role, "integrations.manage"):
        allowed = False
        reasons.append("Your role cannot manage integrations.")
    elif action == "delete_project" and not has_permission(role, "project.delete"):
        allowed = False
        reasons.append("Only the Owner can delete a project.")
    return PolicyDecision(action, tier, allowed, requires_approval, requires_reauth, reasons)
