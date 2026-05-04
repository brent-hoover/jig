"""Bones-with-L2-organizer scenario end-to-end (Track B4 MVP, mock mode).

Same shape as ``test_sim_bones_with_l1_discovery_scenario.py`` but the
L2 step goes through the v2 L2 PO agent path
(``invoke_l2_finalize``) rather than the operator hand-write helper.
This proves the L2 Suite Organizer integrates end-to-end with the rest
of the v2 spine in mock mode.
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
    / "bones-with-l2-organizer.scenario.yaml"
)


def _seed_repo(root: Path) -> None:
    """Initialize a fixture repo so the worktree-diff machinery has a base."""
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
async def test_bones_with_l2_organizer_passes_in_mock_mode(tmp_path: Path):
    """The L2-organizer scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_l2_organizer_writes_suites_via_agent_path(
    tmp_path: Path,
):
    """Spot-check that the L2-PO-authored suites.yaml landed at the
    canonical path with the agent's payload."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    suites_file = tmp_path / ".jig" / "spec" / "suites.yaml"
    assert suites_file.is_file()
    body = suites_file.read_text()
    # The capability id + suite id are the load-bearing artifacts of
    # the L2 PO's output.
    assert "shopify-connect" in body
    assert "catalog" in body
