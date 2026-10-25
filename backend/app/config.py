"""Runtime configuration. Everything is overridable through environment variables."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("SITEFORGE_DATA_DIR", "./data")).resolve())
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    # SECRET_KEY protects the secrets vault. In production it must come from a KMS / secret manager.
    secret_key: str = field(default_factory=lambda: os.getenv("SITEFORGE_SECRET_KEY", ""))
    session_ttl_hours: int = int(os.getenv("SITEFORGE_SESSION_TTL_HOURS", "12"))
    cookie_secure: bool = field(default_factory=lambda: _env_bool("SITEFORGE_COOKIE_SECURE", False))
    approval_ttl_hours: int = int(os.getenv("SITEFORGE_APPROVAL_TTL_HOURS", "24"))
    # LLM provider: "rules" (deterministic, offline) or "anthropic" (needs ANTHROPIC_API_KEY).
    llm_provider: str = field(default_factory=lambda: os.getenv("SITEFORGE_LLM_PROVIDER", "auto"))
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    anthropic_model: str = field(default_factory=lambda: os.getenv("SITEFORGE_ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    browser_qa: str = field(default_factory=lambda: os.getenv("SITEFORGE_BROWSER_QA", "auto"))  # auto|on|off
    agent_budget_runs_per_project: int = int(os.getenv("SITEFORGE_AGENT_BUDGET", "500"))

    def __post_init__(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.database_url:
            self.database_url = f"sqlite:///{self.data_dir / 'siteforge.db'}"
        if not self.secret_key:
            key_file = self.data_dir / ".secret_key"
            if key_file.exists():
                self.secret_key = key_file.read_text().strip()
            else:
                self.secret_key = secrets.token_urlsafe(48)
                key_file.write_text(self.secret_key)
                key_file.chmod(0o600)

    @property
    def builds_dir(self) -> Path:
        p = self.data_dir / "builds"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def hosting_dir(self) -> Path:
        p = self.data_dir / "hosting"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads_dir(self) -> Path:
        p = self.data_dir / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p


settings = Settings()


def reload_settings() -> Settings:
    """Used by tests to point the app at a fresh data directory."""
    global settings
    settings = Settings()
    return settings
