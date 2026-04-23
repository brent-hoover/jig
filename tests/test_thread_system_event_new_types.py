"""Verifies new SystemEvent event_types round-trip through pydantic."""

from __future__ import annotations

import pytest

from jig.thread import SystemEvent


def test_phase_start_event_type_accepted() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="phase_start",
        content="spec",
    )
    assert ev.event_type == "phase_start"


def test_phase_end_event_type_accepted() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="phase_end",
        content="success",
    )
    assert ev.event_type == "phase_end"


def test_agent_run_event_type_accepted() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="agent_run",
        content="dev ran 3 turns in 12.4s",
    )
    assert ev.event_type == "agent_run"


def test_unknown_event_type_still_rejected() -> None:
    with pytest.raises(Exception):
        SystemEvent(
            ticket_id="tid-1",
            author="orchestrator",
            event_type="made_up_kind",  # type: ignore[arg-type]
            content="",
        )
