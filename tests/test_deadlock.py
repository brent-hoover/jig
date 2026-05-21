"""Tests for ``jig.deadlock`` — age-based blocking-entry auto-resolution.

Phase 5 Task L: the orchestrator sweeps open blocking thread entries
on every tick and, depending on age, posts a Note (nudge) or an
Escalation (flip to ``needs_info``). The sweep takes an explicit
``now`` parameter so tests can dial the clock forward without
pulling in ``freezegun``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jig.deadlock import sweep_blocking_entries
from jig.store import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Escalation, Note
from jig.thread_mcp import handle_thread_ask
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- fixtures -------------------------------------------------------------


async def _make_stores(
    tmp_path: Path,
) -> tuple[TicketStore, ThreadStore, MessageBus, str]:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    ticket_id = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="orchestrator",
            status=TicketStatus.IN_PROGRESS,
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    return tickets, threads, bus, ticket_id


async def _post_blocking_question(
    threads: ThreadStore,
    tickets: TicketStore,
    bus: MessageBus,
    ticket_id: str,
    *,
    target: str = "reviewer",
    sender: str = "dev",
) -> str:
    result = await handle_thread_ask(
        tickets=tickets,
        threads=threads,
        bus=bus,
        sender=sender,
        args={
            "ticket_id": ticket_id,
            "target": target,
            "question": "needs a human?",
            "blocking": True,
        },
    )
    return result["question_id"]


# ---- sweep basics ---------------------------------------------------------


class TestDeadlockSweep:
    @pytest.mark.asyncio
    async def test_fresh_blocking_entry_no_action(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        await _post_blocking_question(threads, tickets, bus, ticket_id)
        # Sweep at "now" — age ≈ 0s, under both thresholds.
        result = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=4 * 3600,
            escalate_after_s=24 * 3600,
        )
        assert result.nudged == []
        assert result.escalated == []
        # Ticket remains in_progress (no status flip).
        ticket = await tickets.get(ticket_id)
        assert ticket is not None and ticket.status == TicketStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_entry_past_t1_posts_single_note(self, tmp_path: Path) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        qid = await _post_blocking_question(threads, tickets, bus, ticket_id)

        now = datetime.now(timezone.utc) + timedelta(hours=5)
        result = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=4 * 3600,
            escalate_after_s=24 * 3600,
            now=now,
        )
        assert result.nudged == [qid]
        assert result.escalated == []

        # A Note linked to the blocking entry now exists, authored by
        # the orchestrator and tagging the question's target.
        entries = await threads.for_ticket(ticket_id)
        notes = [e for e in entries if isinstance(e, Note) and e.responds_to == qid]
        assert len(notes) == 1
        assert notes[0].author == "orchestrator"
        assert "reviewer" in notes[0].text

        # Ticket status unchanged at T1 — only T2 escalations flip it.
        ticket = await tickets.get(ticket_id)
        assert ticket is not None and ticket.status == TicketStatus.IN_PROGRESS

    @pytest.mark.asyncio
    async def test_entry_past_t2_posts_escalation_and_flips_status(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        qid = await _post_blocking_question(threads, tickets, bus, ticket_id)

        now = datetime.now(timezone.utc) + timedelta(hours=25)
        result = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=4 * 3600,
            escalate_after_s=24 * 3600,
            now=now,
        )
        assert result.escalated == [qid]
        # Nudge also fires on the same pass since it hadn't been posted
        # yet — cumulative, not exclusive.
        assert result.nudged == [qid]

        entries = await threads.for_ticket(ticket_id)
        escs = [
            e for e in entries if isinstance(e, Escalation) and e.responds_to == qid
        ]
        assert len(escs) == 1
        assert escs[0].author == "orchestrator"
        assert escs[0].target == "any_human"

        ticket = await tickets.get(ticket_id)
        assert ticket is not None and ticket.status == TicketStatus.NEEDS_INFO

    @pytest.mark.asyncio
    async def test_idempotent_no_duplicate_nudge_or_escalation(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        qid = await _post_blocking_question(threads, tickets, bus, ticket_id)
        now = datetime.now(timezone.utc) + timedelta(hours=25)

        # First sweep — both actions fire.
        first = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=4 * 3600,
            escalate_after_s=24 * 3600,
            now=now,
        )
        assert first.nudged == [qid]
        assert first.escalated == [qid]

        # Second sweep — same clock, same entry — neither fires again.
        second = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=4 * 3600,
            escalate_after_s=24 * 3600,
            now=now,
        )
        assert second.nudged == []
        assert second.escalated == []

        # Only one Note and one Escalation total.
        entries = await threads.for_ticket(ticket_id)
        notes = [e for e in entries if isinstance(e, Note) and e.responds_to == qid]
        escs = [
            e for e in entries if isinstance(e, Escalation) and e.responds_to == qid
        ]
        assert len(notes) == 1
        assert len(escs) == 1

    @pytest.mark.asyncio
    async def test_resolved_blocking_entry_skipped(self, tmp_path: Path) -> None:
        """Non-blocking (resolved) entries don't appear in the sweep."""
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        qid = await _post_blocking_question(threads, tickets, bus, ticket_id)
        # Resolve the question.
        await threads.update(qid, {"resolved_by": "dev"})
        # Confirm it no longer shows up as blocking.
        assert not (await threads.has_unresolved_blocking(ticket_id))

        now = datetime.now(timezone.utc) + timedelta(hours=25)
        result = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=4 * 3600,
            escalate_after_s=24 * 3600,
            now=now,
        )
        assert result.nudged == []
        assert result.escalated == []

    @pytest.mark.asyncio
    async def test_zero_threshold_disables_tier(self, tmp_path: Path) -> None:
        """``nudge_after_s=0`` or ``escalate_after_s=0`` disables that
        tier entirely — useful for operators who want only one side of
        the auto-resolution running."""
        tickets, threads, bus, ticket_id = await _make_stores(tmp_path)
        qid = await _post_blocking_question(threads, tickets, bus, ticket_id)
        now = datetime.now(timezone.utc) + timedelta(hours=25)

        result = await sweep_blocking_entries(
            tickets=tickets,
            threads=threads,
            bus=bus,
            nudge_after_s=0,
            escalate_after_s=24 * 3600,
            now=now,
        )
        # Nudge disabled → no Note posted; escalation still fires.
        assert result.nudged == []
        assert result.escalated == [qid]
