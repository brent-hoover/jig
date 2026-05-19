"""Tests for ``build_verify_bundle`` — fix-loop-context step 4.

The verify bundle is what reviewers running on cycle 2+ see in their
prompt. It captures the full state of every finding so far so they
can verify or re-flag with full context.
"""

from __future__ import annotations

from jig.fix_loop_bundle import build_verify_bundle
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAck


def _c(
    *,
    reviewer: str = "reviewer-pattern-conformance",
    type_: ReviewerCommentType = ReviewerCommentType.PATTERN_DIVERGENCE,
    file: str = "src/main.py",
    line: int = 10,
    prose: str = "p",
    cycle: int = 0,
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
    *, finding_id: str, kind: str, author: str = "dev", cycle: int = 0, prose: str = "p"
) -> FindingAck:
    return FindingAck(
        ticket_id="t-1",
        finding_id=finding_id,
        kind=kind,
        author=author,
        cycle=cycle,
        prose=prose,
    )


def test_no_acks_returns_none() -> None:
    """Cycle-1 reviewers see no verify bundle even when comments exist."""
    out = build_verify_bundle(
        all_comments=[_c(prose="cycle 1 finding")],
        all_acks=[],
    )
    assert out is None


def test_no_comments_returns_none() -> None:
    out = build_verify_bundle(all_comments=[], all_acks=[])
    assert out is None


def test_status_open_when_no_claim() -> None:
    comments = [_c(file="src/a.py", prose="raised but unaddressed")]
    # Need at least one ack for the gate; use an unrelated finding.
    acks = [_ack(finding_id="RC-99", kind="addressed")]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    assert out is not None
    # RC-1 (the actual comment's id) has no ack → open
    rc1 = next(f for f in out["findings"] if f["finding_id"] == "RC-1")
    assert rc1["status"] == "open"
    assert rc1["dev_claim"] is None


def test_status_addressed_when_dev_claimed_but_not_resolved() -> None:
    comments = [_c(prose="raised")]
    acks = [_ack(finding_id="RC-1", kind="addressed", cycle=1, prose="fixed")]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    finding = out["findings"][0]
    assert finding["status"] == "addressed"
    assert finding["dev_claim"]["prose"] == "fixed"
    assert finding["dev_claim"]["cycle"] == 1


def test_status_resolved_when_reviewer_confirmed() -> None:
    comments = [_c(prose="raised")]
    acks = [
        _ack(finding_id="RC-1", kind="addressed", cycle=1, prose="fixed"),
        _ack(
            finding_id="RC-1",
            kind="resolved",
            author="reviewer-pattern-conformance",
            cycle=2,
            prose="confirmed",
        ),
    ]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    assert out["findings"][0]["status"] == "resolved"


def test_status_reraised_when_dev_claim_did_not_hold() -> None:
    """Lifecycle addressed → reraised: dev claimed fix, orchestrator
    auto-reraised because the next federation pass saw the same
    signature. Reviewer must see status=reraised (not addressed),
    otherwise the prompt suggests verifying a claim that's already
    been refuted."""
    comments = [_c(cycle=0, prose="raised")]
    acks = [
        _ack(finding_id="RC-1", kind="addressed", cycle=0, prose="claimed fixed"),
        _ack(
            finding_id="RC-1",
            kind="reraised",
            author="orchestrator",
            cycle=1,
            prose="still present in cycle 1 diff",
        ),
    ]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    finding = out["findings"][0]
    assert finding["status"] == "reraised"
    # dev_claim must still surface — the reviewer needs to see what
    # the dev claimed even though the orchestrator already refuted it.
    assert finding["dev_claim"]["prose"] == "claimed fixed"


def test_status_resolved_wins_even_after_reraise() -> None:
    """Resolved is terminal. If a reraise was later resolved, the
    status sticks at resolved, not the latest kind."""
    comments = [_c(cycle=0, prose="raised")]
    acks = [
        _ack(finding_id="RC-1", kind="addressed", cycle=0),
        _ack(finding_id="RC-1", kind="reraised", author="orchestrator", cycle=1),
        _ack(finding_id="RC-1", kind="addressed", cycle=2),
        _ack(
            finding_id="RC-1",
            kind="resolved",
            author="reviewer-pattern-conformance",
            cycle=3,
            prose="this time it stuck",
        ),
    ]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    assert out["findings"][0]["status"] == "resolved"


def test_original_prose_preserved_across_rephrasing() -> None:
    """First appearance of a signature owns the prose, not later
    re-phrasings. The reviewer should see the original framing."""
    comments = [
        _c(cycle=0, prose="ORIGINAL"),
        _c(cycle=2, prose="re-phrased"),
    ]
    acks = [_ack(finding_id="RC-1", kind="addressed", cycle=1, prose="x")]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    assert out["findings"][0]["original_prose"] == "ORIGINAL"


def test_latest_addressed_claim_wins() -> None:
    """When dev marks addressed across multiple cycles, the latest
    claim's prose is shown (most actionable for the reviewer)."""
    comments = [_c()]
    acks = [
        _ack(finding_id="RC-1", kind="addressed", cycle=1, prose="first attempt"),
        _ack(finding_id="RC-1", kind="addressed", cycle=2, prose="second attempt"),
    ]
    out = build_verify_bundle(all_comments=comments, all_acks=acks)
    assert out["findings"][0]["dev_claim"]["prose"] == "second attempt"
    assert out["findings"][0]["dev_claim"]["cycle"] == 2
