"""Realism budget logging (Track H MVP follow-on).

Per ``docs/synthetic-operator/design.md`` §"Realism budget", the
realism budget tracks real-operator behaviors that surprised the
simulator — things a real human did that no scripted persona would
have produced. Each gap is logged structured-ly so the operator can
later triage which to cover (extend a persona, add a scenario), drop
(too rare to bother), or defer.

For MVP scope this module ships the **logging + inspection** surface:

- ``RealismGap`` Pydantic model with the load-bearing fields.
- A JSONL store at ``.jig/sim/realism-gaps.jsonl`` (built on
  ``jig.store.collection.Collection`` so the persistence + indexing +
  load story is identical to every other JSONL store in the project).
- ``log_gap(gap)`` / ``list_gaps(since=...)`` functions for
  programmatic + CLI access.

Gap **consumption** (mechanically growing the personas to cover the
backlog) is Final — this just builds the store so gaps accumulate.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.store.collection import Collection

__all__ = [
    "REALISM_GAPS_RELPATH",
    "RealismGap",
    "RealismGapsStore",
    "list_gaps",
    "log_gap",
]


# Relative to the project root. Lives under ``.jig/sim/`` so all
# simulator-owned state nests cleanly. JSONL semantics matches the
# rest of the JSONL store family.
REALISM_GAPS_RELPATH = Path(".jig") / "sim" / "realism-gaps.jsonl"


class RealismGap(BaseModel):
    """One observed realism gap.

    ``kind`` is a free-form short label the operator picks ("ambiguous-
    confirmation", "mid-stream-scope-change", etc.) — Final formalizes
    a taxonomy; MVP keeps it open so logging stays low-friction.

    ``persona_to_extend`` is optional; when set, names the existing
    persona id whose profile should grow to cover this behavior.
    Triage tooling (Final) reads this hint.

    ``source`` distinguishes operator-logged gaps (the operator hit it
    while running jig) from real-run gaps (a real-mode sim run
    surfaced behavior the simulator didn't anticipate).
    """

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    source: Literal["operator", "real-run"] = "operator"
    persona_to_extend: str | None = None


class RealismGapsStore:
    """Thin wrapper around ``Collection`` for the realism-gaps JSONL.

    Owns the on-disk path + the Collection lifecycle. Tests + CLI
    callers construct one of these per project root; the underlying
    Collection's load/insert/find APIs handle the JSONL round-trip.
    """

    def __init__(self, project_root: Path) -> None:
        self._path = project_root / REALISM_GAPS_RELPATH
        self._collection = Collection(self._path, index_fields=["source"])
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._path

    async def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        await self._collection.load()
        self._loaded = True

    async def log_gap(self, gap: RealismGap) -> str:
        """Append one gap to the JSONL store. Returns the assigned id."""
        await self._ensure_loaded()
        return await self._collection.insert(gap.model_dump(mode="json"))

    async def list_gaps(
        self, *, since: datetime | None = None
    ) -> list[RealismGap]:
        """Return every gap, chronologically. ``since`` filters by observed_at."""
        await self._ensure_loaded()
        rows = await self._collection.find()
        gaps: list[RealismGap] = []
        for row in rows:
            payload = {k: v for k, v in row.items() if not k.startswith("_")}
            gap = RealismGap.model_validate(payload)
            if since is not None and gap.observed_at < since:
                continue
            gaps.append(gap)
        gaps.sort(key=lambda g: g.observed_at)
        return gaps


# ---- module-level convenience -------------------------------------------


async def log_gap(project_root: Path, gap: RealismGap) -> str:
    """One-shot convenience: open store + append one gap.

    Construct a long-lived ``RealismGapsStore`` if you need batch
    operations; this helper opens + tears down a fresh Collection per
    call (cheap because the JSONL is small + Collection's load is
    incremental).
    """
    store = RealismGapsStore(project_root)
    return await store.log_gap(gap)


async def list_gaps(
    project_root: Path, *, since: datetime | None = None
) -> list[RealismGap]:
    """One-shot convenience: open store + read all gaps."""
    store = RealismGapsStore(project_root)
    return await store.list_gaps(since=since)
