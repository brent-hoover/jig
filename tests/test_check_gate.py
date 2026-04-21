"""Tests for jig.check_gate (Phase 5 Task D).

Covers the gate verdict logic (pass/fail/missing), SystemEvent emission
on failing required checks, warning-severity advisory behavior, the
"missing result = fail" rule, severity re-scoring via catalog lookup,
waiver-based clearing, and the read-only ``check_gate_status`` variant.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from jig.check_gate import (
    GateVerdict,
    check_gate_status,
    evaluate_handoff_gate,
)
from jig.check_results import CheckResult
from jig.checks import (
    CheckCatalog,
    CheckSeverity,
    ScriptedCheck,
)
from jig.store.check_results import CheckResultsStore
from jig.store.threads import ThreadStore
from jig.thread import SystemEvent


def _catalog(
    *,
    required: list[str] | None = None,
    warning: list[str] | None = None,
) -> CheckCatalog:
    payload: dict[str, ScriptedCheck] = {}
    for name in required or []:
        payload[name] = ScriptedCheck(
            type="scripted", command="true",
            severity=CheckSeverity.REQUIRED,
        )
    for name in warning or []:
        payload[name] = ScriptedCheck(
            type="scripted", command="true",
            severity=CheckSeverity.WARNING,
        )
    return CheckCatalog.model_validate(
        {k: v.model_dump() for k, v in payload.items()}
    )


def _result(
    *,
    ticket_id: str = "tkt-1",
    phase: str = "dev",
    check_name: str,
    verdict: str = "pass",
    severity: CheckSeverity = CheckSeverity.REQUIRED,
    output: str = "",
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
        output=output,
    )


async def _stores(
    tmp_path: Path,
) -> tuple[CheckResultsStore, ThreadStore]:
    results = CheckResultsStore(tmp_path / "check_results.jsonl")
    await results.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    return results, threads


class TestPassingGate:
    async def test_all_pass(self, tmp_path: Path) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(_result(check_name="unit"))
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        assert verdict.passing is True
        assert verdict.failing == []
        assert verdict.missing == []
        assert verdict.posted_events == []

    async def test_no_required_declared_passes(self, tmp_path: Path) -> None:
        results, threads = await _stores(tmp_path)
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=[],
        )
        assert verdict.passing is True


class TestFailingGate:
    async def test_required_fail_blocks_and_posts_event(
        self, tmp_path: Path
    ) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(
            _result(
                check_name="unit",
                verdict="fail",
                output="AssertionError: expected 1, got 2",
            )
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        assert verdict.passing is False
        assert len(verdict.failing) == 1
        assert verdict.failing[0].check_name == "unit"
        assert verdict.failing[0].verdict == "fail"
        assert len(verdict.posted_events) == 1

        entries = await threads.for_ticket("tkt-1")
        events = [
            e for e in entries
            if isinstance(e, SystemEvent)
            and e.event_type == "check_failure"
        ]
        assert len(events) == 1
        ev = events[0]
        assert ev.check_name == "unit"
        assert ev.check_verdict == "fail"
        assert ev.check_severity == "required"
        assert ev.waived is False
        assert "AssertionError" in ev.excerpt

    async def test_timeout_and_error_also_block(
        self, tmp_path: Path
    ) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(
            _result(check_name="unit", verdict="timeout", offset_s=0)
        )
        await results.post(
            _result(check_name="lint", verdict="error", offset_s=10)
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit", "lint"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit", "lint"],
        )
        assert verdict.passing is False
        names = {f.check_name for f in verdict.failing}
        assert names == {"unit", "lint"}


class TestWarningSeverity:
    async def test_warning_fail_does_not_block(
        self, tmp_path: Path
    ) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(
            _result(
                check_name="lint",
                verdict="fail",
                severity=CheckSeverity.WARNING,
            )
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(warning=["lint"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["lint"],
        )
        assert verdict.passing is True
        assert verdict.failing == []
        entries = await threads.for_ticket("tkt-1")
        assert not any(
            isinstance(e, SystemEvent)
            and e.event_type == "check_failure"
            for e in entries
        )


class TestMissingResults:
    async def test_missing_required_blocks(self, tmp_path: Path) -> None:
        results, threads = await _stores(tmp_path)
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        assert verdict.passing is False
        assert verdict.missing == ["unit"]
        # No event emitted for a missing result — we have no output
        # to quote, and the audit signal is the absence itself
        # (handoff gets bounced).
        assert verdict.posted_events == []

    async def test_mixed_missing_and_pass(
        self, tmp_path: Path
    ) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(_result(check_name="unit", verdict="pass"))
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit", "lint"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit", "lint"],
        )
        assert verdict.passing is False
        assert verdict.missing == ["lint"]
        assert verdict.failing == []


class TestLatestRunWins:
    async def test_latest_pass_clears_earlier_fail(
        self, tmp_path: Path
    ) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(
            _result(check_name="unit", verdict="fail", offset_s=0)
        )
        await results.post(
            _result(check_name="unit", verdict="pass", offset_s=10)
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        assert verdict.passing is True


class TestSeverityRescoredFromCatalog:
    async def test_record_severity_stale_but_catalog_wins(
        self, tmp_path: Path
    ) -> None:
        # Record was posted while the check was REQUIRED; catalog has
        # since been flipped to WARNING. Gate should respect the
        # current catalog.
        results, threads = await _stores(tmp_path)
        await results.post(
            _result(
                check_name="flaky",
                verdict="fail",
                severity=CheckSeverity.REQUIRED,
            )
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(warning=["flaky"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["flaky"],
        )
        assert verdict.passing is True


class TestWaiver:
    async def test_waived_check_failure_clears_gate(
        self, tmp_path: Path
    ) -> None:
        """Waivers are scoped to a specific failing run (commit_sha).
        Both the result and the existing waived event must agree on
        commit_sha for the waiver to carry."""
        results, threads = await _stores(tmp_path)
        sha = "deadbeef" * 5
        result = _result(
            check_name="unit", verdict="fail"
        ).model_copy(update={"commit_sha": sha})
        await results.post(result)
        await threads.post(
            SystemEvent(
                ticket_id="tkt-1",
                author="harness",
                event_type="check_failure",
                content="Required check 'unit' did not pass",
                check_name="unit",
                check_severity="required",
                check_verdict="fail",
                waived=True,
                commit_sha=sha,
            )
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        assert verdict.passing is True
        # No new check_failure posted for the already-waived check.
        entries = await threads.for_ticket("tkt-1")
        failure_events = [
            e for e in entries
            if isinstance(e, SystemEvent)
            and e.event_type == "check_failure"
        ]
        assert len(failure_events) == 1
        assert failure_events[0].waived is True

    async def test_waiver_does_not_carry_to_new_commit(
        self, tmp_path: Path
    ) -> None:
        """A fresh failure on a different commit needs its own waiver —
        the previous waiver is scoped to the specific run it authorized."""
        results, threads = await _stores(tmp_path)
        old_sha = "cafebabe" * 5
        new_sha = "f00df00d" * 5
        # New failing run on a different commit.
        result = _result(
            check_name="unit", verdict="fail"
        ).model_copy(update={"commit_sha": new_sha})
        await results.post(result)
        # Previous (waived) run — different commit.
        await threads.post(
            SystemEvent(
                ticket_id="tkt-1",
                author="harness",
                event_type="check_failure",
                content="Required check 'unit' did not pass",
                check_name="unit",
                check_severity="required",
                check_verdict="fail",
                waived=True,
                commit_sha=old_sha,
            )
        )
        verdict = await evaluate_handoff_gate(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        # Re-run is unwaived — gate fails and posts a new event.
        assert verdict.passing is False
        assert len(verdict.posted_events) == 1


class TestUnknownCheck:
    async def test_unknown_required_name_raises(
        self, tmp_path: Path
    ) -> None:
        results, threads = await _stores(tmp_path)
        with pytest.raises(KeyError):
            await evaluate_handoff_gate(
                catalog=_catalog(required=["unit"]),
                results=results,
                threads=threads,
                ticket_id="tkt-1",
                phase="dev",
                required_check_names=["unit", "mystery"],
            )


class TestGateStatusReadOnly:
    async def test_does_not_post_events(self, tmp_path: Path) -> None:
        results, threads = await _stores(tmp_path)
        await results.post(_result(check_name="unit", verdict="fail"))
        verdict = await check_gate_status(
            catalog=_catalog(required=["unit"]),
            results=results,
            threads=threads,
            ticket_id="tkt-1",
            phase="dev",
            required_check_names=["unit"],
        )
        assert isinstance(verdict, GateVerdict)
        assert verdict.passing is False
        assert verdict.posted_events == []
        assert verdict.mode == "pre-spawn"
        entries = await threads.for_ticket("tkt-1")
        assert not any(
            isinstance(e, SystemEvent)
            and e.event_type == "check_failure"
            for e in entries
        )
