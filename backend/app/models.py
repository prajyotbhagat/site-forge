"""Data model (roadmap section 8). Every tenant-owned row carries tenant_id for server-side scoping."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; treat naive datetimes as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class TS:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Tenant(Base, TS):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(40), default="pilot")


class User(Base, TS):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    is_operator: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(Base, TS):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # owner|admin|editor|reviewer|viewer
    status: Mapped[str] = mapped_column(String(20), default="active")
    invited_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AuthSession(Base, TS):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256 of the cookie token
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf_token: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reauth_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Project(Base, TS):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    business_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lifecycle_state: Mapped[str] = mapped_column(String(40), default="draft")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    selected_stack: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_release_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    transcript: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OnboardingAnswer(Base, TS):
    """Versioned answers with provenance. Only one row per (project, field) has is_current=True."""
    __tablename__ = "onboarding_answers"
    __table_args__ = (Index("ix_answer_current", "project_id", "field_key", "is_current"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    field_key: Mapped[str] = mapped_column(String(80))
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    # user_provided | extracted | inferred | recommended | skipped
    source: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(64), nullable=True)  # user id or agent name
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class Requirement(Base, TS):
    __tablename__ = "requirements"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40))
    priority: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(20))  # in_scope | deferred | out_of_scope
    dependency: Mapped[list] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[list] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(10), default="low")
    data_collected: Mapped[list] = mapped_column(JSON, default=list)
    blueprint_version: Mapped[int] = mapped_column(Integer, default=0)


class Blueprint(Base, TS):
    __tablename__ = "blueprints"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft | approved | superseded


class Secret(Base, TS):
    """Vault rows: ciphertext only. Agents and logs see only the opaque reference (id)."""
    __tablename__ = "vault_secrets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    ciphertext: Mapped[str] = mapped_column(Text)


class IntegrationConnection(Base, TS):
    __tablename__ = "integration_connections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40))
    external_account_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    granted_scopes: Mapped[list] = mapped_column(JSON, default=list)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentRun(Base, TS):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    agent_type: Mapped[str] = mapped_column(String(40))
    input_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    provider: Mapped[str] = mapped_column(String(40))
    output: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_category: Mapped[str | None] = mapped_column(String(60), nullable=True)


class Build(Base, TS):
    __tablename__ = "builds"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    blueprint_id: Mapped[str] = mapped_column(ForeignKey("blueprints.id"))
    spec_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    branch: Mapped[str] = mapped_column(String(200))
    commit_sha: Mapped[str] = mapped_column(String(64))
    artifact_path: Mapped[str] = mapped_column(Text)
    checksum: Mapped[str] = mapped_column(String(64))
    site_config: Mapped[dict] = mapped_column(JSON, default=dict)
    diff: Mapped[dict] = mapped_column(JSON, default=dict)
    test_report: Mapped[dict] = mapped_column(JSON, default=dict)
    security_report: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20))  # passed | blocked | failed


class Approval(Base, TS):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(40))  # scope | release
    resource_id: Mapped[str] = mapped_column(String(32))
    diff_hash: Mapped[str] = mapped_column(String(64))
    approver_id: Mapped[str] = mapped_column(String(32))
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Deployment(Base, TS):
    __tablename__ = "deployments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[str] = mapped_column(ForeignKey("builds.id"))
    environment: Mapped[str] = mapped_column(String(20), default="production")
    release_id: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20))  # deploying|live|superseded|failed|rolled_back
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    rollback_target: Mapped[str | None] = mapped_column(String(32), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deployed_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Lead(Base, TS):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    fields: Mapped[dict] = mapped_column(JSON)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False)
    source_release: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delete_after: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Notification(Base, TS):
    """Outbox for email notifications; idempotency_key prevents duplicate sends on retry."""
    __tablename__ = "notifications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    to: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")


class SiteEvent(Base):
    """Aggregated, cookie-free analytics counters (no personal data)."""
    __tablename__ = "site_events"
    __table_args__ = (UniqueConstraint("project_id", "day", "event"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    day: Mapped[str] = mapped_column(String(10))
    event: Mapped[str] = mapped_column(String(40))
    count: Mapped[int] = mapped_column(Integer, default=0)


class AuditEvent(Base, TS):
    __tablename__ = "audit_events"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    actor: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    resource: Mapped[str | None] = mapped_column(String(120), nullable=True)
    outcome: Mapped[str] = mapped_column(String(20))
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class KnowledgeEntry(Base, TS):
    """Shared team memory. Every agent reads the board before acting and writes what it learned or decided,
    with its reasoning, so later agents build on earlier ones instead of starting from scratch."""
    __tablename__ = "knowledge_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    agent: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(20))  # fact | insight | decision | question | critique | change
    key: Mapped[str] = mapped_column(String(80))
    content: Mapped[Any] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    based_on: Mapped[list] = mapped_column(JSON, default=list)  # keys of entries this one used
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)


class SiteSpec(Base, TS):
    """Versioned page/section/theme specification. Agents, the visual critic and owner change requests
    each produce a new version; builds render a specific version."""
    __tablename__ = "site_specs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    blueprint_id: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(Integer)
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    spec: Mapped[dict] = mapped_column(JSON)
    author: Mapped[str] = mapped_column(String(64))  # agent name or user id
    reason: Mapped[str] = mapped_column(Text, default="")
    changes: Mapped[list] = mapped_column(JSON, default=list)
