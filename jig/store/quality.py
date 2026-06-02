"""QualitySnapshotStore — append-only JSONL of per-end-of-ticket quality
snapshots (radon CC, ruff findings, LoC delta, taxonomy hit counts) tagged
with attribution cells.

Separate from ``AuditStore`` because the shape is different: ``AuditEntry``
is per-rule-application; ``QualitySnapshot`` is a per-run summary used for
quality measurement and cell-based attribution.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import Field

from jig.store.collection import Collection
from jig.store.models import StoreModel


class QualitySnapshot(StoreModel):
    """One per-end-of-ticket quality summary.

    ``run_id`` is a label, NOT a unique key. The default format
    (``f"{ticket.id}.cycle{cycle}"``) can collide across review phases at
    the same cycle (e.g. ``review-tests`` and the final ``review`` both at
    ``cycle=0``). Dedupe on insert is done by the caller on
    ``(run_id, spawned_reviewers)`` so per-phase snapshots survive while
    operator-level retries don't duplicate. ``for_run`` therefore returns
    ≥1 row; ``--run-id`` filters in the CLI scope to whichever
    dispatch(es) shared that label.
    """

    ticket_id: str
    run_id: str
    max_cc: int = Field(ge=0)
    ruff_findings: int = Field(ge=0)
    loc_delta: int
    taxonomy_hit_counts: dict[str, int] = Field(default_factory=dict)
    cell: dict[str, str] = Field(default_factory=dict)
    spawned_reviewers: tuple[str, ...] = ()
    role_versions: dict[str, str] = Field(default_factory=dict)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class QualitySnapshotStore:
    """Append-only JSONL store for ``QualitySnapshot`` records."""

    def __init__(self, path: Path) -> None:
        self._collection = Collection(path, index_fields=["ticket_id", "run_id"])

    async def load(self) -> None:
        await self._collection.load()

    async def append(self, snap: QualitySnapshot) -> str:
        raw = snap.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    def _load(self, raw: dict) -> QualitySnapshot:
        return QualitySnapshot.model_validate(raw)

    async def for_ticket(self, ticket_id: str) -> list[QualitySnapshot]:
        return [
            self._load(r)
            for r in await self._collection.find_where(ticket_id=ticket_id)
        ]

    async def for_run(self, run_id: str) -> list[QualitySnapshot]:
        return [self._load(r) for r in await self._collection.find_where(run_id=run_id)]

    async def all(self) -> list[QualitySnapshot]:
        return [self._load(r) for r in await self._collection.find()]


__all__ = ["QualitySnapshot", "QualitySnapshotStore"]
