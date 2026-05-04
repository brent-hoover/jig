"""Bones-with-fast-and-shippy scenario end-to-end (Track H MVP, mock mode).

Same shape as ``test_sim_bones_with_planner_scenario.py`` but driven
by the fast-and-shippy persona. Validates the persona's profile in
the scripted scenario produces a clean spine pass + the L0 ticket
transitions cleanly to resolved without re-opening (the persona's
``confirm_eagerly`` policy never triggers a refusal-then-confirm
loop in the scripted YAML).
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
    / "bones-with-fast-and-shippy.scenario.yaml"
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
async def test_bones_with_fast_and_shippy_passes_in_mock_mode(tmp_path: Path):
    """The fast-and-shippy persona scenario runs end-to-end."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_fast_and_shippy_uses_correct_persona(tmp_path: Path):
    """Spot-check that the scenario actually claims the fast-and-shippy persona."""
    scenario = load_scenario(SCENARIO_PATH)
    assert scenario.persona == "fast-and-shippy"
