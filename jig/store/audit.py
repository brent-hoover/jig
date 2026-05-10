"""AuditStore — append-JSONL storage for canonicalization audit entries."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import Field

from jig.store.collection import Collection
from jig.store.models import StoreModel


RuleSource = Literal["formatter", "semgrep", "deprecation", "idempotency_check"]


class AuditEntry(StoreModel):
    """One record of a canonicalization rule applying a fix to a file."""

    run_id: str
    phase: str = "canonicalize"
    rule_id: str
    rule_source: RuleSource
    file_path: str
    before_hash: str
    after_hash: str
    applied_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ticket_id: str = ""


class AuditStore:
    """Append-only JSONL store for ``AuditEntry`` records."""

    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "run_id"],
        )

    async def load(self) -> None:
        await self._collection.load()

    async def append(self, entry: AuditEntry) -> str:
        raw = entry.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    def _load(self, raw: dict) -> AuditEntry:
        return AuditEntry.model_validate(raw)

    async def for_run(self, run_id: str) -> list[AuditEntry]:
        raws = await self._collection.find_where(run_id=run_id)
        return [self._load(r) for r in raws]

    async def for_ticket(self, ticket_id: str) -> list[AuditEntry]:
        raws = await self._collection.find_where(ticket_id=ticket_id)
        return [self._load(r) for r in raws]

    async def all(self) -> list[AuditEntry]:
        raws = await self._collection.find()
        return [self._load(r) for r in raws]


__all__ = ["AuditEntry", "AuditStore", "RuleSource"]
