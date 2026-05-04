"""Bones-with-cascade-staged scenario end-to-end (Track C Final, mock mode)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from jig.schemas.arch import CascadeProposal, CascadeState
from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario
from jig.spec_loader import cascade_audit_path, cascades_dir


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-cascade-staged.scenario.yaml"
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
async def test_bones_with_cascade_staged_passes_in_mock_mode(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_cascade_staged_resolves_cascade(tmp_path: Path):
    """All stages approved → cascade transitions to ``resolved``."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    cdir = cascades_dir(tmp_path)
    proposals = sorted(cdir.glob("r-cross-shape-*.yaml"))
    assert len(proposals) == 1
    proposal = CascadeProposal.model_validate(yaml.safe_load(proposals[0].read_text()))
    assert proposal.state == CascadeState.RESOLVED
    # 4 contracts split chunk_size=2 → 2 stages, each approved.
    assert len(proposal.stages) == 2
    for s in proposal.stages:
        assert s.approved is True


@pytest.mark.asyncio
async def test_bones_with_cascade_staged_audit_log_complete(tmp_path: Path):
    """Audit log carries proposed + staged + 2x stage_approved + resolved."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    audit = cascade_audit_path(tmp_path)
    actions: list[str] = []
    for line in audit.read_text().splitlines():
        line = line.strip()
        if line:
            actions.append(json.loads(line)["action"])
    assert actions.count("proposed") == 1
    assert actions.count("staged") == 1
    assert actions.count("stage_approved") == 2
    assert actions.count("resolved") == 1
