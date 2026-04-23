"""Tests for ``jig.section_locks`` — phase-scoped spec-section locks.

Phase 5 Task M: spec-write proposals to sections marked
``section_locks.<field>.locked_after_phase=<phase>`` must be refused
once a matching phase has an accepted Handoff on the ticket. These
tests cover the helper layer; the proposal-accept enforcement layer
lives in ``tests/test_proposal_mcp.py`` alongside the other accept-
branch rules.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.section_locks import (
    accepted_handoff_phases,
    locked_section_fields,
    locked_sections_for_ticket,
)
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.ticket import Ticket, WorkType
from jig.work_types import load_work_type_schema


@pytest.fixture
async def tickets(tmp_path: Path) -> TicketStore:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    return store


@pytest.fixture
async def threads(tmp_path: Path) -> ThreadStore:
    store = ThreadStore(tmp_path / "comments.jsonl")
    await store.load()
    return store


async def _post_accepted_handoff(
    threads: ThreadStore, ticket_id: str, phase: str, *, accepted_by: str = "reviewer"
) -> None:
    await threads.post(
        Handoff(
            ticket_id=ticket_id,
            author="dev",
            phase=phase,
            summary=f"{phase} complete",
            acceptance_state="accepted",
            accepted_by=accepted_by,
        )
    )


async def _post_pending_handoff(
    threads: ThreadStore, ticket_id: str, phase: str
) -> None:
    await threads.post(
        Handoff(
            ticket_id=ticket_id,
            author="dev",
            phase=phase,
            summary=f"{phase} in review",
        )
    )


class TestLockedSectionFields:
    def test_reads_locked_after_phase(self, tmp_path: Path) -> None:
        """The shipped feature schema declares two locks; the helper
        surfaces both as ``field → phase``."""
        schema = load_work_type_schema(tmp_path, "feature")
        locks = locked_section_fields(schema)
        # feature.yaml locks behaviors + acceptance_criteria after `spec`.
        assert locks == {
            "behaviors": "spec",
            "acceptance_criteria": "spec",
        }

    def test_empty_when_no_locks(self, tmp_path: Path) -> None:
        """Schemas without ``section_locks`` produce an empty map."""
        # bugfix schema has no section_locks.
        schema = load_work_type_schema(tmp_path, "bugfix")
        assert locked_section_fields(schema) == {}


class TestAcceptedHandoffPhases:
    @pytest.mark.asyncio
    async def test_pending_handoff_not_counted(self, threads: ThreadStore) -> None:
        await _post_pending_handoff(threads, "t-1", "spec")
        phases = await accepted_handoff_phases(threads, "t-1")
        assert phases == set()

    @pytest.mark.asyncio
    async def test_accepted_handoff_counted(self, threads: ThreadStore) -> None:
        await _post_accepted_handoff(threads, "t-1", "spec")
        phases = await accepted_handoff_phases(threads, "t-1")
        assert phases == {"spec"}

    @pytest.mark.asyncio
    async def test_multiple_phases_distinct_tickets(self, threads: ThreadStore) -> None:
        await _post_accepted_handoff(threads, "t-1", "spec")
        await _post_accepted_handoff(threads, "t-1", "implement")
        await _post_accepted_handoff(threads, "t-2", "spec")
        phases = await accepted_handoff_phases(threads, "t-1")
        assert phases == {"spec", "implement"}


class TestLockedSectionsForTicket:
    @pytest.mark.asyncio
    async def test_locked_after_matching_handoff(
        self, tmp_path: Path, tickets: TicketStore, threads: ThreadStore
    ) -> None:
        ticket_id = await tickets.create(
            Ticket(
                id="t-1",
                work_type=WorkType.FEATURE,
                title="build widget",
                created_by="alice",
            )
        )
        await _post_accepted_handoff(threads, ticket_id, "spec")
        locked = await locked_sections_for_ticket(
            tmp_path, threads, ticket_id, WorkType.FEATURE
        )
        assert locked == {
            "behaviors": "spec",
            "acceptance_criteria": "spec",
        }

    @pytest.mark.asyncio
    async def test_unlocked_without_handoff(
        self, tmp_path: Path, tickets: TicketStore, threads: ThreadStore
    ) -> None:
        ticket_id = await tickets.create(
            Ticket(
                id="t-1",
                work_type=WorkType.FEATURE,
                title="build widget",
                created_by="alice",
            )
        )
        locked = await locked_sections_for_ticket(
            tmp_path, threads, ticket_id, WorkType.FEATURE
        )
        assert locked == {}

    @pytest.mark.asyncio
    async def test_handoff_for_unrelated_phase_leaves_unlocked(
        self, tmp_path: Path, tickets: TicketStore, threads: ThreadStore
    ) -> None:
        """An accepted handoff for a phase that doesn't appear in any
        section-lock leaves locks untouched."""
        ticket_id = await tickets.create(
            Ticket(
                id="t-1",
                work_type=WorkType.FEATURE,
                title="build widget",
                created_by="alice",
            )
        )
        await _post_accepted_handoff(threads, ticket_id, "implement")
        locked = await locked_sections_for_ticket(
            tmp_path, threads, ticket_id, WorkType.FEATURE
        )
        assert locked == {}
