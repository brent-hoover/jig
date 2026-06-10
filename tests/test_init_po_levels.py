"""Unit tests for the medium L0–L3 spawn helpers + level resolver
(``jig.init_workflow``), step 2 of the medium-l0-l3-pipeline feature.

These pieces are pure/unwired here — ``classify_resume`` reaches them in
step 3 — so the tests drive them directly with ticket/thread fixtures and a
mocked agent runner (no live agents).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from jig.init_workflow import (
    NextLevel,
    next_incomplete_level,
    run_po_l0_conversation,
    run_po_l1_conversation,
    run_po_l2_conversation,
    run_po_l3_conversation,
)
from jig.store.bus import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff


async def _stores(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    return tickets, threads, memory, bus


async def _handoff(
    threads: ThreadStore, ticket_id: str, phase: str, *, rejected: bool = False
) -> None:
    kwargs = {}
    if rejected:
        kwargs = {"acceptance_state": "rejected", "rejection_reason": "needs rework"}
    await threads.post(Handoff(ticket_id=ticket_id, author="po", phase=phase, **kwargs))


def _write_suites(tmp_path: Path, suite_ids: list[str]) -> None:
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    data = {
        "spec_version": 2,
        "suites": [
            {
                "id": sid,
                "title": sid.title(),
                "summary": f"summary for {sid}",
                "capabilities": [],
            }
            for sid in suite_ids
        ],
    }
    (spec_dir / "suites.yaml").write_text(yaml.safe_dump(data, sort_keys=False))


# --- next_incomplete_level ----------------------------------------------------


async def test_next_level_bare_project_is_l0(tmp_path: Path) -> None:
    _, threads, _, _ = await _stores(tmp_path)
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=0, ticket_id="project", suite_id=None)


async def test_next_level_after_l0_handoff_is_l1(tmp_path: Path) -> None:
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1")
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=1, ticket_id="discovery", suite_id=None)


async def test_next_level_after_l1_handoff_is_l2(tmp_path: Path) -> None:
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1")
    await _handoff(threads, "discovery", "po-l2")
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=2, ticket_id="suites", suite_id=None)


async def test_next_level_after_l2_is_first_pending_suite(tmp_path: Path) -> None:
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1")
    await _handoff(threads, "discovery", "po-l2")
    await _handoff(threads, "suites", "po-l3")
    _write_suites(tmp_path, ["catalog", "billing"])
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    # The BARE suite id, not the "suite-catalog" ticket id.
    assert nxt == NextLevel(level=3, ticket_id="suite-catalog", suite_id="catalog")


async def test_next_level_skips_completed_suite(tmp_path: Path) -> None:
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1")
    await _handoff(threads, "discovery", "po-l2")
    await _handoff(threads, "suites", "po-l3")
    _write_suites(tmp_path, ["catalog", "billing"])
    await _handoff(threads, "suite-catalog", "sa")  # catalog's L3 done
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=3, ticket_id="suite-billing", suite_id="billing")


async def test_next_level_all_done_returns_none(tmp_path: Path) -> None:
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1")
    await _handoff(threads, "discovery", "po-l2")
    await _handoff(threads, "suites", "po-l3")
    _write_suites(tmp_path, ["catalog", "billing"])
    await _handoff(threads, "suite-catalog", "sa")
    await _handoff(threads, "suite-billing", "sa")
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt is None


async def test_next_level_rejected_handoff_does_not_advance(tmp_path: Path) -> None:
    """A rejected L0 handoff means the level needs rework — the resolver must
    keep returning L0, not advance to L1."""
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1", rejected=True)
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=0, ticket_id="project", suite_id=None)


async def test_next_level_pending_after_rejection_advances(tmp_path: Path) -> None:
    """Once a re-run posts a non-rejected handoff (alongside the earlier
    rejected one), the level counts as complete and the resolver advances."""
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1", rejected=True)
    await _handoff(threads, "project", "po-l1")  # the corrected re-handoff
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=1, ticket_id="discovery", suite_id=None)


async def test_next_level_rejected_suite_handoff_stays_on_suite(tmp_path: Path) -> None:
    """A rejected L3 handoff for a suite keeps that suite pending."""
    _, threads, _, _ = await _stores(tmp_path)
    await _handoff(threads, "project", "po-l1")
    await _handoff(threads, "discovery", "po-l2")
    await _handoff(threads, "suites", "po-l3")
    _write_suites(tmp_path, ["catalog", "billing"])
    await _handoff(threads, "suite-catalog", "sa", rejected=True)
    nxt = await next_incomplete_level(project_path=tmp_path, threads=threads)
    assert nxt == NextLevel(level=3, ticket_id="suite-catalog", suite_id="catalog")


# --- spawn helpers ------------------------------------------------------------


@pytest.mark.parametrize(
    "runner, kwargs, expect_ticket, expect_role",
    [
        (run_po_l0_conversation, {}, "project", "po-l0"),
        (run_po_l1_conversation, {}, "discovery", "po-l1"),
        (run_po_l2_conversation, {}, "suites", "po-l2"),
        (run_po_l3_conversation, {"suite_id": "catalog"}, "suite-catalog", "po-l3"),
    ],
)
async def test_spawn_helper_ensures_ticket_and_role(
    tmp_path: Path, runner, kwargs, expect_ticket, expect_role
) -> None:
    """Each helper creates its level ticket and spawns the matching po-l* role."""
    tickets, threads, memory, bus = await _stores(tmp_path)
    if "suite_id" in kwargs:
        _write_suites(tmp_path, ["catalog"])

    captured = {}

    async def _capture(ctx, **_):
        captured["role"] = ctx.role
        captured["ticket_id"] = ctx.ticket.id

    with (
        patch("jig.init_workflow.load_project", return_value=object()),
        patch("jig.init_workflow.load_role", return_value=object()) as load_role,
        patch(
            "jig.init_workflow._run_agent_with_cli_output",
            new=AsyncMock(side_effect=_capture),
        ),
    ):
        await runner(
            project_path=tmp_path,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            **kwargs,
        )

    # Ticket was created with the right id.
    assert await tickets.get(expect_ticket) is not None
    # The matching po-l* role was loaded and carried into the spawn ctx.
    load_role.assert_called_once_with(tmp_path, expect_role)
    assert captured == {"role": expect_role, "ticket_id": expect_ticket}


async def test_l3_helper_seeds_description_from_suite_summary(tmp_path: Path) -> None:
    """run_po_l3_conversation copies the suite's L2 summary onto the ticket."""
    tickets, threads, memory, bus = await _stores(tmp_path)
    _write_suites(tmp_path, ["catalog"])

    with (
        patch("jig.init_workflow.load_project", return_value=object()),
        patch("jig.init_workflow.load_role", return_value=object()),
        patch("jig.init_workflow._run_agent_with_cli_output", new=AsyncMock()),
    ):
        await run_po_l3_conversation(
            project_path=tmp_path,
            tickets=tickets,
            threads=threads,
            memory=memory,
            bus=bus,
            suite_id="catalog",
        )

    ticket = await tickets.get("suite-catalog")
    assert ticket is not None
    assert ticket.description == "summary for catalog"
