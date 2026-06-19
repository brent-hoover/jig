"""Bones-with-vision-diff scenario end-to-end (Track D Final, mock mode).

Walks the full sim spine + the new ``invoke_vision_diff`` step. The
StubVisionProvider returns a canned ``VisionDiffResult`` carrying two
``VisualDifference`` entries; the FullVisualComplianceReviewer should
emit one ``visual-vision-diff`` comment per entry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-vision-diff.scenario.yaml"
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
async def test_scenario_passes_in_mock_mode(tmp_path: Path) -> None:
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_vision_diff_comments_were_emitted(tmp_path: Path) -> None:
    """Two canned diffs → two visual-vision-diff comments."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    vc = report.captured_reviewer_comments.get("visual-compliance")
    assert vc is not None, (
        "FullVisualComplianceReviewer didn't run — vision-diff sim wiring is broken."
    )
    diffs = [c for c in vc if c.type == "visual-vision-diff"]
    assert len(diffs) == 2, (
        f"expected 2 vision-diff comments matching the canned result; "
        f"got {len(diffs)}: {[c.prose for c in diffs]}"
    )
    kinds = {("layout-shift" in c.prose, "color-mismatch" in c.prose) for c in diffs}
    # Both kinds should be present across the two comments.
    assert any(layout for layout, _ in kinds)
    assert any(color for _, color in kinds)
