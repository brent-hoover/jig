"""Tests for the extracted current_phase_index helper."""

from pathlib import Path

import pytest

from jig.models import PhaseConfig, WorkflowConfig
from jig.phase import current_phase_index
from jig.store.threads import ThreadStore
from jig.thread import SystemEvent


@pytest.fixture
async def threads(tmp_path: Path) -> ThreadStore:
    store = ThreadStore(tmp_path / "threads.jsonl")
    await store.load()
    return store


def _workflow(*phase_names: str) -> WorkflowConfig:
    return WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name=n, role="dev") for n in phase_names],
    )


async def test_current_phase_index_zero_when_no_events(threads: ThreadStore):
    wf = _workflow("plan", "build", "verify")
    idx = await current_phase_index(threads, "t-1", wf)
    assert idx == 0


async def test_current_phase_index_skips_succeeded_phases(threads: ThreadStore):
    wf = _workflow("plan", "build", "verify")
    await threads.post(
        SystemEvent(
            ticket_id="t-1",
            author="orchestrator",
            event_type="phase_run",
            content="phase plan: success",
            phase_result="success",
        )
    )
    assert await current_phase_index(threads, "t-1", wf) == 1


async def test_current_phase_index_returns_length_when_all_done(threads: ThreadStore):
    wf = _workflow("plan", "build")
    for name in ("plan", "build"):
        await threads.post(
            SystemEvent(
                ticket_id="t-1",
                author="orchestrator",
                event_type="phase_run",
                content=f"phase {name}: success",
                phase_result="success",
            )
        )
    assert await current_phase_index(threads, "t-1", wf) == 2


async def test_current_phase_index_ignores_non_success_events(threads: ThreadStore):
    wf = _workflow("plan", "build")
    await threads.post(
        SystemEvent(
            ticket_id="t-1",
            author="orchestrator",
            event_type="phase_run",
            content="phase plan: failed",
            phase_result="failed",
        )
    )
    assert await current_phase_index(threads, "t-1", wf) == 0
