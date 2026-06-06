"""Tests for the unacked-notable gate — Step 4.

The gate logic is extracted into a pure helper
``jig.orchestrator._unacked_notable_finding_ids`` so it can be tested
without spinning up the full orchestrator.
"""

from __future__ import annotations

from jig.orchestrator import _unacked_notable_finding_ids
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck


def _notable(*, file: str = "src/main.py", line: int = 10, cycle: int = 0,
              prose: str = "n") -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.PATTERN_DIVERGENCE,
        severity=Severity.NOTABLE,
        reviewer="reviewer-generalist",
        prose=prose,
        file=file,
        line=line,
        cycle=cycle,
        ticket_id="t-1",
    )


def _ack(*, finding_id: str, kind: str, cycle: int = 0) -> FindingAck:
    return FindingAck(
        ticket_id="t-1",
        finding_id=finding_id,
        kind=kind,
        author="dev",
        cycle=cycle,
        prose="p",
    )


def test_no_notables_returns_empty() -> None:
    result = _unacked_notable_finding_ids(
        all_comments=[],
        all_acks=[],
        in_scope_notables=[],
    )
    assert result == []


def test_notable_with_no_ack_is_unacked() -> None:
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[],
        in_scope_notables=[n],
    )
    assert result == ["RC-1"]


def test_notable_with_addressed_ack_is_satisfied() -> None:
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[_ack(finding_id="RC-1", kind="addressed")],
        in_scope_notables=[n],
    )
    assert result == []


def test_notable_with_resolved_ack_is_satisfied() -> None:
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[_ack(finding_id="RC-1", kind="resolved")],
        in_scope_notables=[n],
    )
    assert result == []


def test_notable_with_reject_ack_is_not_satisfied() -> None:
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[_ack(finding_id="RC-1", kind="reject")],
        in_scope_notables=[n],
    )
    assert result == ["RC-1"]


def test_notable_with_reject_then_resolved_is_satisfied() -> None:
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[
            _ack(finding_id="RC-1", kind="reject", cycle=1),
            _ack(finding_id="RC-1", kind="resolved", cycle=2),
        ],
        in_scope_notables=[n],
    )
    assert result == []


def test_notable_with_reraised_ack_is_not_satisfied() -> None:
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[
            _ack(finding_id="RC-1", kind="addressed", cycle=0),
            _ack(finding_id="RC-1", kind="reraised", cycle=1),
        ],
        in_scope_notables=[n],
    )
    assert result == ["RC-1"]


def test_notable_from_prior_cycle_still_checked() -> None:
    """Notable at cycle N not re-emitted at cycle N+1 still blocks
    if unacked — the gate uses full history not latest-cycle."""
    n_old = _notable(file="src/old.py", cycle=0)
    n_new = _notable(file="src/new.py", cycle=1, prose="new")
    # Only n_new appears in in_scope_notables (simulating reviewer only
    # re-emitting one), but we check all in_scope_notables passed in.
    result = _unacked_notable_finding_ids(
        all_comments=[n_old, n_new],
        all_acks=[],
        in_scope_notables=[n_old, n_new],
    )
    assert len(result) == 2


def test_out_of_scope_notable_not_in_result() -> None:
    """Notables not in in_scope_notables (filtered by caller) don't block."""
    n = _notable()
    result = _unacked_notable_finding_ids(
        all_comments=[n],
        all_acks=[],
        in_scope_notables=[],  # filtered out by _filter_out_of_scope_comments
    )
    assert result == []
