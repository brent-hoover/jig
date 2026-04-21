"""Tests for jig.evaluator_resolver (Phase 5 Task C).

Covers the five doc-10 assignment types plus the natural-sequence
fallback; the ``multi`` composition; unresolvable
``previous_phase_role``; and the ``natural_next_role`` helper.
"""

from __future__ import annotations

from jig.evaluator_resolver import natural_next_role, resolve_evaluator
from jig.models import (
    AutomatedOnlyEvaluator,
    MultiEvaluator,
    PhaseConfig,
    PreviousPhaseRoleEvaluator,
    SpecificHumanEvaluator,
    SpecificRoleEvaluator,
    WorkflowConfig,
)
from jig.thread import Handoff


def _wf(*phases: PhaseConfig) -> WorkflowConfig:
    return WorkflowConfig(name="t", phases=list(phases))


def _handoff(
    *,
    ticket_id: str = "t1",
    phase: str,
    author: str,
    accepted_by: str | None = None,
    state: str = "accepted",
    rejection_reason: str | None = None,
) -> Handoff:
    # I5: Handoff now enforces state/close-field consistency, so
    # rejected handoffs must carry a rejection_reason.
    if state == "rejected" and rejection_reason is None:
        rejection_reason = "rejected in test"
    return Handoff(
        ticket_id=ticket_id,
        author=author,
        phase=phase,
        acceptance_state=state,  # type: ignore[arg-type]
        accepted_by=accepted_by,
        rejection_reason=rejection_reason,
    )


class TestSpecificRole:
    def test_resolves_to_role(self) -> None:
        wf = _wf(PhaseConfig(name="p", role="dev"))
        r = resolve_evaluator(
            spec=SpecificRoleEvaluator(type="specific_role", role="sa"),
            workflow=wf,
            phase_name="p",
            handoff_history=[],
        )
        assert r is not None
        assert r.kind == "role"
        assert r.actors == ["sa"]


class TestSpecificHuman:
    def test_resolves_to_user(self) -> None:
        wf = _wf(PhaseConfig(name="p", role="dev"))
        r = resolve_evaluator(
            spec=SpecificHumanEvaluator(
                type="specific_human", user="alice"
            ),
            workflow=wf,
            phase_name="p",
            handoff_history=[],
        )
        assert r is not None
        assert r.kind == "human"
        assert r.actors == ["alice"]


class TestAutomatedOnly:
    def test_empty_actors(self) -> None:
        wf = _wf(PhaseConfig(name="p", role="dev"))
        r = resolve_evaluator(
            spec=AutomatedOnlyEvaluator(type="automated_only"),
            workflow=wf,
            phase_name="p",
            handoff_history=[],
        )
        assert r is not None
        assert r.kind == "automated"
        assert r.actors == []


class TestPreviousPhaseRole:
    def test_finds_last_accepted_actor(self) -> None:
        wf = _wf(
            PhaseConfig(name="review", role="reviewer"),
            PhaseConfig(name="fix", role="dev"),
        )
        history = [
            _handoff(phase="review", author="dev", accepted_by="alice"),
        ]
        r = resolve_evaluator(
            spec=PreviousPhaseRoleEvaluator(
                type="previous_phase_role", role="reviewer"
            ),
            workflow=wf,
            phase_name="fix",
            handoff_history=history,
        )
        assert r is not None
        assert r.kind == "role"
        assert r.actors == ["alice"]

    def test_skips_rejected_handoffs(self) -> None:
        wf = _wf(
            PhaseConfig(name="review", role="reviewer"),
            PhaseConfig(name="fix", role="dev"),
        )
        history = [
            _handoff(
                phase="review", author="dev",
                state="rejected", accepted_by=None,
            ),
            _handoff(phase="review", author="dev", accepted_by="bob"),
        ]
        r = resolve_evaluator(
            spec=PreviousPhaseRoleEvaluator(
                type="previous_phase_role", role="reviewer"
            ),
            workflow=wf,
            phase_name="fix",
            handoff_history=history,
        )
        assert r is not None
        assert r.actors == ["bob"]

    def test_picks_latest_when_multiple(self) -> None:
        wf = _wf(
            PhaseConfig(name="review", role="reviewer"),
            PhaseConfig(name="fix", role="dev"),
        )
        history = [
            _handoff(phase="review", author="dev", accepted_by="alice"),
            _handoff(phase="review", author="dev", accepted_by="carol"),
        ]
        r = resolve_evaluator(
            spec=PreviousPhaseRoleEvaluator(
                type="previous_phase_role", role="reviewer"
            ),
            workflow=wf,
            phase_name="fix",
            handoff_history=history,
        )
        assert r is not None
        assert r.actors == ["carol"]

    def test_unresolvable_returns_none(self) -> None:
        wf = _wf(
            PhaseConfig(name="review", role="reviewer"),
            PhaseConfig(name="fix", role="dev"),
        )
        # No accepted handoff for "reviewer" phase yet.
        r = resolve_evaluator(
            spec=PreviousPhaseRoleEvaluator(
                type="previous_phase_role", role="reviewer"
            ),
            workflow=wf,
            phase_name="fix",
            handoff_history=[],
        )
        assert r is None

    def test_ignores_other_roles(self) -> None:
        wf = _wf(
            PhaseConfig(name="spec", role="po"),
            PhaseConfig(name="review", role="reviewer"),
            PhaseConfig(name="fix", role="dev"),
        )
        history = [
            _handoff(phase="spec", author="po", accepted_by="alice"),
        ]
        r = resolve_evaluator(
            spec=PreviousPhaseRoleEvaluator(
                type="previous_phase_role", role="reviewer"
            ),
            workflow=wf,
            phase_name="fix",
            handoff_history=history,
        )
        # "alice" accepted a po-phase handoff, not a reviewer one.
        assert r is None


class TestMulti:
    def test_all_specs_resolve(self) -> None:
        wf = _wf(PhaseConfig(name="p", role="dev"))
        r = resolve_evaluator(
            spec=MultiEvaluator(
                type="multi",
                evaluators=[
                    SpecificRoleEvaluator(
                        type="specific_role", role="reviewer"
                    ),
                    SpecificHumanEvaluator(
                        type="specific_human", user="alice"
                    ),
                ],
            ),
            workflow=wf,
            phase_name="p",
            handoff_history=[],
        )
        assert r is not None
        assert r.kind == "multi"
        assert r.actors == ["reviewer", "alice"]
        assert len(r.members) == 2
        assert r.members[0].kind == "role"
        assert r.members[1].kind == "human"

    def test_one_unresolvable_collapses(self) -> None:
        wf = _wf(
            PhaseConfig(name="review", role="reviewer"),
            PhaseConfig(name="fix", role="dev"),
        )
        r = resolve_evaluator(
            spec=MultiEvaluator(
                type="multi",
                evaluators=[
                    SpecificRoleEvaluator(
                        type="specific_role", role="reviewer"
                    ),
                    PreviousPhaseRoleEvaluator(
                        type="previous_phase_role", role="reviewer"
                    ),
                ],
            ),
            workflow=wf,
            phase_name="fix",
            handoff_history=[],
        )
        assert r is None


class TestNoneSpec:
    def test_returns_none(self) -> None:
        wf = _wf(PhaseConfig(name="p", role="dev"))
        assert (
            resolve_evaluator(
                spec=None,
                workflow=wf,
                phase_name="p",
                handoff_history=[],
            )
            is None
        )


class TestNaturalNextRole:
    def test_returns_next_phase_role(self) -> None:
        wf = _wf(
            PhaseConfig(name="a", role="dev"),
            PhaseConfig(name="b", role="reviewer"),
        )
        assert natural_next_role(wf, "a") == "reviewer"

    def test_terminal_phase_returns_none(self) -> None:
        wf = _wf(
            PhaseConfig(name="a", role="dev"),
            PhaseConfig(name="b", role="reviewer"),
        )
        assert natural_next_role(wf, "b") is None

    def test_unknown_phase_returns_none(self) -> None:
        wf = _wf(PhaseConfig(name="a", role="dev"))
        assert natural_next_role(wf, "missing") is None
