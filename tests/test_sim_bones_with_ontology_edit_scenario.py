"""Bones-with-ontology-edit scenario end-to-end (Track B Final, mock mode).

Drives the ``bones-with-ontology-edit.scenario.yaml`` walk — seeds two
ontology terms, edits one, finds references, removes one with an
in-place reference rewrite, and verifies the bones spine still lands
green.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-ontology-edit.scenario.yaml"
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
async def test_ontology_edit_scenario_passes_in_mock_mode(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
