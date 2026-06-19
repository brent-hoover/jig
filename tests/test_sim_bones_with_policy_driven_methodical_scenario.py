"""Bones-with-policy-driven-methodical scenario (Track H Final).

Same shape as ``test_sim_bones_with_planner_scenario.py`` but the
scenario YAML has ``policy_driven: true`` so the driver routes
per-step responses through ``jig.sim.policy.apply_policy`` rather
than reading scripted text. Validates that the spine still resolves
end-to-end AND that the report carries one PolicyTurn per step.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.policy import PolicyTurn
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-policy-driven-methodical.scenario.yaml"
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
async def test_policy_driven_methodical_passes_end_to_end(tmp_path: Path):
    """Policy-driven methodical scenario walks the spine cleanly."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    assert scenario.policy_driven is True
    assert scenario.scenario_seed == 1729

    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_policy_driven_methodical_records_policy_turns(tmp_path: Path):
    """One PolicyTurn lands per scenario step."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    assert len(report.policy_turns) == len(scenario.steps)
    for turn in report.policy_turns:
        assert isinstance(turn, PolicyTurn)
        assert turn.persona_id == "methodical"
        assert turn.scenario_seed == 1729
