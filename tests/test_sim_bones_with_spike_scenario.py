"""Bones-with-spike scenario end-to-end (Track C MVP follow-on, mock mode).

Same shape as ``test_sim_bones_with_mvp_sa_scenario.py`` but inserts an
``invoke_risk_and_spike`` step between SA finalize and Planner that
authors a risk, proposes a spike ticket, and completes the spike with
``mitigated`` (the happy path). Cascade-after-impossible is covered by
``test_sim_bones_with_cascade_scenario``.
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
    / "bones-with-spike.scenario.yaml"
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
async def test_bones_with_spike_passes_in_mock_mode(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_spike_transitions_risk_to_mitigated(tmp_path: Path):
    """Risk register reflects the mitigated outcome after spike completes."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    from jig.spec_loader import load_architecture
    arch = load_architecture(tmp_path)
    risk = next(
        (r for r in arch.risks if r.id == "r-shopify-rate-limit"), None
    )
    assert risk is not None
    assert risk.status.value == "mitigated"
    # Spike ticket id is the deterministic ``spike-<risk_id>``.
    assert risk.spike_ticket == "spike-r-shopify-rate-limit"


@pytest.mark.asyncio
async def test_bones_with_spike_does_not_emit_cascade_proposal(tmp_path: Path):
    """``mitigated`` outcome does NOT trigger cascade-proposal generation."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    from jig.spec_loader import cascades_dir
    cdir = cascades_dir(tmp_path)
    if cdir.exists():
        assert not list(cdir.glob("*.yaml")), (
            "mitigated spike should not produce a cascade-proposal artifact"
        )
