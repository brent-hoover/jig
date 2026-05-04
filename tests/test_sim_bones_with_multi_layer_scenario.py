"""Bones-with-multi-layer scenario end-to-end (Track F MVP, mock mode).

Same shape as ``test_sim_bones_with_planner_scenario`` but the build
plan has both bones AND mvp layers across one epic, and the
single ``materialize_tickets`` step is replaced by three
``invoke_coordinator_cycle`` calls that exercise the cycle-aware
multi-layer dispatch path.

Cycle 1 → materializes bones; mock dev resolves the bones ticket.
Cycle 2 → advances bones → done, materializes mvp; mock dev resolves it.
Cycle 3 → advances mvp → done, no further materialization.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-multi-layer.scenario.yaml"
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
async def test_bones_with_multi_layer_passes_in_mock_mode(tmp_path: Path):
    """The multi-layer scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_multi_layer_advances_bones_then_mvp(tmp_path: Path):
    """Spot-check that the build plan reflects bones → done → mvp → done."""
    from jig.spec_loader import load_build_plan
    from jig.schemas.plan import LayerStatusEnum

    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    plan = load_build_plan(tmp_path)
    epic = plan.epics[0]
    assert epic.layers.bones.status == LayerStatusEnum.DONE
    assert epic.layers.mvp.status == LayerStatusEnum.DONE
