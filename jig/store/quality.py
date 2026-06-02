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

from collections.abc import Mapping

from pydantic import ConfigDict, Field, field_validator

from jig.store.collection import Collection
from jig.store.models import StoreModel


def _to_sorted_pairs(value: object) -> object:
    """Coerce a dict (or already-coerced tuple-of-pairs) to a stable
    tuple-of-pairs representation. Stored that way so loaded snapshots
    are deeply immutable — ``frozen=True`` alone only blocks attribute
    reassignment, not in-place mutation of nested ``dict`` fields."""
    if isinstance(value, Mapping):
        return tuple(sorted(value.items()))
    # JSON deserialisation feeds list-of-lists; let pydantic coerce.
    return value


class QualitySnapshot(StoreModel):
    """One per-end-of-ticket quality summary.

    ``run_id`` is a label, NOT a unique key. The default format
    (``f"{ticket.id}.cycle{cycle}"``) can collide across review phases at
    the same cycle. Dedupe on insert is done by the caller on
    ``(run_id, phase, spawned_reviewers)`` so per-phase snapshots survive
    while operator-level retries don't duplicate. ``for_run`` therefore
    returns ≥1 row; ``--run-id`` filters in the CLI scope to whichever
    dispatch(es) shared that label.

    ``phase`` carries the workflow phase name (e.g. ``"review-tests"``
    vs. ``"review"``) when the dispatch site knows it; empty string when
    called from the legacy end-of-ticket federation path.

    Frozen — measurement records are read-only after construction;
    mutating a loaded snapshot would produce misleading audit output.
    ``populate_by_name=True`` is repeated explicitly because pydantic v2
    doesn't merge ``model_config`` from parent classes, and ``StoreModel``
    relies on it for the ``_id`` alias roundtrip.

    Nested-map fields (``taxonomy_hit_counts``, ``cell``, ``role_versions``)
    are stored as sorted ``tuple[tuple[k, v], ...]`` rather than ``dict``
    so the immutability is deep — pydantic's ``frozen=True`` only blocks
    attribute reassignment, not ``snap.cell["k"] = v``. Construction
    accepts ``dict`` input for ergonomics; a before-validator normalises
    to the sorted-pairs form. Read-access uses ``dict(s.cell).get(...)``
    or iterates pairs directly.
    """

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    ticket_id: str
    run_id: str
    phase: str = ""
    max_cc: int = Field(ge=0)
    ruff_findings: int = Field(ge=0)
    loc_delta: int
    taxonomy_hit_counts: tuple[tuple[str, int], ...] = ()
    cell: tuple[tuple[str, str], ...] = ()
    spawned_reviewers: tuple[str, ...] = ()
    role_versions: tuple[tuple[str, str], ...] = ()
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    _coerce_pairs = field_validator(
        "taxonomy_hit_counts",
        "cell",
        "role_versions",
        mode="before",
    )(lambda v: _to_sorted_pairs(v))


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
