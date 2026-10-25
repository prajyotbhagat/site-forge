from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config
from ..db import get_db
from ..deps import SESSION_COOKIE, Principal, current_principal, load_session
from ..models import AuthSession, Membership, Tenant, User, utcnow
from ..onboarding.schema import EMAIL_RE
from ..security import anonymize_ip, hash_password, limiter, new_token, token_hash, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(default="", max_length=200)
    organization: str = Field(default="", max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


def _start_session(db: Session, response: Response, user: User) -> dict:
    token = new_token()
    sess = AuthSession(id=token_hash(token), user_id=user.id, csrf_token=new_token(),
                       expires_at=utcnow() + timedelta(hours=config.settings.session_ttl_hours), reauth_at=utcnow())
    db.add(sess)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax", secure=config.settings.cookie_secure,
                        max_age=config.settings.session_ttl_hours * 3600, path="/")
    return {"user": {"id": user.id, "email": user.email, "name": user.name}, "csrf_token": sess.csrf_token}


@router.post("/register")
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    ip = anonymize_ip(request.client.host if request.client else None)
    if not limiter.allow(f"register:{ip}", 10, 3600):
        raise HTTPException(429, "Too many attempts. Please try again later.")
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(422, "Please enter a valid email address.")
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists. Please sign in.")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    tenant = Tenant(name=body.organization.strip() or (body.name.strip() or email.split("@")[0]) + "'s workspace")
    db.add_all([user, tenant])
    db.flush()
    db.add(Membership(user_id=user.id, tenant_id=tenant.id, role="owner"))
    out = _start_session(db, response, user)
    audit.record(db, actor=user.id, action="auth.register", tenant_id=tenant.id)
    db.commit()
    return out


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    ip = anonymize_ip(request.client.host if request.client else None)
    if not limiter.allow(f"login:{ip}:{email}", 8, 900):
        raise HTTPException(429, "Too many sign-in attempts. Please wait 15 minutes.")
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(body.password, user.password_hash):
        audit.record(db, actor=email, action="auth.login", outcome="failure")
        db.commit()
        raise HTTPException(401, "Email or password is incorrect.")
    out = _start_session(db, response, user)
    audit.record(db, actor=user.id, action="auth.login")
    db.commit()
    return out


@router.post("/logout")
def logout(request: Request, response: Response, principal: Principal = Depends(current_principal),
           db: Session = Depends(get_db)):
    db.delete(principal.session)
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request, db: Session = Depends(get_db)):
    sess = load_session(db, request)
    user = db.get(User, sess.user_id) if sess else None
    if user is None:
        return {"user": None}
    tenants = db.execute(select(Tenant, Membership.role).join(Membership, Membership.tenant_id == Tenant.id)
                         .where(Membership.user_id == user.id, Membership.status == "active")).all()
    return {"user": {"id": user.id, "email": user.email, "name": user.name}, "csrf_token": sess.csrf_token,
            "workspaces": [{"id": t.id, "name": t.name, "role": r} for t, r in tenants]}
