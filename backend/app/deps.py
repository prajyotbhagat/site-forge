"""Request dependencies: authentication, CSRF, tenant-scoped project access."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import policy
from .db import get_db
from .models import AuthSession, Membership, Project, User, as_utc, utcnow
from .security import token_hash, verify_password

SESSION_COOKIE = "sf_session"
CSRF_HEADER = "x-csrf-token"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Principal:
    user: User
    session: AuthSession


def load_session(db: Session, request: Request) -> AuthSession | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    sess = db.get(AuthSession, token_hash(token))
    if sess is None or as_utc(sess.expires_at) < utcnow():
        return None
    return sess


def current_principal(request: Request, db: Session = Depends(get_db)) -> Principal:
    sess = load_session(db, request)
    if sess is None:
        raise HTTPException(401, "Please sign in.")
    if request.method in UNSAFE:
        # Double-submit style CSRF protection: header must match the token bound to the session.
        sent = request.headers.get(CSRF_HEADER, "")
        if not sent or sent != sess.csrf_token:
            raise HTTPException(403, "Missing or invalid CSRF token.")
    user = db.get(User, sess.user_id)
    if user is None:
        raise HTTPException(401, "Please sign in.")
    return Principal(user=user, session=sess)


@dataclass
class ProjectAccess:
    project: Project
    role: str
    principal: Principal

    @property
    def user(self) -> User:
        return self.principal.user

    def require(self, permission: str) -> None:
        if not policy.has_permission(self.role, permission):
            raise HTTPException(403, f"Your role ({self.role}) does not allow this action.")


def membership_role(db: Session, user_id: str, tenant_id: str) -> str | None:
    m = db.scalar(select(Membership).where(Membership.user_id == user_id, Membership.tenant_id == tenant_id,
                                           Membership.status == "active"))
    return m.role if m else None


def get_project_access(project_id: str, principal: Principal = Depends(current_principal),
                       db: Session = Depends(get_db)) -> ProjectAccess:
    """Tenant is always derived server-side from the project row and the caller's membership.

    A project in another tenant returns 404 (not 403) so its existence is not disclosed.
    """
    project = db.get(Project, project_id)
    if project is None or project.lifecycle_state == "archived":
        raise HTTPException(404, "Project not found.")
    role = membership_role(db, principal.user.id, project.tenant_id)
    if role is None:
        raise HTTPException(404, "Project not found.")
    return ProjectAccess(project=project, role=role, principal=principal)


def require_reauth(db: Session, principal: Principal, password: str | None) -> None:
    """Step-up authentication for sensitive actions (publish, rollback, delete)."""
    if password:
        if verify_password(password, principal.user.password_hash):
            principal.session.reauth_at = utcnow()
            db.add(principal.session)
            return
        raise HTTPException(401, "That password is incorrect.")
    recent = as_utc(principal.session.reauth_at)
    if recent and utcnow() - recent < timedelta(minutes=5):
        return
    raise HTTPException(401, "Please confirm your password to continue.")
