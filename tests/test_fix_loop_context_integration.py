"""End-to-end integration test for fix-loop-context (step 8).

Exercises the full ack lifecycle without spinning up real agents:

1. Cycle 0: reviewer raises a finding (writes to ReviewCommentsStore).
2. Dev marks_addressed via the MCP handler.
3. Cycle 1: orchestrator's `_run_review_phase_federation`-shaped
   workflow re-runs (scripted): the reviewer raises the *same*
   signature again because the dev claim doesn't match reality.
4. compute_reraised_acks writes a "reraised" row.
5. Cycle 2: dev fixes for real; reviewer doesn't re-flag.
6. Reviewer marks_resolved.
7. ``jig story`` shows the full chronology in order.

Asserts on the FindingAcksStore final state — the operator's autopsy
surface — and the story event sequence.
"""

from __future__ import annotations

from pathlib import Path

from jig.finding_ack_mcp import (
    handle_mark_finding_addressed,
    handle_mark_finding_resolved,
)
from jig.fix_loop_bundle import compute_reraised_acks
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAcksStore
from jig.store.review_comments import ReviewCommentsStore
from jig.store.threads import ThreadStore
from jig.story import StorySource, build_story


def _finding(*, prose: str, cycle: int) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.PATTERN_DIVERGENCE,
        severity=Severity.IMPORTANT,
        reviewer="reviewer-pattern-conformance",
        prose=prose,
        file="src/main.py",
        line=23,
        cycle=cycle,
        ticket_id="t-1",
    )


async def test_full_addressed_reraised_resolved_lifecycle(
    tmp_path: Path,
) -> None:
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True)

    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    acks_store = FindingAcksStore(store_dir / "finding_acks.jsonl")
    await acks_store.load()

    # ---- cycle 0: reviewer raises -----------------------------------
    await rc_store.append(
        _finding(prose="_HN_BASE duplicates BASE in api.py", cycle=0)
    )

    # ---- dev claims fixed (RC-1) ------------------------------------
    await handle_mark_finding_addressed(
        project_path=tmp_path,
        ticket_id="t-1",
        finding_id="RC-1",
        author="dev",
        cycle=0,
        how_resolved="renamed _HN_BASE → BASE (incorrectly, still duplicated)",
    )

    # ---- cycle 1: reviewer raises again ------------------------------
    # snapshot pre-federation state for compute_reraised_acks. The
    # MCP handler writes through a fresh store instance, so reload
    # the test's instance to pick up the addressed ack.
    await acks_store.load()
    prior_comments = await rc_store.for_ticket_chronological("t-1")
    prior_acks = await acks_store.for_ticket("t-1")

    new_comment = _finding(prose="_HN_BASE STILL duplicates BASE", cycle=1)
    await rc_store.append(new_comment)

    reraised = compute_reraised_acks(
        prior_comments=prior_comments,
        new_comments=[new_comment],
        prior_acks=prior_acks,
        ticket_id="t-1",
    )
    assert len(reraised) == 1
    for ack in reraised:
        await acks_store.append(ack)

    # ---- dev re-attempts; cycle 2 reviewer doesn't re-flag, resolves -
    await handle_mark_finding_addressed(
        project_path=tmp_path,
        ticket_id="t-1",
        finding_id="RC-1",
        author="dev",
        cycle=2,
        how_resolved="this time really removed _HN_BASE",
    )
    await handle_mark_finding_resolved(
        project_path=tmp_path,
        ticket_id="t-1",
        finding_id="RC-1",
        author="reviewer-pattern-conformance",
        cycle=3,
        confirmation="confirmed; single BASE definition in api.py only",
    )

    # ---- audit trail assertions --------------------------------------
    # Reload to pick up writes made via the MCP handler's separate
    # store instance (same pattern as the orchestrator).
    await acks_store.load()
    final_acks = await acks_store.for_finding("t-1", "RC-1")
    kinds = [a.kind for a in final_acks]
    # Lifecycle: addressed → reraised → addressed → resolved
    assert kinds.count("addressed") == 2
    assert kinds.count("reraised") == 1
    assert kinds.count("resolved") == 1

    # ---- story output shows the full chronology ----------------------
    threads = ThreadStore(store_dir / "threads.jsonl")
    await threads.load()
    events = await build_story("t-1", project_path=tmp_path, threads=threads)
    finding_events = [e for e in events if e.source == StorySource.finding]
    # 2 raised (cycle 0 + cycle 1) + 2 addressed + 1 reraised + 1 resolved = 6
    assert len(finding_events) == 6
    kinds_in_order = [e.kind for e in finding_events]
    assert kinds_in_order.count("finding_raised") == 2
    assert kinds_in_order.count("finding_addressed") == 2
    assert kinds_in_order.count("finding_reraised") == 1
    assert kinds_in_order.count("finding_resolved") == 1


async def test_resolved_then_new_finding_no_reraise(tmp_path: Path) -> None:
    """After a finding is resolved, a later finding with the same
    signature is treated as a new occurrence — not a reraise — even
    though the prior signature exists."""
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True)

    rc_store = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    await rc_store.load()
    acks_store = FindingAcksStore(store_dir / "finding_acks.jsonl")
    await acks_store.load()

    await rc_store.append(_finding(prose="raise", cycle=0))
    await handle_mark_finding_addressed(
        project_path=tmp_path,
        ticket_id="t-1",
        finding_id="RC-1",
        author="dev",
        cycle=0,
        how_resolved="fixed",
    )
    await handle_mark_finding_resolved(
        project_path=tmp_path,
        ticket_id="t-1",
        finding_id="RC-1",
        author="reviewer-pattern-conformance",
        cycle=1,
        confirmation="confirmed",
    )

    # Regression appears in cycle 2.
    await acks_store.load()  # pick up writes via the MCP handlers' instances
    prior_comments = await rc_store.for_ticket_chronological("t-1")
    prior_acks = await acks_store.for_ticket("t-1")
    new_comment = _finding(prose="regression", cycle=2)
    await rc_store.append(new_comment)

    reraised = compute_reraised_acks(
        prior_comments=prior_comments,
        new_comments=[new_comment],
        prior_acks=prior_acks,
        ticket_id="t-1",
    )
    # The resolution stands — no auto-reraise. The new flag is a fresh
    # cycle for handling separately.
    assert reraised == []
