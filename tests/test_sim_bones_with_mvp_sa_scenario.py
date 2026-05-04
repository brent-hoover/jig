"""Bones-with-MVP-SA scenario end-to-end (Track C MVP commit 4, mock mode).

Same shape as ``test_sim_bones_with_l2_organizer_scenario.py`` but the
SA step goes through the v2 SA MVP incremental authoring path
(``invoke_sa_incremental``) rather than the operator-hand-write
helpers. Proves the SA discovery loop integrates end-to-end with the
rest of the v2 spine in mock mode.
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
    / "bones-with-mvp-sa.scenario.yaml"
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
async def test_bones_with_mvp_sa_passes_in_mock_mode(tmp_path: Path):
    """Full SA-MVP scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_mvp_sa_writes_two_modules(tmp_path: Path):
    """Spot-check that the multi-module SA output landed on disk."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    # Both modules' contracts.yaml exist.
    catalog_contracts = (
        tmp_path / ".jig" / "spec" / "modules" / "catalog-ingest" / "contracts.yaml"
    )
    categorization_contracts = (
        tmp_path / ".jig" / "spec" / "modules" / "categorization" / "contracts.yaml"
    )
    assert catalog_contracts.is_file()
    assert categorization_contracts.is_file()
    # The behavioral contract authored on catalog-ingest survived
    # round-trip through the upsert + finalize.
    body = catalog_contracts.read_text()
    assert "ingest-batch-atomicity" in body


@pytest.mark.asyncio
async def test_bones_with_mvp_sa_resolves_architecture_ticket(tmp_path: Path):
    """``arch_finalize`` resolves the architecture ticket via the shared resolver."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
