"""Bones-with-accessibility scenario end-to-end (Track D Final, mock mode).

Walks the full sim spine + the new ``invoke_accessibility_review``
step. The deliberately-broken wireframe HTML in the scenario should
trip multiple WCAG AA rules (missing img alt, empty button, missing
html lang).
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
    / "bones-with-accessibility.scenario.yaml"
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
async def test_accessibility_violations_were_emitted(tmp_path: Path) -> None:
    """The broken wireframe should trip multiple WCAG rules."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    a11y = report.captured_reviewer_comments.get("accessibility")
    assert a11y is not None, (
        "AccessibilityReviewer didn't run — sim wiring is broken."
    )
    rule_ids = {c.wcag_rule_id for c in a11y if c.wcag_rule_id is not None}
    # The broken HTML hits 1.1.1 (img alt), 2.4.4 (button text), and
    # 3.1.1 (html lang). At minimum we expect the alt + button + lang
    # rules to fire.
    assert any("1.1.1" in r for r in rule_ids), rule_ids
    assert any("2.4.4" in r for r in rule_ids), rule_ids
    assert any("3.1.1" in r for r in rule_ids), rule_ids
