"""Bones-with-cascade-risk-low-override scenario end-to-end (Track C Final, mock mode)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario
from jig.spec_loader import load_architecture


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-cascade-risk-low-override.scenario.yaml"
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
async def test_bones_with_cascade_risk_low_override_passes_in_mock_mode(
    tmp_path: Path,
):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_cascade_risk_low_override_persists_flag(tmp_path: Path):
    """The categorization module ends up with cascade_risk_low=true."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    arch = load_architecture(tmp_path)
    cat = next(m for m in arch.modules if m.id == "categorization")
    assert cat.cascade_risk_low is True
    assert cat.cascade_risk_low_rationale is not None
    # Sibling module must NOT be flagged so we know the flag is targeted.
    cat_ingest = next(m for m in arch.modules if m.id == "catalog-ingest")
    assert cat_ingest.cascade_risk_low is False
