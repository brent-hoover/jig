"""Tests for the generalist reviewer — single-pass review for small workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.persistence import load_role, load_workflow
from jig.reviewers.dispatch import (
    GENERALIST_REVIEWER_ID,
    _LLM_REVIEWER_IDS,
    _REVIEWER_ID_TO_ROLE_FILE,
    known_llm_reviewer_ids,
)


class TestRoleConfig:
    def test_loads(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer-generalist")
        assert cfg.role == "reviewer-generalist"

    def test_loads_via_filename_lookup_too(self, tmp_path: Path) -> None:
        # underscored filename → hyphenated role id
        cfg = load_role(tmp_path, "reviewer_generalist")
        assert cfg.role == "reviewer-generalist"

    def test_has_required_tools(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer-generalist")
        # Same tool surface as other judgment reviewers.
        assert "reviewer_post_comment" in cfg.allowed_tools
        assert "mark_finding_resolved" in cfg.allowed_tools
        assert cfg.strict_tools is True

    def test_prompt_covers_all_five_axes(self, tmp_path: Path) -> None:
        cfg = load_role(tmp_path, "reviewer-generalist")
        p = cfg.phase_prompt.lower()
        # The whole point of this reviewer is covering all five specialist
        # axes in one pass. Pin that the prompt actually mentions each.
        assert "pattern" in p
        assert "error" in p
        assert "architectural" in p or "boundaries" in p
        assert "performance" in p
        assert "security" in p


class TestDispatchRegistration:
    def test_in_llm_reviewer_ids(self) -> None:
        assert GENERALIST_REVIEWER_ID in _LLM_REVIEWER_IDS

    def test_known_to_workflow_validator(self) -> None:
        # known_llm_reviewer_ids drives load_workflow's reviewer-name
        # validation; if the generalist isn't in there, workflows that
        # reference it will fail to load.
        assert GENERALIST_REVIEWER_ID in known_llm_reviewer_ids()

    def test_mapped_to_role_file(self) -> None:
        assert _REVIEWER_ID_TO_ROLE_FILE[GENERALIST_REVIEWER_ID] == (
            "reviewer_generalist"
        )


@pytest.mark.parametrize(
    "workflow_name",
    ["feature-xs", "feature-s", "bugfix", "refactor", "migration", "perf"],
)
def test_small_workflows_include_generalist_review_phase(
    tmp_path: Path, workflow_name: str
) -> None:
    """Every workflow that previously had no review phase must now have
    one that uses the generalist."""
    wf = load_workflow(tmp_path, workflow_name)
    review_phases = [p for p in wf.phases if p.role == "review"]
    assert review_phases, f"{workflow_name} must include a review phase"
    assert any(GENERALIST_REVIEWER_ID in p.reviewers for p in review_phases), (
        f"{workflow_name}'s review phase must dispatch reviewer-generalist"
    )


def test_default_workflow_unchanged(tmp_path: Path) -> None:
    """The default workflow's specialist federation must not adopt the
    generalist — that would defeat the whole 'specialists for big tickets,
    generalist for small' design."""
    wf = load_workflow(tmp_path, "default")
    end_review = [p for p in wf.phases if p.role == "review" and p.name == "review"]
    assert end_review, "default must have an end-of-ticket review phase"
    assert GENERALIST_REVIEWER_ID not in end_review[0].reviewers


def test_review_phase_placement_between_implement_and_validate(
    tmp_path: Path,
) -> None:
    """Insertion order matters: the generalist review must run AFTER
    implement (so it has code to review) and BEFORE validate (so a
    blocking finding routes back to dev, not after validate passes)."""
    for wf_name in ["feature-xs", "feature-s", "bugfix", "refactor", "migration", "perf"]:
        wf = load_workflow(tmp_path, wf_name)
        names = [p.name for p in wf.phases]
        if "implement" in names and "review" in names and "validate" in names:
            assert (
                names.index("implement")
                < names.index("review")
                < names.index("validate")
            ), f"{wf_name}: review must sit between implement and validate"
