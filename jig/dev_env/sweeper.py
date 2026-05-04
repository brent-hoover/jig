"""Orphan sweeper with operator-confirmation (Track E Final).

Per ``docs/dev-environment/design.md`` §"Cleanup discipline" failure
mode 1: a periodic sweep finds orphan namespaces older than X days and
proposes them for cleanup. The MVP shipped the orphan tracker + CLI
list/drop/purge surface; Final adds the categorizing sweeper +
operator-gated apply path.

The sweeper does NOT auto-drop. It produces a structured
:class:`SweepReport` with three buckets:

- ``auto_safe`` — older than the configured threshold AND no live
  ticket reference (resolved / closed). Operator can ``apply
  --bucket auto_safe`` without ``--confirm`` (low-risk).
- ``needs_confirm`` — terminal status but recent activity (failed /
  blocked within the threshold window). Operator should look first;
  ``apply --bucket needs_confirm`` requires ``--confirm``.
- ``keep`` — recent activity (under threshold + still terminal but
  fresh). Sweeper leaves these alone.

All apply actions land in ``.jig/dev/sweeper-log.jsonl`` for audit.

Background scheduling into ``Orchestrator.startup`` is out of scope
for this commit; the sweeper is invoked via CLI (or programmatically
by tests) here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jig.atomic import atomic_write_text
from jig.dev_env.orphans import (
    OrphanedNamespace,
    drop_orphan,
    list_orphans,
)
from jig.dev_env.provisioning import ProvisioningRegistry
from jig.schemas.dev_env import DevManifest
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus

__all__ = [
    "DEFAULT_THRESHOLD_DAYS",
    "OrphanSweeper",
    "SWEEPER_LOG_RELATIVE",
    "SweepBucket",
    "SweepLogEntry",
    "SweepReport",
    "append_sweeper_log",
    "sweeper_log_path",
]

_logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD_DAYS = 7

# Tickets in these statuses are "stable cleanup candidates" — RESOLVED /
# CLOSED reflect deliberate end-of-life; their namespaces should already
# be cleaned up. Any age past threshold → auto_safe.
_STABLE_TERMINAL_STATUSES: frozenset[TicketStatus] = frozenset(
    {TicketStatus.RESOLVED, TicketStatus.CLOSED}
)

# Tickets in these statuses are "needs operator review" — FAILED /
# BLOCKED / NEEDS_INFO / MERGE_CONFLICT may still be debug-relevant
# even when old. Any age → needs_confirm.
_REVIEW_REQUIRED_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.FAILED,
        TicketStatus.BLOCKED,
        TicketStatus.NEEDS_INFO,
        TicketStatus.MERGE_CONFLICT,
    }
)


# ---------------------------------------------------------------------------
# Buckets + report
# ---------------------------------------------------------------------------


class SweepBucket(str, Enum):
    """The three buckets the apply CLI exposes."""

    AUTO_SAFE = "auto_safe"
    NEEDS_CONFIRM = "needs_confirm"
    ALL = "all"


class SweepReport(BaseModel):
    """Categorized orphan inventory the sweeper returns from ``run()``."""

    model_config = ConfigDict(extra="forbid")

    auto_safe: list[OrphanedNamespace] = Field(default_factory=list)
    needs_confirm: list[OrphanedNamespace] = Field(default_factory=list)
    keep: list[OrphanedNamespace] = Field(default_factory=list)
    threshold_days: int = DEFAULT_THRESHOLD_DAYS
    generated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def total(self) -> int:
        return len(self.auto_safe) + len(self.needs_confirm) + len(self.keep)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


SWEEPER_LOG_RELATIVE = Path(".jig") / "dev" / "sweeper-log.jsonl"


class SweepLogEntry(BaseModel):
    """One row in the sweeper audit trail."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    action: str = Field(..., min_length=1)
    bucket: str | None = None
    threshold_days: int | None = None
    auto_safe_count: int | None = None
    needs_confirm_count: int | None = None
    keep_count: int | None = None
    dropped_count: int | None = None
    detail: str | None = None


def sweeper_log_path(project_root: Path) -> Path:
    """``.jig/dev/sweeper-log.jsonl``."""
    return project_root / SWEEPER_LOG_RELATIVE


def append_sweeper_log(project_root: Path, entry: SweepLogEntry) -> None:
    """Append one ``SweepLogEntry`` row to the audit log atomically."""
    path = sweeper_log_path(project_root)
    existing = path.read_text() if path.is_file() else ""
    payload = entry.model_dump_json() + "\n"
    atomic_write_text(path, existing + payload)


# ---------------------------------------------------------------------------
# Sweeper
# ---------------------------------------------------------------------------


class OrphanSweeper:
    """Categorize + apply orphan cleanup with operator-gated confirmation.

    Construction takes the project root + the dev manifest + a ticket
    store; the sweeper cross-references manifest services × terminal-
    status tickets via the existing :func:`list_orphans` helper, then
    buckets each candidate per the categorization rules.

    Apply runs the existing ``drop_orphan`` path per bucket and emits
    one audit-log row per call.
    """

    def __init__(
        self,
        project_root: Path,
        manifest: DevManifest,
        tickets: TicketStore,
        *,
        threshold_days: int = DEFAULT_THRESHOLD_DAYS,
        registry: ProvisioningRegistry | None = None,
    ) -> None:
        self._root = project_root
        self._manifest = manifest
        self._tickets = tickets
        self._threshold_days = threshold_days
        self._registry = registry or ProvisioningRegistry()

    async def run(self) -> SweepReport:
        """Detect orphans, categorize them, return the report.

        Records one ``run`` row in the audit log so the operator can
        trace which sweep produced which apply later.
        """
        report = await self._categorize()
        append_sweeper_log(
            self._root,
            SweepLogEntry(
                action="run",
                threshold_days=self._threshold_days,
                auto_safe_count=len(report.auto_safe),
                needs_confirm_count=len(report.needs_confirm),
                keep_count=len(report.keep),
            ),
        )
        return report

    async def _categorize(self) -> SweepReport:
        rows = await list_orphans(self._manifest, self._tickets)
        all_tickets = await self._tickets.list_all()
        ticket_by_id = {t.id: t for t in all_tickets}
        threshold = datetime.now(timezone.utc) - timedelta(
            days=self._threshold_days
        )
        report = SweepReport(threshold_days=self._threshold_days)
        for orph in rows:
            ticket = ticket_by_id.get(orph.ticket_id)
            if ticket is None:
                # Defensive: orphan references a ticket no longer in
                # the store. Treat as auto_safe (stale).
                report.auto_safe.append(orph)
                continue
            updated_at = ticket.updated_at
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            is_old = updated_at < threshold
            try:
                status = ticket.status
            except AttributeError:
                report.needs_confirm.append(orph)
                continue
            if status in _STABLE_TERMINAL_STATUSES and is_old:
                report.auto_safe.append(orph)
            elif status in _STABLE_TERMINAL_STATUSES and not is_old:
                report.keep.append(orph)
            elif status in _REVIEW_REQUIRED_STATUSES:
                # Failed/blocked: needs operator look regardless of age.
                report.needs_confirm.append(orph)
            else:
                # Other terminal-ish status (defensive): keep.
                report.keep.append(orph)
        return report

    async def apply(
        self,
        report: SweepReport,
        *,
        bucket: SweepBucket,
    ) -> int:
        """Drop every orphan in the named bucket; return the count dropped.

        ``bucket=ALL`` walks both ``auto_safe`` and ``needs_confirm``
        (``keep`` is never auto-dropped). The CLI gates ``needs_confirm``
        and ``all`` on ``--confirm`` so a stray ``apply --bucket all``
        doesn't immediately destroy operator-debug evidence.
        """
        if bucket == SweepBucket.AUTO_SAFE:
            targets = list(report.auto_safe)
        elif bucket == SweepBucket.NEEDS_CONFIRM:
            targets = list(report.needs_confirm)
        elif bucket == SweepBucket.ALL:
            targets = list(report.auto_safe) + list(report.needs_confirm)
        else:  # pragma: no cover — enum guards
            raise ValueError(f"unknown SweepBucket {bucket!r}")
        for orph in targets:
            try:
                await drop_orphan(
                    self._manifest, orph, registry=self._registry
                )
            except Exception:
                _logger.warning(
                    "OrphanSweeper.apply: drop failed for %s",
                    orph.id,
                    exc_info=True,
                )
        append_sweeper_log(
            self._root,
            SweepLogEntry(
                action="apply",
                bucket=bucket.value,
                dropped_count=len(targets),
            ),
        )
        return len(targets)
