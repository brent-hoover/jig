"""Bones-with-responsive scenario end-to-end (Track D Final, mock mode).

Walks the full sim spine + the new ``invoke_responsive_review``
step. The override HTML in the scenario trips three rules
simultaneously (no viewport, fixed-width attribute, empty
breakpoints) so the scenario verifies the reviewer's per-rule
emission.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-responsive.scenario.yaml"
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
async def test_responsive_violations_were_emitted(tmp_path: Path) -> None:
    """The broken wireframe should trip multiple responsive rules."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    resp = report.captured_reviewer_comments.get("responsive-design")
    assert resp is not None, (
        "ResponsiveDesignReviewer didn't run — sim wiring is broken."
    )
    proses = [c.prose for c in resp]
    # Missing viewport, fixed-width attribute, and empty breakpoints
    # should all surface.
    assert any("viewport" in p.lower() for p in proses), proses
    assert any("fixed-width" in p for p in proses), proses
    assert any("breakpoints" in p for p in proses), proses
