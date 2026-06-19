"""Sim driver step handlers for Track B Final.

Direct unit-style tests for ``invoke_discovery_resume`` and
``invoke_ontology_edit`` step kinds — driving the handlers directly
without going through a full scenario keeps these tests fast + focused
on the handler logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import (
    Scenario,
    ScenarioStep,
    StepKind,
)


def _scenario(steps: list[ScenarioStep], *, tag: str = "discovery-resume") -> Scenario:
    return Scenario(
        id=f"test-{tag}",
        description="test scenario",
        persona="methodical",
        estimated_cost_usd_max=1.0,
        steps=steps,
        coverage_tags=[tag],
    )


def _seed_repo(root: Path) -> None:
    import subprocess

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


# ---- invoke_discovery_resume ---------------------------------------------


@pytest.mark.asyncio
async def test_resume_step_clean_state_no_divergences(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_DISCOVERY_RESUME,
                params={"mutation": "clean", "reconcile_mode": "auto"},
            )
        ]
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_resume_step_stale_journey_auto_reanchors(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_DISCOVERY_RESUME,
                params={"mutation": "stale-journey", "reconcile_mode": "auto"},
            )
        ]
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_resume_step_concurrent_edit_prefer_doc(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_DISCOVERY_RESUME,
                params={"mutation": "concurrent-edit", "reconcile_mode": "prefer-doc"},
            )
        ]
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_resume_step_partial_walk_orphan(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_DISCOVERY_RESUME,
                params={
                    "mutation": "partial-walk-orphan",
                    "reconcile_mode": "abandon-state",
                },
            )
        ]
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_resume_step_unknown_mutation_errors(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_DISCOVERY_RESUME,
                params={"mutation": "totally-unknown"},
            )
        ]
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    # Step itself errored; report should reflect that.
    assert not report.passed
    assert any(
        "invoke_discovery_resume" in (s.error or "")
        or "unknown mutation" in (s.error or "")
        for s in report.step_outcomes
    )


# ---- invoke_ontology_edit -------------------------------------------------


@pytest.mark.asyncio
async def test_ontology_edit_step_seed_then_edit(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={
                    "action": "seed",
                    "term": "blocker",
                    "definition": "x",
                },
            ),
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={
                    "action": "edit",
                    "term": "blocker",
                    "definition": "refined",
                    "examples": ["a", "b"],
                },
            ),
        ],
        tag="ontology-edit",
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_ontology_edit_step_remove_with_replacement(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={
                    "action": "seed",
                    "term": "blocker",
                    "definition": "x",
                },
            ),
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={
                    "action": "seed",
                    "term": "impediment",
                    "definition": "y",
                },
            ),
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={
                    "action": "remove",
                    "term": "blocker",
                    "replacement_term": "impediment",
                },
            ),
        ],
        tag="ontology-edit",
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_ontology_edit_step_find_references(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={
                    "action": "seed",
                    "term": "blocker",
                    "definition": "x",
                },
            ),
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={"action": "find_references", "term": "blocker"},
            ),
        ],
        tag="ontology-edit",
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_ontology_edit_step_unknown_action_errors(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = _scenario(
        [
            ScenarioStep(
                kind=StepKind.INVOKE_ONTOLOGY_EDIT,
                params={"action": "ghost"},
            )
        ],
        tag="ontology-edit",
    )
    report = await Driver().run(scenario, project_root=tmp_path)
    assert not report.passed


# ---- coverage tags --------------------------------------------------------


def test_track_b_final_coverage_tags_canonical():
    """The three new tags must be in the canonical taxonomy."""
    from jig.sim.coverage import CANONICAL_TAGS

    for tag in ("discovery-resume", "ontology-edit", "tui-slash-commands"):
        assert tag in CANONICAL_TAGS
