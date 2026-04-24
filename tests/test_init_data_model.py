"""Data model extensions for the init workflow."""
from pathlib import Path

import pytest

from jig.persistence import load_role
from jig.spec_generator import Gap
from jig.store.tickets import TicketStore
from jig.thread import Note, SystemEvent
from jig.ticket import Ticket, WorkType


def test_worktype_has_brief_and_architecture():
    assert WorkType("brief") == WorkType.BRIEF
    assert WorkType("architecture") == WorkType.ARCHITECTURE


def test_system_event_accepts_new_event_types():
    for et in (
        "spec_generated",
        "spec_gaps_reported",
        "sa_skipped",
        "scaffold_applied",
    ):
        ev = SystemEvent(
            ticket_id="t1",
            author="cli",
            event_type=et,  # type: ignore[arg-type]
            content="",
        )
        assert ev.event_type == et


def test_note_carries_payload():
    n = Note(
        ticket_id="brief",
        author="spec-generator",
        text="gaps",
        payload={"gaps": [{"kind": "missing", "severity": "blocking"}]},
    )
    assert n.payload["gaps"][0]["kind"] == "missing"


def test_note_payload_defaults_empty():
    n = Note(ticket_id="t", author="u", text="hi")
    assert n.payload == {}


def test_gap_model_round_trip():
    g = Gap(
        kind="contradiction",
        location="Non-goals",
        description="Stated non-goal contradicts planned capability X.",
        severity="blocking",
    )
    assert g.suggested_question is None
    dumped = g.model_dump()
    assert dumped["kind"] == "contradiction"
    Gap.model_validate(dumped)


def test_po_role_loads():
    cfg = load_role(Path("/nonexistent-project"), "po")
    assert cfg.role == "po"
    assert "Product Owner" in cfg.phase_prompt or "PO" in cfg.phase_prompt
    assert "brief_set_section" in cfg.allowed_tools
    assert "po_finish_brief" in cfg.allowed_tools


def test_sa_role_loads():
    cfg = load_role(Path("/nonexistent-project"), "sa")
    assert cfg.role == "sa"
    assert "arch_set_field" in cfg.allowed_tools
    assert "sa_propose_scaffold" in cfg.allowed_tools
    # SA must NOT have brief_* tools per REQ-INIT-SA.4
    assert not any(t.startswith("brief_") for t in cfg.allowed_tools)


def test_spec_generator_role_loads():
    cfg = load_role(Path("/nonexistent-project"), "spec-generator")
    assert cfg.role == "spec-generator"
    assert "spec_publish" in cfg.allowed_tools
    assert "spec_report_gaps" in cfg.allowed_tools
    # No conversational tools per REQ-INIT-SPECGEN.9
    assert "ask_question" not in cfg.allowed_tools


@pytest.mark.asyncio
async def test_create_reserved_ticket_uses_given_id(tmp_path):
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    ticket = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="Project brief",
        created_by="cli",
    )
    tid = await store.create(ticket)
    assert tid == "brief"
    loaded = await store.get("brief")
    assert loaded is not None
    assert loaded.work_type == WorkType.BRIEF


@pytest.mark.asyncio
async def test_create_reserved_ticket_twice_raises(tmp_path):
    store = TicketStore(tmp_path / "tickets.jsonl")
    await store.load()
    first = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="t1",
        created_by="cli",
    )
    await store.create(first)
    second = Ticket(
        id="brief",
        work_type=WorkType.BRIEF,
        title="t2",
        created_by="cli",
    )
    with pytest.raises(ValueError, match="already exists"):
        await store.create(second)
