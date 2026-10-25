"""Email notification adapter with an idempotent outbox."""
from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import config
from ..models import IntegrationConnection, Notification
from ..security import Vault, redact

log = logging.getLogger("siteforge.notify")


def queue(db: Session, *, tenant_id: str, project_id: str, to: str, subject: str, body: str, key: str) -> Notification:
    existing = db.scalar(select(Notification).where(Notification.idempotency_key == key))
    if existing:
        return existing  # retries never send twice
    n = Notification(tenant_id=tenant_id, project_id=project_id, to=to, subject=subject, body=body, idempotency_key=key)
    db.add(n)
    db.flush()
    deliver(db, n)
    return n


def deliver(db: Session, n: Notification) -> None:
    conn = db.scalar(select(IntegrationConnection).where(IntegrationConnection.project_id == n.project_id,
                                                         IntegrationConnection.provider == "smtp",
                                                         IntegrationConnection.status == "connected"))
    if conn and conn.secret_reference:
        try:
            password = Vault.reveal(db, n.tenant_id, conn.secret_reference)
            msg = EmailMessage()
            msg["From"] = conn.config.get("from_address")
            msg["To"] = n.to
            msg["Subject"] = n.subject
            msg.set_content(n.body)
            with smtplib.SMTP(conn.config["host"], int(conn.config.get("port", 587)), timeout=15) as s:
                s.starttls()
                s.login(conn.config["username"], password)
                s.send_message(msg)
            n.status = "sent"
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("SMTP delivery failed: %s", redact(str(exc)))
            n.status = "failed"
            return
    # Default: write to the outbox folder (visible in the dashboard); a real provider plugs in here.
    outbox = config.settings.data_dir / "outbox"
    outbox.mkdir(exist_ok=True)
    (outbox / f"{n.id}.txt").write_text(f"To: {n.to}\nSubject: {n.subject}\n\n{n.body}\n")
    n.status = "delivered_to_outbox"
