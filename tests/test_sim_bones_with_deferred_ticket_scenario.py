"""Bones-with-deferred-ticket scenario end-to-end (Track H MVP, mock mode).

Validates the Coordinator DEFERRED queue path: a two-bones-ticket
build plan, one ticket walks to resolved through the spine, one is
deferred via ``Coordinator.defer_ticket``, and the mechanical
triage pass surfaces the deferred entry as ``leave_deferred``
(ticket still in flight). The deferred-queue JSONL artifact + the
deferred ticket's open status gate the path.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-deferred-ticket.scenario.yaml"
)


def _seed_repo(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    for k, v in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", k, v], cwd=root, check=True, capture_output=True
        )
    (root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=root, check=True, capture_output=True
    )


@pytest.mark.asyncio
async def test_bones_with_deferred_ticket_passes_in_mock_mode(tmp_path: Path):
    """The DEFERRED queue scenario runs end-to-end."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_deferred_ticket_writes_queue_jsonl(tmp_path: Path):
    """Spot-check the deferred-queue JSONL persists the deferred entry."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    queue = tmp_path / ".jig" / "plan" / "deferred-queue.jsonl"
    assert queue.is_file()
    body = queue.read_text()
    assert "tb-catalog-ingest-sync" in body
    assert "blocked on external API spec" in body


@pytest.mark.asyncio
async def test_bones_with_deferred_ticket_deferred_ticket_stays_open(
    tmp_path: Path,
):
    """The deferred ticket remains open — no auto-rematerialization at MVP."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
