"""Tests for AuditStore + AuditEntry."""

from __future__ import annotations

from pathlib import Path

from jig.store.audit import AuditEntry, AuditStore


def _make_entry(
    *,
    run_id: str = "r1",
    rule_id: str = "ruff-format",
    rule_source: str = "formatter",
    file_path: str = "src/a.py",
    ticket_id: str = "tkt-1",
    before_hash: str = "a" * 64,
    after_hash: str = "b" * 64,
) -> AuditEntry:
    return AuditEntry(
        run_id=run_id,
        rule_id=rule_id,
        rule_source=rule_source,  # type: ignore[arg-type]
        file_path=file_path,
        before_hash=before_hash,
        after_hash=after_hash,
        ticket_id=ticket_id,
    )


async def _store(tmp_path: Path) -> AuditStore:
    s = AuditStore(tmp_path / "audit.jsonl")
    await s.load()
    return s


class TestAuditEntry:
    def test_defaults(self) -> None:
        e = AuditEntry(
            run_id="r1",
            rule_id="x",
            rule_source="formatter",
            file_path="a.py",
            before_hash="0" * 64,
            after_hash="1" * 64,
        )
        assert e.phase == "canonicalize"
        assert e.ticket_id == ""
        assert e.applied_at is not None


class TestAppendAndQuery:
    async def test_append_returns_id(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        eid = await store.append(_make_entry())
        assert isinstance(eid, str) and eid

    async def test_for_ticket(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_entry(ticket_id="tkt-1", file_path="a.py"))
        await store.append(_make_entry(ticket_id="tkt-2", file_path="b.py"))
        await store.append(_make_entry(ticket_id="tkt-1", file_path="c.py"))
        got = await store.for_ticket("tkt-1")
        assert {e.file_path for e in got} == {"a.py", "c.py"}

    async def test_for_run(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_entry(run_id="r1"))
        await store.append(_make_entry(run_id="r2"))
        await store.append(_make_entry(run_id="r1"))
        got = await store.for_run("r1")
        assert len(got) == 2

    async def test_all(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_entry(file_path="a.py"))
        await store.append(_make_entry(file_path="b.py"))
        got = await store.all()
        assert {e.file_path for e in got} == {"a.py", "b.py"}


class TestPersistence:
    async def test_reload(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.append(_make_entry(file_path="persist.py"))
        store2 = AuditStore(tmp_path / "audit.jsonl")
        await store2.load()
        got = await store2.all()
        assert [e.file_path for e in got] == ["persist.py"]
