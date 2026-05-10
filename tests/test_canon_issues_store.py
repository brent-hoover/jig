"""Tests for CanonicalizationIssueStore."""

from __future__ import annotations

from pathlib import Path

from jig.store.canon_issues import (
    CanonicalizationIssue,
    CanonicalizationIssueStore,
)


def _make_issue(
    *,
    issue_type: str = "convention_violation",
    rule_id: str = "no-print",
    file_path: str = "src/a.py",
    ticket_id: str = "tkt-1",
    routing: str = "human_review",
) -> CanonicalizationIssue:
    return CanonicalizationIssue(
        type=issue_type,  # type: ignore[arg-type]
        rule_id=rule_id,
        rule_message="forbidden call",
        diff_hunk="-print('x')\n+log.info('x')\n",
        file_path=file_path,
        ticket_id=ticket_id,
        suggested_fixes=["use logging"],
        routing=routing,  # type: ignore[arg-type]
    )


async def _store(tmp_path: Path) -> CanonicalizationIssueStore:
    s = CanonicalizationIssueStore(tmp_path / "canon_issues.jsonl")
    await s.load()
    return s


class TestModel:
    def test_defaults(self) -> None:
        i = CanonicalizationIssue(
            type="convention_violation",
            rule_id="x",
            rule_message="m",
            diff_hunk="d",
            file_path="a.py",
        )
        assert i.status == "open"
        assert i.routing == "human_review"
        assert i.work_unit_refs == []
        assert i.suggested_fixes == []


class TestAppendAndQuery:
    async def test_append_and_for_ticket(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_issue(ticket_id="tkt-1", file_path="a.py"))
        await store.append(_make_issue(ticket_id="tkt-2", file_path="b.py"))
        await store.append(_make_issue(ticket_id="tkt-1", file_path="c.py"))
        got = await store.for_ticket("tkt-1")
        assert {i.file_path for i in got} == {"a.py", "c.py"}

    async def test_open_issues(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        oid = await store.append(_make_issue())
        await store.append(_make_issue(file_path="b.py"))
        await store.resolve(oid)
        got = await store.open_issues()
        assert len(got) == 1
        assert got[0].file_path == "b.py"


class TestStatusTransitions:
    async def test_resolve(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        iid = await store.append(_make_issue())
        ok = await store.resolve(iid)
        assert ok
        # Re-load to verify persistence
        store2 = CanonicalizationIssueStore(tmp_path / "canon_issues.jsonl")
        await store2.load()
        all_open = await store2.open_issues()
        assert all_open == []

    async def test_wontfix(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        iid = await store.append(_make_issue())
        ok = await store.wontfix(iid)
        assert ok
        got = await store.open_issues()
        assert got == []

    async def test_resolve_unknown_id_returns_false(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        ok = await store.resolve("missing")
        assert ok is False
