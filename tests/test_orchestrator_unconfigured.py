"""Tests for orchestrator unconfigured / reload behaviour.

Covers the bootstrap path introduced with the /init-from-TUI UX:
  - startup() with no config.yaml → unconfigured mode (no stores, no loops)
  - reload() after config.yaml is written → configured mode (stores loaded)
"""

import pytest
from pathlib import Path

from jig.events import EventEmitter
from jig.orchestrator import Orchestrator


@pytest.mark.asyncio
async def test_orchestrator_starts_unconfigured_when_no_project(tmp_path: Path):
    """startup() with no .jig/config.yaml leaves the orchestrator in unconfigured mode."""
    orch = Orchestrator(project_path=tmp_path, emitter=EventEmitter())
    await orch.startup()
    assert orch.is_configured is False
    assert orch.tickets is None
    await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_reload_promotes_to_configured(tmp_path: Path):
    """After config.yaml appears on disk, reload() loads stores and marks configured."""
    orch = Orchestrator(project_path=tmp_path, emitter=EventEmitter())
    await orch.startup()
    assert orch.is_configured is False

    # Simulate /init writing a minimal config.yaml
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir(parents=True, exist_ok=True)
    (jig_dir / "config.yaml").write_text(
        f"project:\n  id: test-id\n  name: test\n  path: {tmp_path}\n"
    )

    await orch.reload()
    assert orch.is_configured is True
    assert orch.tickets is not None
    await orch.shutdown()


@pytest.mark.asyncio
async def test_list_active_agents_returns_in_flight_agents(tmp_path: Path):
    """list_active_agents() projects StallDetector.in_flight_agents into the
    agent_thinking event payload shape so ws_server's agents snapshot can
    populate the TUI sidebar without waiting for the next live heartbeat
    (issue #101)."""
    orch = Orchestrator(project_path=tmp_path, emitter=EventEmitter())

    # Empty when no agents are running.
    assert await orch.list_active_agents() == []

    # Simulate two agents in flight.
    orch._stall_detector.record_agent_start("ticket-abc:test")
    orch._stall_detector.record_agent_start("ticket-xyz:dev")

    agents = await orch.list_active_agents()
    by_role = {a["role"]: a for a in agents}
    assert set(by_role) == {"test", "dev"}
    assert by_role["test"]["ticket_id"] == "ticket-abc"
    assert by_role["dev"]["ticket_id"] == "ticket-xyz"
    assert all(a["active"] is True for a in agents)
    assert all(isinstance(a["elapsed"], int) for a in agents)


@pytest.mark.asyncio
async def test_orchestrator_reload_is_noop_when_already_configured(tmp_path: Path):
    """reload() is a no-op when the orchestrator is already in configured mode."""
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir(parents=True, exist_ok=True)
    (jig_dir / "config.yaml").write_text(
        f"project:\n  id: test-id\n  name: test\n  path: {tmp_path}\n"
    )

    orch = Orchestrator(project_path=tmp_path, emitter=EventEmitter())
    await orch.startup()
    assert orch.is_configured is True

    tickets_before = orch.tickets
    await orch.reload()
    # Store object unchanged — no re-init
    assert orch.tickets is tickets_before
    await orch.shutdown()
