"""Tests for ``compute_reraised_acks`` — fix-loop-context step 5.

After each federation pass the orchestrator scans the new cycle's
comments. A comment whose signature was previously addressed (but not
yet resolved) produces an auto-``reraised`` ack — the system-generated
trail entry that flags "the dev claimed to fix this but the reviewer
found it again."
"""

from __future__ import annotations

from jig.fix_loop_bundle import compute_reraised_acks
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck


def _comment(
    *,
    file: str = "src/a.py",
    line: int = 10,
    cycle: int = 0,
    prose: str = "p",
    reviewer: str = "reviewer-pattern-conformance",
    type_: ReviewerCommentType = ReviewerCommentType.PATTERN_DIVERGENCE,
) -> ReviewerComment:
    return ReviewerComment(
        type=type_,
        severity=Severity.IMPORTANT,
        reviewer=reviewer,
        prose=prose,
        file=file,
        line=line,
        cycle=cycle,
        ticket_id="t-1",
    )


def _ack(
    *, finding_id: str, kind: str, cycle: int = 0, author: str = "dev"
) -> FindingAck:
    return FindingAck(
        ticket_id="t-1",
        finding_id=finding_id,
        kind=kind,
        author=author,
        cycle=cycle,
        prose="p",
    )


def test_addressed_then_reraised_writes_ack() -> None:
    prior = [_comment(cycle=0, prose="cycle 0 raise")]
    new = [_comment(cycle=1, prose="cycle 1 re-flag")]
    acks = [_ack(finding_id="RC-1", kind="addressed", cycle=0)]
    out = compute_reraised_acks(
        prior_comments=prior,
        new_comments=new,
        prior_acks=acks,
        ticket_id="t-1",
    )
    assert len(out) == 1
    assert out[0].kind == "reraised"
    assert out[0].finding_id == "RC-1"
    assert out[0].prose == "cycle 1 re-flag"
    # Auto-reraised acks are system-authored; the reviewer's identity
    # is captured by the new comment itself, not by attribution on the
    # synthetic ack row.
    assert out[0].author == "orchestrator"


def test_resolved_then_reflagged_no_reraise() -> None:
    """If a reviewer already confirmed the finding as resolved, a
    later re-flag is NOT auto-reraised — the resolution stands, the
    re-flag becomes a NEW finding cycle for handling separately."""
    prior = [_comment(cycle=0, prose="cycle 0 raise")]
    new = [_comment(cycle=2, prose="cycle 2 re-flag")]
    acks = [
        _ack(finding_id="RC-1", kind="addressed", cycle=0),
        _ack(
            finding_id="RC-1",
            kind="resolved",
            cycle=1,
            author="reviewer-pattern-conformance",
        ),
    ]
    out = compute_reraised_acks(
        prior_comments=prior,
        new_comments=new,
        prior_acks=acks,
        ticket_id="t-1",
    )
    assert out == []


def test_new_finding_no_reraise() -> None:
    """A new signature that wasn't in prior_comments at all isn't a
    reraise — it's a brand new finding."""
    new = [_comment(file="src/new.py", cycle=1, prose="new")]
    out = compute_reraised_acks(
        prior_comments=[],
        new_comments=new,
        prior_acks=[],
        ticket_id="t-1",
    )
    assert out == []


def test_reraise_when_no_addressed_ack() -> None:
    """A finding that was raised previously but never addressed by the
    dev IS re-raised when it shows up again — the reviewer keeps
    flagging it. The reraised ack documents the recurrence."""
    prior = [_comment(cycle=0, prose="cycle 0 raise")]
    new = [_comment(cycle=1, prose="cycle 1 re-flag")]
    # No acks at all.
    out = compute_reraised_acks(
        prior_comments=prior,
        new_comments=new,
        prior_acks=[],
        ticket_id="t-1",
    )
    # The dev never claimed to address it; re-raise to document the
    # recurrence. Operator can tell the difference from the
    # ack-history shape (no addressed claim immediately before).
    assert len(out) == 1
    assert out[0].kind == "reraised"


def test_carries_new_comments_prose() -> None:
    prior = [_comment(cycle=0, prose="original framing")]
    new = [_comment(cycle=1, prose="re-phrased finding")]
    acks = [_ack(finding_id="RC-1", kind="addressed", cycle=0)]
    out = compute_reraised_acks(
        prior_comments=prior,
        new_comments=new,
        prior_acks=acks,
        ticket_id="t-1",
    )
    assert out[0].prose == "re-phrased finding"


def test_reraise_cycle_matches_new_comment() -> None:
    prior = [_comment(cycle=0)]
    new = [_comment(cycle=3, prose="re-flag")]
    acks = [_ack(finding_id="RC-1", kind="addressed", cycle=0)]
    out = compute_reraised_acks(
        prior_comments=prior,
        new_comments=new,
        prior_acks=acks,
        ticket_id="t-1",
    )
    assert out[0].cycle == 3


def test_multiple_findings_independent_handling() -> None:
    prior = [
        _comment(file="src/a.py", cycle=0),
        _comment(file="src/b.py", cycle=0),
    ]
    new = [
        _comment(file="src/a.py", cycle=1),  # reraise
        _comment(file="src/b.py", cycle=1),  # not reraised (resolved)
        _comment(file="src/c.py", cycle=1),  # new finding, no reraise
    ]
    acks = [
        _ack(finding_id="RC-1", kind="addressed", cycle=0),
        _ack(finding_id="RC-2", kind="addressed", cycle=0),
        _ack(finding_id="RC-2", kind="resolved", cycle=1, author="reviewer-x"),
    ]
    out = compute_reraised_acks(
        prior_comments=prior,
        new_comments=new,
        prior_acks=acks,
        ticket_id="t-1",
    )
    assert len(out) == 1
    assert out[0].finding_id == "RC-1"
