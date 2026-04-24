"""Data model extensions for the init workflow."""
from jig.thread import Note, SystemEvent
from jig.ticket import WorkType
from jig.spec_generator import Gap


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
