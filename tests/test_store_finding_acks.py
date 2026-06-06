"""Tests for ``FindingAcksStore`` — fix-loop-context step 1.

Sibling of ``ReviewCommentsStore``: append-only JSONL, Collection-backed,
indexed on ticket_id and finding_id. One row per ack event. Records
addressed (dev claim), resolved (reviewer confirmation), reraised
(orchestrator-detected re-flag).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jig.store.finding_acks import FindingAck, FindingAcksStore

UTC = timezone.utc


def _ack(
    *,
    ticket_id: str = "t-1",
    finding_id: str = "RC-1",
    kind: str = "addressed",
    author: str = "dev",
    cycle: int = 0,
    prose: str = "fixed",
) -> FindingAck:
    return FindingAck(
        ticket_id=ticket_id,
        finding_id=finding_id,
        kind=kind,
        author=author,
        cycle=cycle,
        prose=prose,
    )


class TestModelFields:
    def test_minimum_required_fields(self) -> None:
        ack = _ack()
        assert ack.ticket_id == "t-1"
        assert ack.finding_id == "RC-1"
        assert ack.kind == "addressed"
        assert ack.author == "dev"
        assert ack.cycle == 0
        assert ack.prose == "fixed"
        assert ack.created_at.tzinfo is not None

    def test_kind_constrained_to_literals(self) -> None:
        # pydantic validates Literal at construction
        import pytest
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            FindingAck(
                ticket_id="t-1",
                finding_id="RC-1",
                kind="invented-kind",  # type: ignore[arg-type]
                author="dev",
                cycle=0,
                prose="x",
            )

    def test_reject_kind_accepted(self) -> None:
        ack = FindingAck(
            ticket_id="t-1",
            finding_id="RC-1",
            kind="reject",  # type: ignore[arg-type]
            author="dev",
            cycle=0,
            prose="disagree with this finding",
        )
        assert ack.kind == "reject"


class TestRoundtrip:
    async def test_append_and_for_ticket(self, tmp_path: Path) -> None:
        store = FindingAcksStore(tmp_path / "acks.jsonl")
        await store.load()
        await store.append(_ack(finding_id="RC-1"))
        await store.append(_ack(finding_id="RC-2", kind="resolved", author="rev"))
        rows = await store.for_ticket("t-1")
        assert len(rows) == 2
        assert {r.finding_id for r in rows} == {"RC-1", "RC-2"}

    async def test_for_finding_filters(self, tmp_path: Path) -> None:
        store = FindingAcksStore(tmp_path / "acks.jsonl")
        await store.load()
        await store.append(_ack(finding_id="RC-1"))
        await store.append(_ack(finding_id="RC-2"))
        await store.append(_ack(finding_id="RC-1", kind="resolved", author="rev"))
        rows = await store.for_finding("t-1", "RC-1")
        assert len(rows) == 2
        assert {r.kind for r in rows} == {"addressed", "resolved"}


class TestAppendStampsCreatedAt:
    async def test_sentinel_replaced_with_now(self, tmp_path: Path) -> None:
        from datetime import timedelta

        store = FindingAcksStore(tmp_path / "acks.jsonl")
        await store.load()
        ack = _ack()
        # Construct with sentinel explicitly to confirm the store
        # overwrites it. (The default factory shape would silently pass
        # this test — we want it to fail loudly if someone uses the
        # wrong default.)
        ack = ack.model_copy(update={"created_at": datetime(1, 1, 1, tzinfo=UTC)})
        before = datetime.now(UTC)
        await store.append(ack)
        rows = await store.for_ticket("t-1")
        ts = rows[0].created_at
        assert ts != datetime(1, 1, 1, tzinfo=UTC)
        assert ts >= before - timedelta(seconds=2)


class TestAppendOnlyAcrossLoads:
    async def test_persisted_rows_survive_reload(self, tmp_path: Path) -> None:
        path = tmp_path / "acks.jsonl"
        first = FindingAcksStore(path)
        await first.load()
        await first.append(_ack(finding_id="RC-1"))
        await first.append(_ack(finding_id="RC-2", kind="resolved", author="rev"))

        second = FindingAcksStore(path)
        await second.load()
        rows = await second.for_ticket("t-1")
        assert len(rows) == 2
