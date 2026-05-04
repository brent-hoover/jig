"""Bones-with-ambivalent-needs-clarification scenario (Track H MVP, mock mode).

Same shape as ``test_sim_bones_with_l1_discovery_scenario.py`` but
driven by the ambivalent persona. Validates the scripted
clarification-loop pattern lands a discovery.md with the
``clarification`` marker and the spine still resolves end-to-end.
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
    / "bones-with-ambivalent-needs-clarification.scenario.yaml"
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
async def test_bones_with_ambivalent_passes_in_mock_mode(tmp_path: Path):
    """The ambivalent persona scenario runs end-to-end."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_ambivalent_uses_correct_persona(tmp_path: Path):
    """Spot-check the scenario claims the ambivalent persona."""
    scenario = load_scenario(SCENARIO_PATH)
    assert scenario.persona == "ambivalent"


@pytest.mark.asyncio
async def test_bones_with_ambivalent_records_clarification_in_discovery(
    tmp_path: Path,
):
    """The clarification marker must land in discovery.md so the
    persona's loop is observable in the artifact, not just the YAML."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    discovery = tmp_path / ".jig" / "spec" / "discovery.md"
    assert discovery.is_file()
    body = discovery.read_text()
    assert "clarification" in body.lower()
