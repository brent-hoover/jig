"""Approval-gate and key-counter correctness (roborev jobs 370-372).

- PROPOSED -> OPEN is reachable only through the dedicated approve path; the
  generic update path (agent MCP, IssueService.update) must reject it, or any
  caller could bypass the operator-only gate.
- The jig-N counter is persisted before the JSONL append, so a failed/crashed
  append leaves a harmless gap rather than reissuing a key.
- PROPOSED is non-terminal for the post-run analyzer, so a project of only
  proposed tickets does not emit project_complete.
"""

from pathlib import Path

import pytest

from jig.orchestrator import _NON_TERMINAL_ANALYZER_STATUSES
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _proposed(title: str = "t") -> Ticket:
    return Ticket(
        work_type=WorkType.FEATURE,
        title=title,
        created_by="u",
        description=TICKET_AC_PLACEHOLDER,
        status=TicketStatus.PROPOSED,
    )


@pytest.mark.asyncio
async def test_generic_update_rejects_proposed_to_open(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(_proposed())

    with pytest.raises(ValueError, match="approv"):
        await store.update(tid, status=TicketStatus.OPEN)

    assert (await store.get(tid)).status is TicketStatus.PROPOSED


@pytest.mark.asyncio
async def test_approve_promotes_proposed_to_open(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(_proposed())

    await store.approve(tid)

    assert (await store.get(tid)).status is TicketStatus.OPEN


@pytest.mark.asyncio
async def test_other_transitions_still_allowed(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(_proposed())

    # PROPOSED -> CLOSED is not the gated transition and stays allowed.
    await store.update(tid, status=TicketStatus.CLOSED)

    assert (await store.get(tid)).status is TicketStatus.CLOSED


@pytest.mark.asyncio
async def test_failed_append_advances_counter(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    first = await store.create(_proposed("first"))
    first_ticket = await store.get(first)
    assert first_ticket.key == "jig-1"

    # Force the append to fail by reusing an existing id. The counter must
    # already have advanced (persist-before-append), so the consumed number
    # becomes a gap rather than being reissued.
    dup = _proposed("dup")
    dup.id = first
    with pytest.raises(ValueError, match="already exists"):
        await store.create(dup)

    third = await store.create(_proposed("third"))
    assert (await store.get(third)).key == "jig-3"


@pytest.mark.asyncio
async def test_approve_rejects_non_proposed(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(_proposed())
    await store.approve(tid)  # PROPOSED -> OPEN

    # Approving again (now OPEN) must not silently re-open / re-approve.
    with pytest.raises(ValueError, match="(?i)proposed"):
        await store.approve(tid)


@pytest.mark.asyncio
async def test_update_has_no_approval_bypass_kwarg(tmp_path: Path) -> None:
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    tid = await store.create(_proposed())

    # A reserved-looking kwarg must not unlock the gate — it is treated as an
    # (unknown) field and the transition is still rejected.
    with pytest.raises((ValueError, Exception)):
        await store.update(tid, status=TicketStatus.OPEN, _allow_approval=True)
    assert (await store.get(tid)).status is TicketStatus.PROPOSED


def test_proposed_is_non_terminal_for_analyzer() -> None:
    assert TicketStatus.PROPOSED in _NON_TERMINAL_ANALYZER_STATUSES
