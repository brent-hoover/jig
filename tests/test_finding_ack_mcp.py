"""Tests for ``jig.finding_ack_mcp`` — fix-loop-context step 2.

Two handlers: ``handle_mark_finding_addressed`` (called by dev / test /
document / validate roles) and ``handle_mark_finding_resolved`` (called
by judgment-reviewer roles). Both validate the ``finding_id`` resolves
against the ticket's current ``ReviewCommentsStore`` contents, persist
a ``FindingAck`` row, and short-circuit on same-cycle same-author
repeats so retries don't pollute the log.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.finding_ack_mcp import (
    UnknownFindingError,
    handle_mark_finding_addressed,
    handle_mark_finding_resolved,
)
from jig.reviewers.comment import ReviewerComment, ReviewerCommentType, Severity
from jig.store.finding_acks import FindingAcksStore
from jig.store.review_comments import ReviewCommentsStore


async def _seed_comment(
    project_path: Path,
    *,
    ticket_id: str = "t-1",
    reviewer: str = "reviewer-pattern-conformance",
    file: str = "src/a.py",
    line: int = 10,
) -> None:
    """Seed one reviewer comment so RC-1 exists for handlers to resolve."""
    store_path = project_path / ".jig" / "store" / "review_comments.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = ReviewCommentsStore(store_path)
    await store.load()
    await store.append(
        ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer=reviewer,
            prose="something to address",
            file=file,
            line=line,
            ticket_id=ticket_id,
        )
    )


async def _read_acks(project_path: Path, ticket_id: str = "t-1"):
    acks_store = FindingAcksStore(
        project_path / ".jig" / "store" / "finding_acks.jsonl"
    )
    await acks_store.load()
    return await acks_store.for_ticket(ticket_id)


class TestAddressedHappyPath:
    async def test_writes_addressed_row(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        ack_id = await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="removed the duplication",
        )
        assert ack_id  # store returns a non-empty id

        acks = await _read_acks(tmp_path)
        assert len(acks) == 1
        assert acks[0].kind == "addressed"
        assert acks[0].finding_id == "RC-1"
        assert acks[0].author == "dev"
        assert acks[0].cycle == 1
        assert acks[0].prose == "removed the duplication"


class TestResolvedHappyPath:
    async def test_writes_resolved_row(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        ack_id = await handle_mark_finding_resolved(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="reviewer-pattern-conformance",
            cycle=2,
            confirmation="single definition confirmed",
        )
        assert ack_id

        acks = await _read_acks(tmp_path)
        assert len(acks) == 1
        assert acks[0].kind == "resolved"
        assert acks[0].author == "reviewer-pattern-conformance"
        assert acks[0].prose == "single definition confirmed"


class TestUnknownFindingId:
    async def test_unknown_id_raises_clear_error(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        with pytest.raises(UnknownFindingError) as excinfo:
            await handle_mark_finding_addressed(
                project_path=tmp_path,
                ticket_id="t-1",
                finding_id="RC-99",
                author="dev",
                cycle=1,
                how_resolved="...",
            )
        assert "RC-99" in str(excinfo.value)

    async def test_unknown_ticket_id_raises(self, tmp_path: Path) -> None:
        # No findings seeded at all → ticket has no findings at all.
        with pytest.raises(UnknownFindingError):
            await handle_mark_finding_addressed(
                project_path=tmp_path,
                ticket_id="t-missing",
                finding_id="RC-1",
                author="dev",
                cycle=0,
                how_resolved="x",
            )


class TestIdempotencyWithinCycle:
    """Same (finding_id, kind, author, cycle) called twice is a no-op
    on the second call — returns the existing ack id without writing
    another row. Defends against agent retry storms polluting the
    audit log."""

    async def test_same_cycle_same_author_dedupes(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        first = await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="first attempt prose",
        )
        second = await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="second attempt prose (would-be duplicate)",
        )
        assert first == second  # dedup returns the existing row id
        acks = await _read_acks(tmp_path)
        assert len(acks) == 1
        # Original prose preserved — retries do NOT overwrite history.
        assert acks[0].prose == "first attempt prose"

    async def test_different_cycle_creates_new_row(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        first = await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="cycle 1 fix",
        )
        second = await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=2,
            how_resolved="cycle 2 follow-up fix",
        )
        assert first != second
        acks = await _read_acks(tmp_path)
        assert len(acks) == 2
        cycles = sorted(a.cycle for a in acks)
        assert cycles == [1, 2]

    async def test_different_author_creates_new_row(self, tmp_path: Path) -> None:
        """Dev marks addressed → reviewer marks resolved. Two distinct
        rows because the (kind, author) tuple differs."""
        await _seed_comment(tmp_path)
        await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="fixed",
        )
        await handle_mark_finding_resolved(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="reviewer-pattern-conformance",
            cycle=2,
            confirmation="confirmed",
        )
        acks = await _read_acks(tmp_path)
        assert len(acks) == 2
        kinds = sorted(a.kind for a in acks)
        assert kinds == ["addressed", "resolved"]


class TestRejectKind:
    async def test_reject_kind_writes_reject_ack(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="I disagree — this pattern is intentional",
            kind="reject",
        )
        acks = await _read_acks(tmp_path)
        assert len(acks) == 1
        assert acks[0].kind == "reject"
        assert acks[0].prose == "I disagree — this pattern is intentional"

    async def test_omitting_kind_defaults_to_addressed(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved="fixed it",
        )
        acks = await _read_acks(tmp_path)
        assert acks[0].kind == "addressed"

    async def test_reject_requires_nonempty_rationale(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        with pytest.raises(ValueError, match="non-empty"):
            await handle_mark_finding_addressed(
                project_path=tmp_path,
                ticket_id="t-1",
                finding_id="RC-1",
                author="dev",
                cycle=1,
                how_resolved="",
                kind="reject",
            )

    async def test_reject_requires_nonempty_rationale_whitespace(
        self, tmp_path: Path
    ) -> None:
        await _seed_comment(tmp_path)
        with pytest.raises(ValueError, match="non-empty"):
            await handle_mark_finding_addressed(
                project_path=tmp_path,
                ticket_id="t-1",
                finding_id="RC-1",
                author="dev",
                cycle=1,
                how_resolved="   ",
                kind="reject",
            )

    async def test_kind_resolved_rejected(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        with pytest.raises(ValueError):
            await handle_mark_finding_addressed(
                project_path=tmp_path,
                ticket_id="t-1",
                finding_id="RC-1",
                author="dev",
                cycle=1,
                how_resolved="x",
                kind="resolved",  # type: ignore[arg-type]
            )


class TestProseValidation:
    async def test_prose_over_max_length_rejected(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        long_prose = "x" * 501
        with pytest.raises(ValueError) as excinfo:
            await handle_mark_finding_addressed(
                project_path=tmp_path,
                ticket_id="t-1",
                finding_id="RC-1",
                author="dev",
                cycle=1,
                how_resolved=long_prose,
            )
        assert "500" in str(excinfo.value)

    async def test_prose_at_max_length_accepted(self, tmp_path: Path) -> None:
        await _seed_comment(tmp_path)
        ok_prose = "x" * 500
        await handle_mark_finding_addressed(
            project_path=tmp_path,
            ticket_id="t-1",
            finding_id="RC-1",
            author="dev",
            cycle=1,
            how_resolved=ok_prose,
        )
        acks = await _read_acks(tmp_path)
        assert acks[0].prose == ok_prose
