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
        f"project:\n"
        f"  id: test-id\n"
        f"  name: test\n"
        f"  path: {tmp_path}\n"
    )

    await orch.reload()
    assert orch.is_configured is True
    assert orch.tickets is not None
    await orch.shutdown()


@pytest.mark.asyncio
async def test_orchestrator_reload_is_noop_when_already_configured(tmp_path: Path):
    """reload() is a no-op when the orchestrator is already in configured mode."""
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir(parents=True, exist_ok=True)
    (jig_dir / "config.yaml").write_text(
        f"project:\n"
        f"  id: test-id\n"
        f"  name: test\n"
        f"  path: {tmp_path}\n"
    )

    orch = Orchestrator(project_path=tmp_path, emitter=EventEmitter())
    await orch.startup()
    assert orch.is_configured is True

    tickets_before = orch.tickets
    await orch.reload()
    # Store object unchanged — no re-init
    assert orch.tickets is tickets_before
    await orch.shutdown()
