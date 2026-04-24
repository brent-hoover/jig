"""Data model extensions for the init workflow."""
import asyncio
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


@pytest.mark.asyncio
async def test_ticket_store_create_duplicate_id_concurrent(tmp_path):
    """Two concurrent create() calls for the same explicit id must
    produce exactly one success and one ValueError, with exactly one
    insert op in the JSONL log. Guards against the TOCTOU race where
    the existence check happened outside the store's asyncio.Lock.
    """
    path = tmp_path / "tickets.jsonl"
    store = TicketStore(path)
    await store.load()

    def make() -> Ticket:
        return Ticket(
            id="brief",
            work_type=WorkType.BRIEF,
            title="b",
            created_by="cli",
        )

    results = await asyncio.gather(
        store.create(make()),
        store.create(make()),
        return_exceptions=True,
    )
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, ValueError)]
    assert len(successes) == 1, f"expected 1 success, got {results!r}"
    assert len(failures) == 1, f"expected 1 ValueError, got {results!r}"

    # JSONL log should have exactly one insert op for "brief"
    lines = path.read_text().splitlines()
    insert_lines = [
        ln for ln in lines if '"_op": "insert"' in ln and '"_id": "brief"' in ln
    ]
    assert len(insert_lines) == 1, (
        f"expected 1 insert line, got {len(insert_lines)}: {insert_lines!r}"
    )
