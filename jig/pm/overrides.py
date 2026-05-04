"""Bones-first override audit (Track F Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Bones-first ordering and operator
override":

> Slash command: ``/plan unblock <epic-mvp>`` allows MVP work to start
> on epics whose bones is complete, while one or more other epics'
> bones continues. Override emits a ``bones_promoted_incomplete``
> analytics event so consequences are visible later if the unfinished
> bones forces a contract change that affects already-built MVP work.

This module ships:

- ``OverrideEntry`` — one row in the audit log (epic id + operator
  rationale + timestamp + ``cascade_risk_low_acknowledged`` so the SA
  hint is recorded if it was the trigger).
- ``OverrideStore`` — JSONL append-only at ``.jig/plan/overrides.jsonl``.
- ``record_unblock_override`` — append + emit
  ``BonesPromotedIncomplete`` analytics event.

Coordinator-side ``cascade_risk_low`` integration (already shipped in
Track C Final via ``next_layer_ready``) emits the event from the
``Coordinator.materialize_layer`` path when MVP is materialized while
bones is incomplete via the SA-flagged path.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import BonesPromotedIncomplete
from jig.atomic import atomic_write_text

__all__ = [
    "OVERRIDE_RELPATH",
    "OverrideEntry",
    "OverrideStore",
    "list_overrides",
    "record_unblock_override",
]


_logger = logging.getLogger(__name__)


OVERRIDE_RELPATH = Path(".jig") / "plan" / "overrides.jsonl"


class OverrideEntry(BaseModel):
    """One row in the override audit log."""

    model_config = ConfigDict(extra="forbid")

    epic_id: str
    rationale: str = ""
    cascade_risk_low_acknowledged: bool = False
    actor: str = "operator"
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    still_running_bones_epic_ids: list[str] = Field(default_factory=list)
    sa_marked_cascade_risk_low_epic_ids: list[str] = Field(default_factory=list)


class OverrideStore:
    """JSONL append-only store of bones-first override entries.

    Mirrors the deferred-queue + calibration patterns in jig.pm. Volume
    is bounded by operator activity — small file, atomic full rewrite
    on append.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._entries: list[OverrideEntry] = []
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._project_root / OVERRIDE_RELPATH

    async def load(self) -> None:
        path = self.path
        if not path.is_file():
            self._entries = []
            self._loaded = True
            return
        rows: list[OverrideEntry] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(OverrideEntry.model_validate_json(line))
            except Exception:
                _logger.warning(
                    "skipping malformed override row: %r", line
                )
        self._entries = rows
        self._loaded = True

    async def append(self, entry: OverrideEntry) -> None:
        if not self._loaded:
            await self.load()
        self._entries.append(entry)
        payload = "\n".join(
            json.dumps(e.model_dump(mode="json"), sort_keys=True)
            for e in self._entries
        )
        if payload:
            payload += "\n"
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, payload)

    def all(self) -> list[OverrideEntry]:
        return list(self._entries)


async def record_unblock_override(
    *,
    project_root: Path,
    epic_id: str,
    rationale: str = "",
    cascade_risk_low_acknowledged: bool = False,
    still_running_bones_epic_ids: list[str] | None = None,
    sa_marked_cascade_risk_low_epic_ids: list[str] | None = None,
    actor: str = "operator",
    emitter: EventEmitter | None = None,
) -> OverrideEntry:
    """Append one override row and emit ``BonesPromotedIncomplete``.

    The ``still_running_bones_epic_ids`` argument lists every epic
    whose bones layer is in flight at override time so the analytics
    event captures full context. ``sa_marked_cascade_risk_low_epic_ids``
    is the subset of those flagged ``cascade_risk_low: true`` by the
    SA — empty when the operator overrides without an SA hint.
    """
    store = OverrideStore(project_root)
    await store.load()
    entry = OverrideEntry(
        epic_id=epic_id,
        rationale=rationale,
        cascade_risk_low_acknowledged=cascade_risk_low_acknowledged,
        actor=actor,
        still_running_bones_epic_ids=list(still_running_bones_epic_ids or []),
        sa_marked_cascade_risk_low_epic_ids=list(
            sa_marked_cascade_risk_low_epic_ids or []
        ),
    )
    await store.append(entry)

    if emitter is not None:
        emitter.emit_nowait(
            BonesPromotedIncomplete(
                promoted_epic_ids=[epic_id],
                still_running_bones_epic_ids=list(
                    still_running_bones_epic_ids or []
                ),
                sa_marked_cascade_risk_low=list(
                    sa_marked_cascade_risk_low_epic_ids or []
                ),
                operator_rationale_category=(
                    rationale[:60] if rationale else None
                ),
            )
        )
    return entry


def list_overrides(project_root: Path) -> list[OverrideEntry]:
    """Synchronous helper for the CLI: load + return all entries."""
    store = OverrideStore(project_root)
    path = store.path
    if not path.is_file():
        return []
    rows: list[OverrideEntry] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(OverrideEntry.model_validate_json(line))
        except Exception:
            _logger.warning("skipping malformed override row: %r", line)
    return rows
