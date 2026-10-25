"""Audit trail. Details are redacted before they are stored."""
from __future__ import annotations

import contextvars
import logging

from sqlalchemy.orm import Session

from .models import AuditEvent
from .security import redact

log = logging.getLogger("siteforge.audit")
correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("correlation_id", default=None)


def record(
    db: Session,
    *,
    actor: str,
    action: str,
    outcome: str = "success",
    tenant_id: str | None = None,
    project_id: str | None = None,
    resource: str | None = None,
    details: dict | None = None,
) -> AuditEvent:
    ev = AuditEvent(
        tenant_id=tenant_id,
        project_id=project_id,
        actor=actor,
        action=action,
        resource=resource,
        outcome=outcome,
        correlation_id=correlation_id.get(),
        details=redact(details or {}),
    )
    db.add(ev)
    log.info("audit %s %s %s %s", actor, action, resource, outcome)
    return ev
