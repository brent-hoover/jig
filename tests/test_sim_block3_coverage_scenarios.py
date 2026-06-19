"""Block 3 (federation) coverage scenarios.

Closes 5 of the 9 reviewer-kind gaps the v2-review's coverage
report flagged:

- ``reviewer-architectural`` (LLM)
- ``reviewer-performance`` (LLM)
- ``reviewer-cross-cutting-policy`` (mechanical)
- ``reviewer-intent-compliance`` (mechanical)
- ``reviewer-spec-compliance`` (mechanical)

Each scenario authors a tracer-bullet project + drives
``invoke_federation_execution`` so the reviewer participates in the
dispatch. LLM reviewers inject canned comments via the mocked
orchestrator; mechanical reviewers run for real against the authored
artifacts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


pytestmark = pytest.mark.sim_smoke


SCENARIOS_DIR = Path(__file__).parent / "scenarios"


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


@pytest.mark.parametrize(
    "scenario_id",
    [
        "bones-with-cross-cutting-policy",
        "bones-with-spec-compliance",
        "bones-with-intent-compliance",
        "bones-with-architectural-reviewer",
        "bones-with-performance-reviewer",
    ],
)
@pytest.mark.asyncio
async def test_coverage_scenario_passes(tmp_path: Path, scenario_id: str) -> None:
    """Each Block 3 coverage scenario runs end-to-end in mock mode."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.scenario.yaml")
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.parametrize(
    "scenario_id, expected_tag",
    [
        ("bones-with-cross-cutting-policy", "reviewer-cross-cutting-policy"),
        ("bones-with-spec-compliance", "reviewer-spec-compliance"),
        ("bones-with-intent-compliance", "reviewer-intent-compliance"),
        ("bones-with-architectural-reviewer", "reviewer-architectural"),
        ("bones-with-performance-reviewer", "reviewer-performance"),
    ],
)
def test_scenario_claims_expected_coverage_tag(
    scenario_id: str, expected_tag: str
) -> None:
    """Each scenario claims the gap-tag it's intended to close."""
    scenario = load_scenario(SCENARIOS_DIR / f"{scenario_id}.scenario.yaml")
    assert expected_tag in scenario.coverage_tags, (
        f"scenario {scenario_id!r} must claim coverage_tag "
        f"{expected_tag!r} to close the v2-review gap"
    )
