"""Project lifecycle state machine (roadmap section 8)."""
from __future__ import annotations

from .models import Project

STATES = [
    "draft", "onboarding", "brief_ready", "scope_approved", "building", "preview_ready",
    "changes_requested", "approved_for_release", "deploying", "live", "monitoring",
    "paused", "failed", "archived",
]

LABELS = {
    "draft": "Draft",
    "onboarding": "Onboarding in progress",
    "brief_ready": "Brief ready",
    "scope_approved": "Scope approved",
    "building": "Building",
    "preview_ready": "Preview ready",
    "changes_requested": "Changes requested",
    "approved_for_release": "Approved for release",
    "deploying": "Deploying",
    "live": "Live",
    "monitoring": "Live · monitoring",
    "paused": "Paused",
    "failed": "Failed",
    "archived": "Archived",
}

_T: dict[str, set[str]] = {
    "draft": {"onboarding"},
    "onboarding": {"brief_ready"},
    "brief_ready": {"onboarding", "scope_approved"},
    "scope_approved": {"onboarding", "building"},
    "building": {"preview_ready", "failed"},
    "preview_ready": {"changes_requested", "approved_for_release", "building", "onboarding"},
    "changes_requested": {"onboarding", "building"},
    "approved_for_release": {"deploying", "building", "onboarding"},
    "deploying": {"live", "failed", "monitoring"},
    "live": {"monitoring", "onboarding", "building", "deploying"},
    "monitoring": {"onboarding", "building", "deploying"},
    "failed": {"building", "onboarding", "deploying"},
    "paused": {"onboarding", "building"},
    "archived": set(),
}
for _s in STATES:
    if _s != "archived":
        _T[_s] |= {"paused", "archived"}


class InvalidTransition(Exception):
    pass


def can_transition(src: str, dst: str) -> bool:
    return src == dst or dst in _T.get(src, set())


def transition(project: Project, dst: str) -> None:
    if not can_transition(project.lifecycle_state, dst):
        raise InvalidTransition(f"cannot move project from {project.lifecycle_state} to {dst}")
    project.lifecycle_state = dst


def is_live(project: Project) -> bool:
    return project.current_release_id is not None
