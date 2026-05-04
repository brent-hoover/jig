"""Bones walking-skeleton scenario end-to-end (Track H5, bones).

This is the bones milestone test: when this passes in mock mode, the
v2 walking skeleton is real. The synthetic operator drives the scripted
``bones-walking-skeleton.scenario.yaml`` through every layer the bones
scope exercises (PO L0/L3, manual L1/L2/SA artifacts, build-plan +
Coordinator dispatch, mock dev commit, contract-compliance reviewer)
and confirms the assertions hold.

Run target: under 10 seconds, no LLM calls, no network.

Real mode (``jig sim run --real``) is operator-invoked and not part
of CI — see ``jig.sim.cli``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario


# Module-level marker — every test in this file runs as part of the
# smoke tier (see pyproject.toml [tool.pytest.ini_options].markers).
pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-walking-skeleton.scenario.yaml"
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
async def test_bones_walking_skeleton_passes_in_mock_mode(tmp_path: Path):
    """The bones milestone: walking-skeleton scenario runs end-to-end.

    On failure, ``report.failure_summary()`` enumerates every failed
    step + assertion with the step kind and the assertion's diagnostic
    detail. That output is the operator's first signal when the spine
    breaks.
    """
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_scenario_emits_simulator_tagged_events(tmp_path: Path):
    """All analytics from a sim run carry simulator=True (per H6 isolation).

    Real-project consumers filter to simulator=False by default; this
    test guards the tag actually lands on every event.
    """
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    assert report.captured_events, "scenario produced no analytics events"
    assert all(e.simulator for e in report.captured_events)


@pytest.mark.asyncio
async def test_bones_scenario_writes_every_canonical_artifact(tmp_path: Path):
    """Spot-check every bones-scope artifact landed in the canonical place.

    Catches regressions where a step succeeds but writes to the wrong
    path — e.g. a typo in spec_loader that the per-step assertion
    might miss.
    """
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    expected = [
        ".jig/spec/project.md",
        ".jig/spec/project.structured.yaml",
        ".jig/spec/suites.yaml",
        ".jig/spec/suites/catalog/brief.md",
        ".jig/spec/suites/catalog/spec.structured.yaml",
        ".jig/spec/architecture.yaml",
        ".jig/spec/modules/catalog-ingest/contracts.yaml",
        ".jig/plan/build-plan.yaml",
    ]
    missing = [p for p in expected if not (tmp_path / p).is_file()]
    assert not missing, f"missing artifacts: {missing}"
