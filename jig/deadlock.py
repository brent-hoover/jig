"""Deadlock auto-resolution — orchestrator-as-resolver-of-last-resort.

Per doc 08 §Deadlock resolution: when a blocking thread entry sits
too long without action, the orchestrator has to break the wait.
Phase 5 Task L implements this as an age-based sweep over every
non-terminal ticket's blocking entries:

* **T1** (``nudge_after_s``, default 4h): post a Note tagging the
  blocking entry's target actor. Minimal — just a poke to get
  attention before hitting the human-grade escalation.
* **T2** (``escalate_after_s``, default 24h): post an Escalation to
  ``any_human`` and flip the ticket to ``needs_info`` so the TUI
  surfaces it prominently.

Thresholds come from ``config.deadlock.nudge_after_s`` /
``escalate_after_s``; callers pass them in per-sweep so this module
stays free of global-config imports and is easy to test with a
fake clock (``now=...``).

Idempotency relies on the nudge Note and escalation Escalation both
carrying ``responds_to=<blocking_entry.id>``. Before posting, the
sweep checks the ticket's existing entries for a prior nudge or
escalation against the same entry id and no-ops if one already
landed. This keeps a fast-running orchestrator (e.g., WS tick
every few seconds) from spamming the thread on a long-open entry.

The sweep deliberately does not propagate exceptions for individual
tickets — a bad record on one ticket shouldn't wedge the rest of
the project. Problems are logged and the sweep moves on; the caller
gets the successful nudge/escalate ids in the return value.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from jig.store import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Escalation, Note, ThreadEntry
from jig.ticket import TicketStatus

if TYPE_CHECKING:  # avoid circulars; only needed for type hints
    pass

_logger = logging.getLogger(__name__)


# Tickets in these statuses are effectively done — no point
# auto-resolving their old blocking entries. Leaving
# ``NEEDS_INFO`` out of the skip set is deliberate: a T1 nudge
# can still fire on a ticket a T2 already flipped, keeping the
# audit trail consistent if thresholds change or the sweep
# races. In practice the idempotency check on ``responds_to``
# stops re-firing anyway.
_TERMINAL_STATUSES: frozenset[TicketStatus] = frozenset(
    {
        TicketStatus.RESOLVED,
        TicketStatus.BLOCKED,  # manually parked — operator owns it
        TicketStatus.FAILED,
    }
)


@dataclass
class DeadlockSweepResult:
    """Summary of a single sweep pass.

    Lists carry blocking-entry ids so callers can emit telemetry
    without re-walking the store.
    """

    nudged: list[str] = field(default_factory=list)
    escalated: list[str] = field(default_factory=list)


async def sweep_blocking_entries(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    nudge_after_s: int,
    escalate_after_s: int,
    now: datetime | None = None,
) -> DeadlockSweepResult:
    """Run one deadlock auto-resolution pass.

    ``now`` is the clock reference for age comparisons; defaults to
    UTC ``datetime.now`` but tests pass a fixed value to dial the
    sweep past a threshold without waiting hours. ``nudge_after_s=0``
    or ``escalate_after_s=0`` disables that tier — useful when an
    operator wants only one side firing.

    Returns a :class:`DeadlockSweepResult` listing which blocking
    entry ids received each action. Entries that triggered both a
    nudge and an escalation on the same pass appear in both lists.
    """
    now = now or datetime.now(timezone.utc)
    result = DeadlockSweepResult()

    for ticket in await tickets.list_all():
        if ticket.status in _TERMINAL_STATUSES:
            continue

        try:
            entries = await threads.for_ticket(ticket.id)
        except Exception:
            _logger.exception(
                "deadlock sweep: failed to load thread entries for ticket %s",
                ticket.id,
            )
            continue

        # Skip entries the orchestrator itself posted as responders
        # (``responds_to`` is non-null). Those are auto-resolution
        # artefacts — re-sweeping them would cascade into escalations-
        # of-escalations and burn the thread.
        blocking = [
            e
            for e in entries
            if e.is_blocking() and getattr(e, "responds_to", None) is None
        ]
        if not blocking:
            continue

        for entry in blocking:
            age_s = (now - entry.created_at).total_seconds()
            if age_s < 0:
                # Clock skew — created_at is in the future. Don't act.
                continue

            already_nudged = _has_responder(entries, kind_cls=Note, entry_id=entry.id)
            already_escalated = _has_responder(
                entries, kind_cls=Escalation, entry_id=entry.id
            )

            # Escalation tier first: if we're past T2, we want to see
            # needs_info surface even if the T1 nudge hasn't been
            # recorded yet (the cumulative nudge below still posts).
            if (
                escalate_after_s > 0
                and age_s >= escalate_after_s
                and not already_escalated
            ):
                await _post_escalation(
                    tickets=tickets,
                    threads=threads,
                    bus=bus,
                    ticket_id=ticket.id,
                    entry=entry,
                )
                result.escalated.append(entry.id)

            if nudge_after_s > 0 and age_s >= nudge_after_s and not already_nudged:
                await _post_nudge(
                    threads=threads,
                    bus=bus,
                    ticket_id=ticket.id,
                    entry=entry,
                )
                result.nudged.append(entry.id)

    return result


def _has_responder(
    entries: list[ThreadEntry], *, kind_cls: type, entry_id: str
) -> bool:
    """Whether the ticket's entries already contain an orchestrator-
    posted responder of type ``kind_cls`` pointing at ``entry_id``."""
    for e in entries:
        if isinstance(e, kind_cls) and getattr(e, "responds_to", None) == entry_id:
            return True
    return False


def _describe_target(entry: ThreadEntry) -> str:
    """Human-readable phrase for the actor waited on by ``entry``.

    Used in the nudge Note's text. Questions and Escalations carry an
    explicit ``target``; other blocking kinds (Objection, Handoff)
    don't — we name the kind instead so the Note still reads
    sensibly.
    """
    target = getattr(entry, "target", None)
    if target:
        return str(target)
    return f"{entry.kind} author"


async def _post_nudge(
    *,
    threads: ThreadStore,
    bus: MessageBus,
    ticket_id: str,
    entry: ThreadEntry,
) -> None:
    target = _describe_target(entry)
    age_hours = int(
        (datetime.now(timezone.utc) - entry.created_at).total_seconds() // 3600
    )
    text = (
        f"Deadlock nudge: {entry.kind} {entry.id!r} has been open "
        f"~{age_hours}h waiting on {target}. Please respond or "
        f"reassign."
    )
    note = Note(
        ticket_id=ticket_id,
        author="orchestrator",
        text=text,
        responds_to=entry.id,
    )
    nid = await threads.post(note)
    await bus.publish(
        Message(
            sender="orchestrator",
            to=target,
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "deadlock_nudge_posted",
                "ticket_id": ticket_id,
                "note_id": nid,
                "responds_to": entry.id,
                "target": target,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    _logger.info(
        "deadlock sweep: nudge posted ticket=%s entry=%s target=%s",
        ticket_id,
        entry.id,
        target,
    )


async def _post_escalation(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    ticket_id: str,
    entry: ThreadEntry,
) -> None:
    target = _describe_target(entry)
    details = (
        f"{entry.kind} {entry.id!r} remained open past the deadlock "
        f"threshold waiting on {target}. Auto-escalated to human "
        f"review; ticket moved to needs_info."
    )
    escalation = Escalation(
        ticket_id=ticket_id,
        author="orchestrator",
        reason="deadlock_timeout",
        details=details,
        target="any_human",
        responds_to=entry.id,
    )
    eid = await threads.post(escalation)

    # Flip the ticket to NEEDS_INFO so the TUI / WS surface it.
    # ``update_status`` is safe to call with the same status — the
    # update just rewrites updated_at — but skip it if we're already
    # needs_info to avoid churn.
    ticket = await tickets.get(ticket_id)
    if ticket is not None and ticket.status != TicketStatus.NEEDS_INFO:
        await tickets.update_status(ticket_id, TicketStatus.NEEDS_INFO)

    await bus.publish(
        Message(
            sender="orchestrator",
            to="any_human",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "deadlock_escalation_posted",
                "ticket_id": ticket_id,
                "escalation_id": eid,
                "responds_to": entry.id,
                "waiting_on": target,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    _logger.info(
        "deadlock sweep: escalation posted ticket=%s entry=%s waiting_on=%s",
        ticket_id,
        entry.id,
        target,
    )


__all__ = [
    "DeadlockSweepResult",
    "sweep_blocking_entries",
]
