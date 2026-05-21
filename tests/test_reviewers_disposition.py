"""Severity-tier disposition policy (Track G Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Severity tiers and disposition":

- critical → block (ticket FAILED with reason ``reviewer-critical``)
- important → consult SA (``Handoff(phase="sa-consult")`` posted)
- notable → DEFERRED (``Coordinator.defer_ticket(...)``)

These tests pin each branch + the interaction with the Coordinator
helper. Coverage matrix: each severity alone, multiple severities
mixed, idempotent re-application, missing-coordinator/missing-thread
fallbacks.
"""

from __future__ import annotations

from pathlib import Path

from jig.coordinator import Coordinator
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.disposition import (
    FAIL_REASON_REVIEWER_CRITICAL,
    SA_CONSULT_PHASE,
    apply_severity_disposition,
)
from jig.store.tickets import TicketStore
from jig.store.threads import ThreadStore
from jig.thread import Handoff
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- helpers -------------------------------------------------------------


async def _build_stores(
    tmp_path: Path,
) -> tuple[TicketStore, ThreadStore, Coordinator]:
    """Wire fresh per-test stores + Coordinator instance."""
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    await tickets.load()
    await threads.load()
    coord = Coordinator(tickets=tickets, project_root=tmp_path)
    return tickets, threads, coord


async def _make_ticket(store: TicketStore, ticket_id: str = "t-disp") -> Ticket:
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="disposition test",
        created_by="planner-pm",
        description=TICKET_AC_PLACEHOLDER,
    )
    await store.create(t)
    fresh = await store.get(ticket_id)
    assert fresh is not None
    return fresh


def _comment(
    *,
    severity: str,
    reviewer: str = "reviewer-pattern-conformance",
    type_: str = "pattern-divergence",
    confidence: float = 0.8,
    contract_uri: str | None = None,
    file: str | None = "jig/foo.py",
    prose: str = "x" * 50,
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType(type_),
        severity=Severity(severity),
        reviewer=reviewer,
        prose=prose,
        confidence=confidence,
        contract_uri=contract_uri,
        file=file,
    )


# ---- critical → FAILED ---------------------------------------------------


class TestCriticalDisposition:
    async def test_critical_marks_ticket_failed(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [_comment(severity="critical")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        assert len(result.blocked_by) == 1
        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED

    async def test_critical_stamps_fail_reason_label(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        await apply_severity_disposition(
            [_comment(severity="critical")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert f"fail:{FAIL_REASON_REVIEWER_CRITICAL}" in fresh.labels

    async def test_critical_idempotent_on_already_failed_ticket(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        await tickets.update_status(ticket.id, TicketStatus.FAILED)
        result = await apply_severity_disposition(
            [_comment(severity="critical")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        assert len(result.blocked_by) == 1
        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED

    async def test_multiple_criticals_one_status_flip(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        comments = [_comment(severity="critical") for _ in range(3)]
        result = await apply_severity_disposition(
            comments, ticket, tickets, coord, threads=threads
        )
        assert len(result.blocked_by) == 3
        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED
        assert fresh.labels.count(f"fail:{FAIL_REASON_REVIEWER_CRITICAL}") == 1


# ---- important → SA-consult Handoff -------------------------------------


class TestImportantDisposition:
    async def test_important_posts_sa_consult_handoff(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [_comment(severity="important")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        assert len(result.consulted_sa) == 1

        entries = await threads.for_ticket(ticket.id)
        handoffs = [e for e in entries if isinstance(e, Handoff)]
        assert len(handoffs) == 1
        assert handoffs[0].phase == SA_CONSULT_PHASE

    async def test_multiple_importants_post_multiple_handoffs(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        comments = [
            _comment(severity="important", reviewer="reviewer-error-handling"),
            _comment(severity="important", reviewer="reviewer-test-adequacy"),
        ]
        await apply_severity_disposition(
            comments, ticket, tickets, coord, threads=threads
        )
        entries = await threads.for_ticket(ticket.id)
        handoffs = [e for e in entries if isinstance(e, Handoff)]
        assert len(handoffs) == 2

    async def test_important_without_threads_records_but_no_handoff(
        self, tmp_path: Path
    ) -> None:
        """Without a ThreadStore, the comment is still classified for
        caller introspection but no Handoff is posted."""
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [_comment(severity="important")],
            ticket,
            tickets,
            coord,
            threads=None,
        )
        assert len(result.consulted_sa) == 1
        entries = await threads.for_ticket(ticket.id)
        assert not [e for e in entries if isinstance(e, Handoff)]

    async def test_important_does_not_change_ticket_status(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        await apply_severity_disposition(
            [_comment(severity="important")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status != TicketStatus.FAILED


# ---- notable → DEFERRED queue -------------------------------------------


class TestNotableDisposition:
    async def test_notable_calls_coordinator_defer_ticket(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [_comment(severity="notable")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        assert len(result.deferred) == 1
        deferred_entries = coord.list_deferred()
        assert len(deferred_entries) == 1
        assert deferred_entries[0].ticket_id == ticket.id
        assert deferred_entries[0].reason == "reviewer-notable"

    async def test_notable_without_coordinator_records_no_action(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, _ = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [_comment(severity="notable")],
            ticket,
            tickets,
            coordinator=None,
            threads=threads,
        )
        assert len(result.deferred) == 1

    async def test_notable_does_not_change_ticket_status(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        await apply_severity_disposition(
            [_comment(severity="notable")],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status != TicketStatus.FAILED

    async def test_notable_summary_mentions_count(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        await apply_severity_disposition(
            [
                _comment(
                    severity="notable",
                    reviewer="reviewer-pattern-conformance",
                ),
                _comment(
                    severity="notable",
                    reviewer="reviewer-pattern-conformance",
                ),
                _comment(severity="notable", reviewer="reviewer-test-adequacy"),
            ],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        deferred = coord.list_deferred()
        assert len(deferred) == 1
        assert "3 notable" in deferred[0].notes


# ---- mixed severities ---------------------------------------------------


class TestMixedSeverities:
    async def test_critical_and_important_and_notable_all_dispatched(
        self, tmp_path: Path
    ) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [
                _comment(severity="critical"),
                _comment(severity="important"),
                _comment(severity="notable"),
            ],
            ticket,
            tickets,
            coord,
            threads=threads,
        )
        assert len(result.blocked_by) == 1
        assert len(result.consulted_sa) == 1
        assert len(result.deferred) == 1

        fresh = await tickets.get(ticket.id)
        assert fresh is not None
        assert fresh.status == TicketStatus.FAILED

        entries = await threads.for_ticket(ticket.id)
        assert any(
            isinstance(e, Handoff) and e.phase == SA_CONSULT_PHASE for e in entries
        )

        assert len(coord.list_deferred()) == 1

    async def test_empty_input_returns_empty_result(self, tmp_path: Path) -> None:
        tickets, threads, coord = await _build_stores(tmp_path)
        ticket = await _make_ticket(tickets)
        result = await apply_severity_disposition(
            [], ticket, tickets, coord, threads=threads
        )
        assert result.blocked_by == []
        assert result.consulted_sa == []
        assert result.deferred == []
