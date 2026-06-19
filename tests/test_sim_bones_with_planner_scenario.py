"""Bones-with-planner scenario end-to-end (Track F MVP, mock mode).

Same shape as ``test_sim_bones_scenario.py`` but the build-plan step
goes through the v2 Planner PM agent path (``invoke_plan_finalize``)
rather than the operator hand-write helper. This proves the Planner
integrates end-to-end with the rest of the v2 spine in mock mode.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = Path(__file__).parent / "scenarios" / "bones-with-planner.scenario.yaml"


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
async def test_bones_with_planner_passes_in_mock_mode(tmp_path: Path):
    """The Planner-PM scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_planner_writes_plan_via_agent_path(tmp_path: Path):
    """Spot-check that the Planner-authored plan landed at the canonical path."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    plan_file = tmp_path / ".jig" / "plan" / "build-plan.yaml"
    assert plan_file.is_file()
    body = plan_file.read_text()
    # Project name + the bones ticket id are the load-bearing artifacts
    # of the Planner's output.
    assert "bones-with-planner" in body
    assert "tb-catalog-ingest" in body
