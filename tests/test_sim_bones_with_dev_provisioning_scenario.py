"""Bones-with-dev-provisioning scenario end-to-end (Track E MVP, mock mode).

Mirrors test_sim_bones_with_mvp_sa_scenario.py but exercises the new
``invoke_dev_provisioning`` step + ``dev_provisioning`` block on the
data store. Verifies the dev manifest lands on disk and the
provisioning lifecycle (CREATE SCHEMA + DROP SCHEMA on success) fires
through the in-memory recorder.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-dev-provisioning.scenario.yaml"
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
async def test_bones_with_dev_provisioning_passes_in_mock_mode(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_dev_provisioning_writes_manifest(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    manifest_path = tmp_path / ".jig" / "dev" / "manifest.yaml"
    assert manifest_path.is_file()
    body = manifest_path.read_text()
    assert "main-db" in body
    assert "shared_namespaced" in body
