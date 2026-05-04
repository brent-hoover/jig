"""Orphan-namespace tracking + cleanup helpers (Track E MVP deliverable 5).

A namespace is "orphan" when its associated ticket is no longer OPEN
or IN_PROGRESS — the agent finished long ago, but the namespace was
left behind (cleanup hook never fired, operator paused, daemon
crashed, ...).

Per ``docs/dev-environment/design.md`` §"Cleanup discipline" failure
mode 1 + the design's `cleanup_on_failure: archive` default, archived
namespaces accumulate until the operator inspects + drops them. This
module surfaces them via ``list_orphans`` / ``drop_orphan`` and
``OrphanTracker``.

For MVP scope we cross-reference the manifest's services × live
tickets in the store. Each terminal-status ticket (RESOLVED, FAILED,
BLOCKED, NEEDS_INFO, CLOSED, MERGE_CONFLICT) implies its namespace
should have been cleaned up; flag it as a candidate. Periodic-sweep
automation that actually queries the Postgres ``information_schema`` /
NATS subjects / etc. is Final scope.

The audit log at ``.jig/dev/orphan-log.jsonl`` records each detection
+ cleanup run so the operator has provenance.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.atomic import atomic_write_text
from jig.dev_env.provisioning import (
    ProvisioningRegistry,
    cleanup_agent_namespace,
    render_namespace,
)
from jig.schemas.dev_env import DevManifest, ManifestService
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus

__all__ = [
    "ORPHAN_LOG_RELATIVE",
    "OrphanedNamespace",
    "OrphanLogEntry",
    "OrphanTracker",
    "append_orphan_log",
    "drop_orphan",
    "list_orphans",
    "orphan_log_path",
]

_logger = logging.getLogger(__name__)

# Set of statuses that mean "the agent for this ticket is still live"
# — we don't flag namespaces for those tickets as orphan candidates.
_LIVE_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.OPEN, TicketStatus.IN_PROGRESS}
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class OrphanedNamespace(BaseModel):
    """One flagged orphan candidate.

    ``id`` is a stable composite of ``service_id`` + ``ticket_id`` so
    the operator's ``jig dev orphans drop <id>`` references something
    deterministic (and safe in shell — no spaces, all alnum + dashes).
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    service_id: str = Field(..., min_length=1)
    service_kind: str = Field(..., min_length=1)
    ticket_id: str = Field(..., min_length=1)
    namespace: str = Field(..., min_length=1)
    ticket_status: str = Field(..., min_length=1)


class OrphanLogEntry(BaseModel):
    """One row in the orphan-log audit trail."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    action: Literal["detect", "drop", "purge"]
    orphan_id: str | None = None
    orphan_count: int | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


ORPHAN_LOG_RELATIVE = Path(".jig") / "dev" / "orphan-log.jsonl"


def orphan_log_path(project_root: Path) -> Path:
    """``.jig/dev/orphan-log.jsonl`` — append-only orphan-detection trail."""
    return project_root / ORPHAN_LOG_RELATIVE


def append_orphan_log(project_root: Path, entry: OrphanLogEntry) -> None:
    """Append one ``OrphanLogEntry`` to the audit log.

    Atomic-append by reading + rewriting the whole file. JSONL grows
    unbounded but the typical orphan-log entry count stays small (one
    row per ``jig dev orphans list/drop/purge`` invocation).
    """
    path = orphan_log_path(project_root)
    existing = ""
    if path.is_file():
        existing = path.read_text()
    payload = entry.model_dump_json() + "\n"
    atomic_write_text(path, existing + payload)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _orphan_id(service_id: str, ticket_id: str) -> str:
    """Stable composite id used by the CLI."""
    return f"{service_id}:{ticket_id}"


def _scan_for_orphans(
    manifest: DevManifest, tickets: Iterable[Ticket]
) -> list[OrphanedNamespace]:
    """Cross-reference manifest services × tickets to find orphan candidates.

    A ticket whose status is OPEN or IN_PROGRESS is "live" and its
    namespace is presumed in active use. Any other status (RESOLVED,
    FAILED, BLOCKED, ...) implies the namespace should already be
    cleaned up; if it's still around, flag it. (MVP doesn't actually
    query the underlying service to confirm the namespace exists —
    that's Final scope.)
    """
    out: list[OrphanedNamespace] = []
    for service in manifest.services:
        if service.strategy != "shared_namespaced":
            continue
        for t in tickets:
            try:
                status = t.status
            except AttributeError:  # pragma: no cover — defensive
                continue
            if status in _LIVE_STATUSES:
                continue
            namespace = render_namespace(
                service.namespace_template,
                agent_id=t.assignee or "agent",
                ticket_id=t.id,
            )
            out.append(
                OrphanedNamespace(
                    id=_orphan_id(service.id, t.id),
                    service_id=service.id,
                    service_kind=service.kind,
                    ticket_id=t.id,
                    namespace=namespace,
                    ticket_status=status.value,
                )
            )
    return out


async def list_orphans(
    manifest: DevManifest, tickets: TicketStore | Iterable[Ticket]
) -> list[OrphanedNamespace]:
    """Return all orphan candidates for ``manifest`` against ``tickets``.

    ``tickets`` accepts either a ``TicketStore`` (we fetch all rows)
    or any iterable of ``Ticket`` (tests can pass a list directly).
    """
    if isinstance(tickets, TicketStore):
        rows = await tickets.list_all()
    else:
        rows = list(tickets)
    return _scan_for_orphans(manifest, rows)


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------


def _service_by_id(
    manifest: DevManifest, service_id: str
) -> ManifestService | None:
    return next((s for s in manifest.services if s.id == service_id), None)


async def drop_orphan(
    manifest: DevManifest,
    orphan: OrphanedNamespace,
    *,
    success_reason: str = "manual",
    registry: ProvisioningRegistry | None = None,
) -> None:
    """Drop one orphan via its service's cleanup path.

    ``success_reason`` controls whether to fire the success or failure
    cleanup policy: ``"manual"`` defaults to success (operator-driven
    drop); pass ``"failure_archive"`` to route through the archive
    path (preserve for inspection).
    """
    service = _service_by_id(manifest, orphan.service_id)
    if service is None:
        _logger.warning(
            "drop_orphan: service id=%s not in manifest; skipping",
            orphan.service_id,
        )
        return
    registry = registry or ProvisioningRegistry()
    fake_manifest = DevManifest(
        services=[service],
        connection_string_templates={
            orphan.service_id: manifest.connection_string_templates.get(
                orphan.service_id, ""
            )
        },
    )
    await cleanup_agent_namespace(
        fake_manifest,
        agent_id="orphan-tracker",
        ticket_id=orphan.ticket_id,
        success=success_reason != "failure_archive",
        registry=registry,
    )


# ---------------------------------------------------------------------------
# OrphanTracker — facade tying scan + log + drop together for the CLI
# ---------------------------------------------------------------------------


class OrphanTracker:
    """Thin facade for the operator-facing orphan workflow.

    Wraps ``list_orphans`` + ``drop_orphan`` so the CLI doesn't have to
    juggle the manifest + tickets + log path separately. Stateless
    across calls — every method re-reads from disk for freshness.
    """

    def __init__(
        self,
        project_root: Path,
        manifest: DevManifest,
        tickets: TicketStore,
        *,
        registry: ProvisioningRegistry | None = None,
    ) -> None:
        self._root = project_root
        self._manifest = manifest
        self._tickets = tickets
        self._registry = registry or ProvisioningRegistry()

    async def list_orphans(self) -> list[OrphanedNamespace]:
        rows = await list_orphans(self._manifest, self._tickets)
        append_orphan_log(
            self._root,
            OrphanLogEntry(action="detect", orphan_count=len(rows)),
        )
        return rows

    async def drop(
        self, orphan_id: str, *, success_reason: str = "manual"
    ) -> bool:
        """Drop the orphan whose composite id matches; return True iff found."""
        rows = await list_orphans(self._manifest, self._tickets)
        match = next((o for o in rows if o.id == orphan_id), None)
        if match is None:
            return False
        await drop_orphan(
            self._manifest,
            match,
            success_reason=success_reason,
            registry=self._registry,
        )
        append_orphan_log(
            self._root,
            OrphanLogEntry(action="drop", orphan_id=orphan_id),
        )
        return True

    async def purge(self) -> int:
        """Drop every orphan; return the count purged."""
        rows = await list_orphans(self._manifest, self._tickets)
        for o in rows:
            await drop_orphan(
                self._manifest, o, registry=self._registry
            )
        append_orphan_log(
            self._root,
            OrphanLogEntry(action="purge", orphan_count=len(rows)),
        )
        return len(rows)
