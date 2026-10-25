"""Shared knowledge board: how specialist agents pass what they know to each other.

Each agent receives `board.view()` — every current fact, insight and decision written so far — and writes
its own conclusions back with a rationale and the keys it relied on. The UI shows this as the team's
reasoning trail, so the owner can see *why* the site looks the way it does.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ..models import KnowledgeEntry, Project
from ..security import redact


class Board:
    def __init__(self, db: Session, project: Project) -> None:
        self.db = db
        self.project = project
        self._cache: dict[str, KnowledgeEntry] | None = None

    def _load(self) -> dict[str, KnowledgeEntry]:
        if self._cache is None:
            rows = self.db.scalars(select(KnowledgeEntry).where(
                KnowledgeEntry.project_id == self.project.id, KnowledgeEntry.is_current.is_(True))
                .order_by(KnowledgeEntry.created_at)).all()
            self._cache = {r.key: r for r in rows}
        return self._cache

    def write(self, agent: str, kind: str, key: str, content: Any, rationale: str = "", confidence: float = 1.0,
              based_on: list[str] | None = None) -> KnowledgeEntry:
        cur = self._load()
        if key in cur:
            if cur[key].content == content and cur[key].rationale == rationale:
                return cur[key]
            self.db.execute(update(KnowledgeEntry).where(KnowledgeEntry.project_id == self.project.id,
                                                         KnowledgeEntry.key == key).values(is_current=False))
        e = KnowledgeEntry(tenant_id=self.project.tenant_id, project_id=self.project.id, agent=agent, kind=kind,
                           key=key, content=redact(content), rationale=rationale, confidence=confidence,
                           based_on=based_on or [], is_current=True)
        self.db.add(e)
        cur[key] = e
        return e

    def get(self, key: str, default: Any = None) -> Any:
        e = self._load().get(key)
        return e.content if e else default

    def view(self) -> dict[str, Any]:
        """Compact snapshot handed to every agent (and to the model, when one is configured)."""
        return {k: {"by": e.agent, "kind": e.kind, "value": e.content, "why": e.rationale} for k, e in self._load().items()}

    def view_values(self) -> dict[str, Any]:
        return {k: e.content for k, e in self._load().items()}

    def trail(self) -> list[dict]:
        rows = self.db.scalars(select(KnowledgeEntry).where(KnowledgeEntry.project_id == self.project.id)
                               .order_by(KnowledgeEntry.created_at)).all()
        return [{"agent": r.agent, "kind": r.kind, "key": r.key, "content": r.content, "rationale": r.rationale,
                 "confidence": r.confidence, "based_on": r.based_on, "current": r.is_current,
                 "at": r.created_at.isoformat() if r.created_at else None} for r in rows]
