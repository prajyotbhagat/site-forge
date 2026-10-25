"""Typed agent contract (roadmap section 5, "Agent contract: structured handoffs").

Every agent returns an AgentResult. The orchestrator validates it with pydantic and rejects malformed
or out-of-scope proposals before anything touches canonical project state.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["extracted", "inferred", "recommended"]
Severity = Literal["info", "low", "medium", "high", "critical"]


class ProposedChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field_key: str
    value: Any
    source: Source = "inferred"
    confidence: float = Field(ge=0, le=1)
    reason: str = ""


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    severity: Severity
    title: str
    detail: str = ""
    mitigation: str = ""
    blocking: bool = False


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    status: Literal["ok", "needs_input", "blocked", "error"] = "ok"
    summary: str
    proposed_changes: list[ProposedChange] = []
    evidence: list[str] = []
    confidence: float = Field(default=1.0, ge=0, le=1)
    missing_information: list[str] = []
    risks: list[Risk] = []
    dependencies: list[str] = []
    user_question: str | None = None
    artifacts: dict[str, Any] = {}
    tests_required: list[str] = []
    requires_human_approval: bool = False
    risk_tier: Literal["low", "medium", "high"] = "low"
