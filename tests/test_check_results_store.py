"""Tests for CheckResultsStore (Phase 5 Task A).

Covers append-only persistence of ``CheckResult`` records and the
query surfaces the gating layer depends on: per-ticket,
per-(ticket, phase), latest-per-check, and "latest batch" (one
record per check_name, most recent).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path


from jig.check_results import CheckResult
from jig.checks import CheckSeverity
from jig.store.check_results import CheckResultsStore


def _make_result(
    *,
    ticket_id: str = "tkt-1",
    phase: str = "dev",
    check_name: str = "unit-tests",
    verdict: str = "pass",
    severity: CheckSeverity = CheckSeverity.REQUIRED,
    offset_s: int = 0,
) -> CheckResult:
    base = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_s)
    return CheckResult(
        ticket_id=ticket_id,
        phase=phase,
        check_name=check_name,
        check_type="scripted",
        verdict=verdict,  # type: ignore[arg-type]
        severity=severity,
        started_at=base,
        finished_at=base + timedelta(seconds=1),
        output="ok",
    )


async def _store(tmp_path: Path) -> CheckResultsStore:
    store = CheckResultsStore(tmp_path / "check_results.jsonl")
    await store.load()
    return store


class TestRoundtrip:
    async def test_post_and_get(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        r = _make_result()
        rid = await store.post(r)
        loaded = await store.get(rid)
        assert loaded is not None
        assert loaded.check_name == "unit-tests"
        assert loaded.verdict == "pass"
        assert loaded.severity == CheckSeverity.REQUIRED

    async def test_get_missing_returns_none(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        assert await store.get("does-not-exist") is None


class TestQueries:
    async def test_for_ticket_returns_chronological(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        r_old = _make_result(offset_s=0, check_name="a")
        r_new = _make_result(offset_s=30, check_name="b")
        await store.post(r_new)  # insert out of order on purpose
        await store.post(r_old)
        got = await store.for_ticket("tkt-1")
        assert [r.check_name for r in got] == ["a", "b"]

    async def test_for_phase_filters(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.post(_make_result(phase="dev", check_name="x"))
        await store.post(_make_result(phase="review", check_name="y"))
        dev = await store.for_phase("tkt-1", "dev")
        assert [r.check_name for r in dev] == ["x"]
        review = await store.for_phase("tkt-1", "review")
        assert [r.check_name for r in review] == ["y"]

    async def test_latest_for_check_returns_newest(
        self, tmp_path: Path
    ) -> None:
        store = await _store(tmp_path)
        old = _make_result(verdict="fail", offset_s=0)
        new = _make_result(verdict="pass", offset_s=60)
        await store.post(old)
        await store.post(new)
        latest = await store.latest_for_check("tkt-1", "dev", "unit-tests")
        assert latest is not None
        assert latest.verdict == "pass"

    async def test_latest_for_check_none_when_missing(
        self, tmp_path: Path
    ) -> None:
        store = await _store(tmp_path)
        assert await store.latest_for_check("tkt-1", "dev", "nope") is None

    async def test_latest_batch_keeps_one_per_check(
        self, tmp_path: Path
    ) -> None:
        store = await _store(tmp_path)
        await store.post(_make_result(check_name="unit", verdict="fail", offset_s=0))
        await store.post(_make_result(check_name="unit", verdict="pass", offset_s=60))
        await store.post(_make_result(check_name="lint", verdict="pass", offset_s=30))
        batch = await store.latest_batch("tkt-1", "dev")
        by_name = {r.check_name: r for r in batch}
        assert set(by_name) == {"unit", "lint"}
        assert by_name["unit"].verdict == "pass"
        assert by_name["lint"].verdict == "pass"

    async def test_latest_batch_empty_phase(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        assert await store.latest_batch("tkt-1", "dev") == []


class TestPersistence:
    async def test_reload_reads_jsonl(self, tmp_path: Path) -> None:
        store = await _store(tmp_path)
        await store.post(_make_result(check_name="persist"))
        # Fresh instance pointed at the same file
        store2 = CheckResultsStore(tmp_path / "check_results.jsonl")
        await store2.load()
        got = await store2.for_ticket("tkt-1")
        assert [r.check_name for r in got] == ["persist"]
