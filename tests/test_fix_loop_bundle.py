"""Tests for ``jig.fix_loop_bundle.build_fix_loop_bundle`` —
fix-loop-context step 3.

Pure-ish builder that turns ``ReviewCommentsStore`` + ``FindingAcksStore``
contents plus a routing decision into the dict the prompt builder
renders.
"""

from __future__ import annotations

from pathlib import Path

from jig.fix_loop_bundle import build_fix_loop_bundle
from jig.models import PhaseConfig, WorkflowConfig
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck


def _wf() -> WorkflowConfig:
    """4-phase workflow: spec, test, implement, review.

    ``writes`` globs route findings: tests/** → test (idx 1),
    src/** → implement (idx 2).
    """
    return WorkflowConfig(
        name="default",
        phases=[
            PhaseConfig(name="spec", role="spec", writes=["docs/spec/**"]),
            PhaseConfig(name="test", role="test", writes=["tests/**"]),
            PhaseConfig(name="implement", role="dev", writes=["src/**"]),
            PhaseConfig(name="review", role="review"),
        ],
    )


def _comment(
    *,
    file: str | None = None,
    severity: Severity = Severity.IMPORTANT,
    cycle: int = 0,
    reviewer: str = "reviewer-pattern-conformance",
    type_: ReviewerCommentType = ReviewerCommentType.PATTERN_DIVERGENCE,
    prose: str = "fixture",
    line: int = 10,
    ticket_id: str = "t-1",
) -> ReviewerComment:
    return ReviewerComment(
        type=type_,
        severity=severity,
        reviewer=reviewer,
        prose=prose,
        file=file,
        line=line,
        cycle=cycle,
        ticket_id=ticket_id,
    )


async def test_empty_comments_yields_empty_bundle(tmp_path: Path) -> None:
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=[],
        all_acks=[],
        worktree_path=tmp_path,
    )
    assert out == {"findings": [], "overflow_count": 0}


async def test_only_notable_yields_empty_bundle(tmp_path: Path) -> None:
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=[_comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)],
        all_acks=[],
        worktree_path=tmp_path,
    )
    assert out["findings"] == []


async def test_filters_to_target_phase_findings(tmp_path: Path) -> None:
    """A finding targeting tests/** should not appear in the bundle
    when target_phase_idx is the implement phase."""
    comments = [
        # routes to test phase (idx 1)
        _comment(file="tests/test_filter.py", cycle=0),
        # routes to implement phase (idx 2)
        _comment(file="src/main.py", cycle=0, prose="impl finding"),
    ]
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=comments,
        all_acks=[],
        worktree_path=tmp_path,
    )
    assert len(out["findings"]) == 1
    assert out["findings"][0]["file"] == "src/main.py"
    assert out["findings"][0]["finding_id"] == "RC-2"


async def test_only_latest_cycle_rendered(tmp_path: Path) -> None:
    comments = [
        _comment(file="src/old.py", cycle=0, prose="cycle 0 finding"),
        _comment(file="src/new.py", cycle=1, prose="cycle 1 finding"),
    ]
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=comments,
        all_acks=[],
        worktree_path=tmp_path,
    )
    assert len(out["findings"]) == 1
    assert out["findings"][0]["prose"] == "cycle 1 finding"


async def test_ack_history_included(tmp_path: Path) -> None:
    comments = [_comment(file="src/main.py", cycle=1, prose="re-flagged")]
    acks = [
        FindingAck(
            ticket_id="t-1",
            finding_id="RC-1",
            kind="addressed",
            author="dev",
            cycle=0,
            prose="claimed fixed",
        ),
        FindingAck(
            ticket_id="t-1",
            finding_id="RC-1",
            kind="reraised",
            author="orchestrator",
            cycle=1,
            prose="still present after dev's commit",
        ),
    ]
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=comments,
        all_acks=acks,
        worktree_path=tmp_path,
    )
    finding = out["findings"][0]
    assert finding["finding_id"] == "RC-1"
    assert len(finding["ack_history"]) == 2
    kinds = [a["kind"] for a in finding["ack_history"]]
    assert kinds == ["addressed", "reraised"]


async def test_rephrased_finding_collapses_to_one_entry(tmp_path: Path) -> None:
    """Two re-phrased comments with the same signature should appear
    once in the bundle (one RC, not two)."""
    comments = [
        _comment(file="src/main.py", cycle=1, prose="phrasing A"),
        _comment(file="src/main.py", cycle=1, prose="phrasing B (same line)"),
    ]
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=comments,
        all_acks=[],
        worktree_path=tmp_path,
    )
    assert len(out["findings"]) == 1


class TestNotableInclusion:
    """Step 2: notables from all cycles appear in the dev bundle."""

    async def test_notable_routed_to_owning_phase_included(
        self, tmp_path: Path
    ) -> None:
        """A notable on tests/** lands in the bundle when the target IS
        the test phase — non-dev phases receive the notables they own."""
        notable = _comment(file="tests/test_x.py", severity=Severity.NOTABLE)
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=1,
            all_comments=[notable],
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert len(out["findings"]) == 1
        assert out["findings"][0]["file"] == "tests/test_x.py"

    async def test_notable_outside_target_phase_ownership_excluded(
        self, tmp_path: Path
    ) -> None:
        """A notable on tests/** must NOT land in the implement-phase
        bundle — it routes to the test phase like blocking findings do."""
        notable = _comment(file="tests/test_x.py", severity=Severity.NOTABLE)
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert out["findings"] == []

    async def test_unowned_notable_falls_back_to_dev_bundle(
        self, tmp_path: Path
    ) -> None:
        """A notable with no file matches no writes glob — _route_one's
        dev fallback keeps it in the dev bundle."""
        notable = _comment(file=None, severity=Severity.NOTABLE)
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert len(out["findings"]) == 1

    async def test_notable_unacked_appears_in_bundle(self, tmp_path: Path) -> None:
        comments = [_comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)]
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=comments,
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=comments,
        )
        assert len(out["findings"]) == 1
        assert out["findings"][0]["severity"] == "notable"

    async def test_notable_from_prior_cycle_appears_even_if_not_latest(
        self, tmp_path: Path
    ) -> None:
        # notable at cycle 0 only; importants at cycle 1 (latest) — different files
        notable = _comment(file="src/notable.py", severity=Severity.NOTABLE, cycle=0)
        important = _comment(
            file="src/main.py", severity=Severity.IMPORTANT, cycle=1, prose="imp"
        )
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable, important],
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        prose_set = {f["prose"] for f in out["findings"]}
        assert "fixture" in prose_set  # notable present despite being from cycle 0
        assert "imp" in prose_set

    async def test_notable_with_addressed_ack_excluded(self, tmp_path: Path) -> None:
        notable = _comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)
        acks = [
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="addressed",
                author="dev",
                cycle=1,
                prose="fixed",
            )
        ]
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=acks,
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert out["findings"] == []

    async def test_notable_with_reject_ack_included(self, tmp_path: Path) -> None:
        notable = _comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)
        acks = [
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="reject",
                author="dev",
                cycle=1,
                prose="disagree",
            )
        ]
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=acks,
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert len(out["findings"]) == 1

    async def test_notable_with_reject_then_resolved_excluded(
        self, tmp_path: Path
    ) -> None:
        notable = _comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)
        acks = [
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="reject",
                author="dev",
                cycle=1,
                prose="disagree",
            ),
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="resolved",
                author="reviewer-generalist",
                cycle=2,
                prose="accepted",
            ),
        ]
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=acks,
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert out["findings"] == []

    async def test_notable_with_reraised_ack_included(self, tmp_path: Path) -> None:
        notable = _comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)
        acks = [
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="addressed",
                author="dev",
                cycle=0,
                prose="claimed fixed",
            ),
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="reraised",
                author="orchestrator",
                cycle=1,
                prose="still present",
            ),
        ]
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=acks,
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert len(out["findings"]) == 1

    async def test_empty_in_scope_notables_no_notables_in_bundle(
        self, tmp_path: Path
    ) -> None:
        notable = _comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=[],  # filtered out by caller
        )
        assert out["findings"] == []

    async def test_notable_only_bundle_nonempty(self, tmp_path: Path) -> None:
        """When there are only notables and no importants, the bundle is
        non-empty so the dev has context for what it is being asked to ack."""
        notable = _comment(file="src/main.py", severity=Severity.NOTABLE, cycle=0)
        out = await build_fix_loop_bundle(
            workflow=_wf(),
            blocked_phase_idx=3,
            target_phase_idx=2,
            all_comments=[notable],
            all_acks=[],
            worktree_path=tmp_path,
            in_scope_notable_comments=[notable],
        )
        assert out["findings"] != []


async def test_overflow_caps_at_30(tmp_path: Path) -> None:
    comments = [
        _comment(file=f"src/f{i}.py", line=1, cycle=0, prose=f"f{i}") for i in range(35)
    ]
    out = await build_fix_loop_bundle(
        workflow=_wf(),
        blocked_phase_idx=3,
        target_phase_idx=2,
        all_comments=comments,
        all_acks=[],
        worktree_path=tmp_path,
    )
    assert len(out["findings"]) == 30
    assert out["overflow_count"] == 5
