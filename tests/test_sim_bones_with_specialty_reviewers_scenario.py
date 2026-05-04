"""Bones-with-specialty-reviewers scenario end-to-end (Track G Final, mock mode).

Extends ``bones-with-mvp-sa`` with a tracer-bullet ticket carrying
``labels: ["touches-auth"]`` so the specialty-reviewer dispatch step
verifies ``reviewer-security`` is selected per
``docs/pm-workflow/design.md`` §"Reviewer federation — selection logic".
Mock-mode only — the specialty reviewer's LLM agent isn't spawned;
this test pins the dispatch contract.
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
    / "bones-with-specialty-reviewers.scenario.yaml"
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
async def test_bones_with_specialty_reviewers_passes_in_mock_mode(
    tmp_path: Path,
) -> None:
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_security_reviewer_selected_for_touches_auth_ticket(
    tmp_path: Path,
) -> None:
    """The dispatch step's pass implies the selection but exercise it
    directly to leave a regression-style assertion in place."""
    from jig.reviewers import SECURITY_REVIEWER_ID, select_reviewers_for_ticket
    from jig.ticket import Ticket, WorkType

    ticket = Ticket(
        id="t-x",
        work_type=WorkType.FEATURE,
        title="x",
        created_by="po",
        labels=["touches-auth"],
    )
    ids = select_reviewers_for_ticket(ticket, project_root=tmp_path)
    assert SECURITY_REVIEWER_ID in ids
