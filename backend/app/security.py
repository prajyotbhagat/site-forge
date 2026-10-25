"""Security primitives: password hashing, session tokens, secrets vault, redaction, rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.orm import Session

from . import config
from .models import Secret

# ---------------------------------------------------------------- passwords

_SCRYPT = dict(n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
    except ValueError:
        return False
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return hmac.compare_digest(dk, expected)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def anonymize_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    return hmac.new(config.settings.secret_key.encode(), ip.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- vault


def _fernet() -> Fernet:
    key = hashlib.sha256(("vault:" + config.settings.secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


class Vault:
    """KMS-style secret storage. Callers get an opaque `vault://` reference, never the value.

    Only provider adapters call `reveal`, at the moment of use. Agents, prompts, logs and API responses
    must only ever carry the reference.
    """

    PREFIX = "vault://"

    @staticmethod
    def store(db: Session, tenant_id: str, value: str) -> str:
        ref_id = "sec_" + secrets.token_hex(16)
        db.add(Secret(id=ref_id, tenant_id=tenant_id, ciphertext=_fernet().encrypt(value.encode()).decode()))
        register_secret_for_redaction(value)
        return Vault.PREFIX + ref_id

    @staticmethod
    def reveal(db: Session, tenant_id: str, reference: str) -> str:
        ref_id = reference.removeprefix(Vault.PREFIX)
        row = db.get(Secret, ref_id)
        if row is None or row.tenant_id != tenant_id:  # tenant boundary on secret references
            raise PermissionError("secret reference not found for this tenant")
        try:
            value = _fernet().decrypt(row.ciphertext.encode()).decode()
        except InvalidToken as exc:  # pragma: no cover
            raise PermissionError("secret could not be decrypted") from exc
        register_secret_for_redaction(value)
        return value

    @staticmethod
    def delete(db: Session, tenant_id: str, reference: str) -> None:
        row = db.get(Secret, reference.removeprefix(Vault.PREFIX))
        if row is not None and row.tenant_id == tenant_id:
            db.delete(row)


# ---------------------------------------------------------------- redaction

_known_secrets: set[str] = set()
_SECRET_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9_-])sk-(?:ant-|proj-|live_|test_)?[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}(?![A-Za-z0-9])"),
    re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+=]{12,}"),
]


def register_secret_for_redaction(value: str) -> None:
    if value and len(value) >= 6:
        _known_secrets.add(value)


def find_secrets(text: str) -> list[str]:
    hits = [p.pattern for p in _SECRET_PATTERNS if p.search(text)]
    hits += ["known-vault-secret" for s in _known_secrets if s in text]
    return hits


def redact(obj):
    """Recursively redact secrets from strings/dicts/lists before logging or storing in traces."""
    if isinstance(obj, str):
        out = obj
        for s in _known_secrets:
            out = out.replace(s, "[REDACTED]")
        for p in _SECRET_PATTERNS:
            out = p.sub("[REDACTED]", out)
        return out
    if isinstance(obj, dict):
        return {k: ("[REDACTED]" if k.lower() in {"secret", "password", "token", "api_key"} else redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


# ---------------------------------------------------------------- rate limiting


class RateLimiter:
    """Sliding-window limiter. In-process; swap for Redis when running multiple API workers."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_s: int) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()
