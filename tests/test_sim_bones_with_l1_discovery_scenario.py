"""Bones-with-L1-discovery scenario end-to-end (Track B MVP, mock mode).

Same shape as ``test_sim_bones_with_planner_scenario.py`` with an
extra L1 PO ``discovery_finalize`` step inserted between L0 and the
manual L2 hand-write. Proves the L1 PO authoring path lands a valid
``discovery.md`` + structured cache that downstream phases can read
past, end-to-end through the v2 spine in mock mode.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-l1-discovery.scenario.yaml"
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
async def test_bones_with_l1_discovery_passes_in_mock_mode(tmp_path: Path):
    """The L1-discovery scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_l1_discovery_writes_discovery_md(tmp_path: Path):
    """Spot-check that the L1 PO's discovery.md landed at the canonical path."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    md_file = tmp_path / ".jig" / "spec" / "discovery.md"
    assert md_file.is_file()
    body = md_file.read_text()
    # The headings + persona/journey/capability ids that the L1 PO
    # one-shot landed in the synthesized doc.
    assert "## Personas" in body
    assert "## Journeys" in body
    assert "## Capability roster" in body
    assert "{#merchant}" in body
    assert "{#j-merchant-onboarding}" in body
    assert "{#shopify-connect}" in body

    # Structured cache: round-trips back through the schema.
    cache = tmp_path / ".jig" / "spec" / "discovery.structured.yaml"
    data = yaml.safe_load(cache.read_text())
    assert data["project_name"] == "bones-with-l1-discovery"
    assert len(data["personas"]) == 1
    assert data["journeys"][0]["id"] == "j-merchant-onboarding"
