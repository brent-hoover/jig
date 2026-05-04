"""Bones-with-VD scenario end-to-end (Track D MVP, mock mode).

Same shape as the other bones+ scenario tests; gates the VD MVP slice
(frontend.yaml + wireframe + visual_compliance reviewer) integrating
with the rest of the v2 spine. The mock dev commit body now includes
the wireframe screen-id so the visual_compliance reviewer's reference
check passes.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-vd.scenario.yaml"
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
async def test_bones_with_vd_passes_in_mock_mode(tmp_path: Path):
    """Full VD scenario runs end-to-end through mock-mode dev."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_vd_writes_frontend_and_wireframe(
    tmp_path: Path,
) -> None:
    """Spot-check the VD-authored artifacts landed on disk."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    frontend_yaml = tmp_path / ".jig" / "spec" / "frontend.yaml"
    wireframe_html = (
        tmp_path / ".jig" / "spec" / "wireframes" / "post-a-job.html"
    )
    assert frontend_yaml.is_file()
    assert wireframe_html.is_file()
    body = wireframe_html.read_text()
    # Round-trip preserved the meta block + structural markup.
    assert "wireframe-meta" in body
    assert "Post a job" in body


@pytest.mark.asyncio
async def test_bones_with_vd_visual_compliance_no_critical(
    tmp_path: Path,
) -> None:
    """visual_compliance reviewer ran AND returned no critical comments."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    # The run_reviewer step caches comments under each reviewer's id.
    vc = report.captured_reviewer_comments.get("visual-compliance")
    assert vc is not None, (
        "visual-compliance reviewer was never invoked — Track D MVP "
        "wiring (driver.run_reviewer + ticket.visual_references) is "
        "broken."
    )
    criticals = [c for c in vc if c.severity == "critical"]
    assert criticals == [], (
        f"visual-compliance returned critical comments: {criticals!r}"
    )
