"""Bones-with-quartermaster-feedback scenario end-to-end (Track I Final, mock).

Extends bones-with-multi-layer with two ``invoke_quartermaster_feedback``
steps after the dispatch loop completes. Validates that the feedback
loop integrates with the rest of the v2 spine in mock mode without
requiring an LLM.
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
    / "bones-with-quartermaster-feedback.scenario.yaml"
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
async def test_bones_with_quartermaster_feedback_passes_in_mock_mode(tmp_path: Path):
    """The Final-layer scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_quartermaster_feedback_calibration_shifted(tmp_path: Path):
    """After running the scenario, the on-disk calibration reflects two
    not-useful feedback rows on two different patterns."""
    from jig.quartermaster import get_pattern_calibration

    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    cal = await get_pattern_calibration(tmp_path)
    # Each pattern got one not-useful → threshold default+1.
    assert cal.threshold_for("module_repeated_escalations") == 4
    assert cal.threshold_for("tickets_stalled") == 4
