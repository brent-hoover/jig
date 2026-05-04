"""Bones-with-cascade-rejected scenario end-to-end (Track C Final, mock mode)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario
from jig.spec_loader import cascade_audit_path


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-cascade-rejected.scenario.yaml"
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
async def test_bones_with_cascade_rejected_passes_in_mock_mode(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_cascade_rejected_writes_audit_jsonl(tmp_path: Path):
    """The audit log carries both ``proposed`` and ``rejected`` rows."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    audit = cascade_audit_path(tmp_path)
    assert audit.is_file(), f"no audit log at {audit}"
    actions = []
    for line in audit.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        actions.append(json.loads(line)["action"])
    assert "proposed" in actions
    assert "rejected" in actions
