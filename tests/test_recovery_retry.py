"""Tests for bounded retry with backoff on recovery agent SDK calls.

Acceptance criteria pins:

AC1 — _try_replan and _try_resolve_conflict retry on transient SDK failure,
      bounded attempts (default 3), exponential backoff.
AC2 — Backoff sleep is injectable so tests run without real delays.
AC3 — Each retry attempt is logged with the error and backoff delay.
AC4 — Fails-twice-then-succeeds causes the recovery path to succeed on
      attempt 3 (asserts 3 attempts, no real sleeping).
AC5 — All attempts fail → recovery gives up gracefully, no orchestrator crash,
      preserves swallow-and-log behavior.
AC6 — Normal (non-recovery) agent dispatch is unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from jig.models import RoleConfig, PhaseConfig, WorkflowConfig
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ticket import Ticket, WorkType
from jig.agent import RunAgentResult
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
    )
    from jig.persistence import save_workflow, save_role

    (tmp_path / ".jig" / "workflows").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".jig" / "roles").mkdir(parents=True, exist_ok=True)
    wf = WorkflowConfig(
        name="default", phases=[PhaseConfig(name="spec", role="role-spec")]
    )
    save_workflow(tmp_path, wf)
    save_role(tmp_path, RoleConfig(role="role-spec", phase_prompt="be spec"))


async def _make_orch(tmp_path: Path) -> Orchestrator:
    _make_project(tmp_path)
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    return orch


async def _create_ticket(orch: Orchestrator) -> tuple[str, Ticket]:
    assert orch.tickets is not None
    tid = await orch.tickets.create(
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    ticket = await orch.tickets.get(tid)
    assert ticket is not None
    return tid, ticket


def _stub_load_role(monkeypatch, role_name: str) -> None:
    """Monkeypatch load_role to return a minimal RoleConfig for role_name."""
    from jig import orchestrator as orch_module

    monkeypatch.setattr(
        orch_module,
        "load_role",
        lambda *a, **k: RoleConfig(role=role_name, phase_prompt="x"),
    )


async def _noop_sleep(seconds: float) -> None:
    """Injectable sleep that does nothing (for test speed)."""


# ---------------------------------------------------------------------------
# AC4: _try_replan succeeds on third attempt when SDK fails twice then succeeds
# ---------------------------------------------------------------------------


async def test_try_replan_succeeds_on_third_attempt_after_two_failures(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4 / AC1 / AC2: _try_replan retries up to 3 times; when the SDK call
    fails on attempts 1 and 2 and succeeds on attempt 3, the replan completes
    without raising and exactly 3 attempts were made.

    Injectable sleep (_retry_sleep) must be used so the test runs instantly.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "pm")

        call_count = 0

        async def fail_twice_then_succeed(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"transient SDK error attempt {call_count}")
            return RunAgentResult(status="success", final_text="ok")

        orch._run_agent_with_analytics = fail_twice_then_succeed  # type: ignore[method-assign]

        # Call with injectable no-op sleep, 3 max attempts, base delay 2s
        await orch._try_replan(
            tid,
            ticket,
            ["foo.py"],
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=_noop_sleep,
        )

        assert call_count == 3, f"expected 3 attempts, got {call_count}"
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC5: _try_replan exhausts retries gracefully — no crash, swallows final err
# ---------------------------------------------------------------------------


async def test_try_replan_gives_up_gracefully_after_all_attempts_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5 / AC1 / AC2: when every attempt raises, _try_replan exhausts all
    retries and returns without raising (preserving swallow-and-log behavior).
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "pm")

        call_count = 0

        async def always_fail(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent SDK failure")

        orch._run_agent_with_analytics = always_fail  # type: ignore[method-assign]

        # Must not raise — fire-and-forget semantics are preserved
        await orch._try_replan(
            tid,
            ticket,
            ["foo.py"],
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=_noop_sleep,
        )

        assert call_count == 3, f"expected 3 attempts, got {call_count}"
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC4: _try_resolve_conflict succeeds on third attempt
# ---------------------------------------------------------------------------


async def test_try_resolve_conflict_succeeds_on_third_attempt(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4 / AC1 / AC2: _try_resolve_conflict retries; fails twice then
    succeeds on attempt 3.  Returns True and exactly 3 attempts were made.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "conflict_resolver")

        call_count = 0

        async def fail_twice_then_succeed(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"transient SDK error attempt {call_count}")
            return RunAgentResult(status="success", final_text="resolved")

        orch._run_agent_with_analytics = fail_twice_then_succeed  # type: ignore[method-assign]

        result = await orch._try_resolve_conflict(
            tid,
            ticket,
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=_noop_sleep,
        )

        assert result is True
        assert call_count == 3, f"expected 3 attempts, got {call_count}"
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC5: _try_resolve_conflict exhausts retries gracefully
# ---------------------------------------------------------------------------


async def test_try_resolve_conflict_gives_up_gracefully_after_all_attempts_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5 / AC1 / AC2: when every attempt raises, _try_resolve_conflict
    exhausts all retries and returns False (not raises) — preserving the
    swallow-and-log behavior.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "conflict_resolver")

        call_count = 0

        async def always_fail(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent SDK failure")

        orch._run_agent_with_analytics = always_fail  # type: ignore[method-assign]

        result = await orch._try_resolve_conflict(
            tid,
            ticket,
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=_noop_sleep,
        )

        assert result is False
        assert call_count == 3, f"expected 3 attempts, got {call_count}"
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC3: retry attempts are logged with error and backoff delay
# ---------------------------------------------------------------------------


async def test_try_replan_logs_retry_attempts_with_error_and_delay(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC3: each retry log record must contain both the underlying error text
    and the backoff delay value so operators can observe transient failures.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "pm")

        call_count = 0

        async def fail_twice_then_succeed(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error for logging test")
            return RunAgentResult(status="success", final_text="ok")

        orch._run_agent_with_analytics = fail_twice_then_succeed  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING, logger="jig.orchestrator"):
            await orch._try_replan(
                tid,
                ticket,
                ["foo.py"],
                _max_attempts=3,
                _base_delay=2.0,
                _sleep=_noop_sleep,
            )

        # At least one retry log record must include BOTH the error text AND
        # the numeric backoff delay (e.g. "2.0") so operators see the full picture.
        retry_records = [
            r
            for r in caplog.records
            if (
                (
                    "retry" in r.getMessage().lower()
                    or "attempt" in r.getMessage().lower()
                )
                and "transient error for logging test" in r.getMessage()
                and "2.0" in r.getMessage()
            )
        ]
        assert retry_records, (
            "expected at least one retry log record containing both the error text "
            "'transient error for logging test' and the delay '2.0'; "
            f"got records: {[r.getMessage() for r in caplog.records]}"
        )
    finally:
        await orch.shutdown()


async def test_try_resolve_conflict_logs_retry_attempts_with_error_and_delay(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """AC3: _try_resolve_conflict retry log records must contain both the
    underlying error text and the backoff delay value.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "conflict_resolver")

        call_count = 0

        async def fail_twice_then_succeed(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient error for logging test")
            return RunAgentResult(status="success", final_text="resolved")

        orch._run_agent_with_analytics = fail_twice_then_succeed  # type: ignore[method-assign]

        with caplog.at_level(logging.WARNING, logger="jig.orchestrator"):
            await orch._try_resolve_conflict(
                tid,
                ticket,
                _max_attempts=3,
                _base_delay=2.0,
                _sleep=_noop_sleep,
            )

        retry_records = [
            r
            for r in caplog.records
            if (
                (
                    "retry" in r.getMessage().lower()
                    or "attempt" in r.getMessage().lower()
                )
                and "transient error for logging test" in r.getMessage()
                and "2.0" in r.getMessage()
            )
        ]
        assert retry_records, (
            "expected at least one retry log record containing both the error text "
            "'transient error for logging test' and the delay '2.0'; "
            f"got records: {[r.getMessage() for r in caplog.records]}"
        )
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC6: normal (non-recovery) dispatch is unchanged — no new parameters
# ---------------------------------------------------------------------------


async def test_run_agent_with_analytics_signature_unchanged(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: _run_agent_with_analytics itself does not grow retry parameters.
    The normal dispatch call site (no _max_attempts / _sleep kwargs) must
    still work exactly as before.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)

        import inspect

        sig = inspect.signature(orch._run_agent_with_analytics)
        param_names = set(sig.parameters.keys())

        # Retry parameters must NOT appear on _run_agent_with_analytics
        assert "_max_attempts" not in param_names, (
            "_run_agent_with_analytics must not have _max_attempts — "
            "retry lives in the recovery entry-points only"
        )
        assert "_sleep" not in param_names, (
            "_run_agent_with_analytics must not have _sleep — "
            "retry lives in the recovery entry-points only"
        )
    finally:
        await orch.shutdown()


async def test_try_replan_retry_parameters_exist(
    tmp_path: Path,
) -> None:
    """AC2: _try_replan must accept _max_attempts, _base_delay, and _sleep
    keyword parameters so tests can inject instant no-op delays.
    """
    import inspect

    orch = await _make_orch(tmp_path)
    try:
        sig = inspect.signature(orch._try_replan)
        param_names = set(sig.parameters.keys())
        assert "_max_attempts" in param_names, "_try_replan missing _max_attempts param"
        assert "_base_delay" in param_names, "_try_replan missing _base_delay param"
        assert "_sleep" in param_names, "_try_replan missing _sleep param"
    finally:
        await orch.shutdown()


async def test_try_resolve_conflict_retry_parameters_exist(
    tmp_path: Path,
) -> None:
    """AC2: _try_resolve_conflict must accept _max_attempts, _base_delay, and
    _sleep keyword parameters.
    """
    import inspect

    orch = await _make_orch(tmp_path)
    try:
        sig = inspect.signature(orch._try_resolve_conflict)
        param_names = set(sig.parameters.keys())
        assert "_max_attempts" in param_names, (
            "_try_resolve_conflict missing _max_attempts param"
        )
        assert "_base_delay" in param_names, (
            "_try_resolve_conflict missing _base_delay param"
        )
        assert "_sleep" in param_names, "_try_resolve_conflict missing _sleep param"
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC1: exponential backoff sequence — sleep delays must grow as 2^n * base
# ---------------------------------------------------------------------------


async def test_try_replan_exponential_backoff_sequence(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: the sleep delay passed on each retry must follow 2^n * base, i.e.
    [2.0, 4.0] for attempts 1 and 2 before the third succeeds.

    A capturing sleep replaces the real asyncio.sleep so the sequence is
    recorded without any actual waiting.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "pm")

        call_count = 0
        recorded_delays: list[float] = []

        async def capturing_sleep(seconds: float) -> None:
            recorded_delays.append(seconds)

        async def fail_twice_then_succeed(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"transient failure {call_count}")
            return RunAgentResult(status="success", final_text="ok")

        orch._run_agent_with_analytics = fail_twice_then_succeed  # type: ignore[method-assign]

        await orch._try_replan(
            tid,
            ticket,
            ["foo.py"],
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=capturing_sleep,
        )

        # Two failures before the third success → two sleep calls.
        # Delays must be base*2^0=2.0 then base*2^1=4.0.
        assert recorded_delays == [2.0, 4.0], (
            f"expected exponential backoff delays [2.0, 4.0], got {recorded_delays}"
        )
    finally:
        await orch.shutdown()


async def test_try_resolve_conflict_exponential_backoff_sequence(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: _try_resolve_conflict sleep delays must follow 2^n * base, i.e.
    [2.0, 4.0] for two failures before the third succeeds.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "conflict_resolver")

        call_count = 0
        recorded_delays: list[float] = []

        async def capturing_sleep(seconds: float) -> None:
            recorded_delays.append(seconds)

        async def fail_twice_then_succeed(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError(f"transient failure {call_count}")
            return RunAgentResult(status="success", final_text="resolved")

        orch._run_agent_with_analytics = fail_twice_then_succeed  # type: ignore[method-assign]

        result = await orch._try_resolve_conflict(
            tid,
            ticket,
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=capturing_sleep,
        )

        assert result is True
        assert recorded_delays == [2.0, 4.0], (
            f"expected exponential backoff delays [2.0, 4.0], got {recorded_delays}"
        )
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC1/AC4/AC5: default _max_attempts is 3 — never pass it explicitly
# ---------------------------------------------------------------------------


async def test_try_replan_default_max_attempts_is_3(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1/AC4/AC5: when _max_attempts is NOT supplied, exactly 3 attempts
    must occur on persistent failure — so the default is verifiably 3, not 1
    or 5 or any other value.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "pm")

        call_count = 0

        async def always_fail(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent failure")

        orch._run_agent_with_analytics = always_fail  # type: ignore[method-assign]

        # Deliberately omit _max_attempts to exercise the default
        await orch._try_replan(
            tid,
            ticket,
            ["foo.py"],
            _base_delay=2.0,
            _sleep=_noop_sleep,
        )

        assert call_count == 3, (
            f"default _max_attempts must be 3; got {call_count} attempts"
        )
    finally:
        await orch.shutdown()


async def test_try_resolve_conflict_default_max_attempts_is_3(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1/AC4/AC5: when _max_attempts is NOT supplied, exactly 3 attempts
    must occur on persistent failure for _try_resolve_conflict.
    """
    orch = await _make_orch(tmp_path)
    try:
        tid, ticket = await _create_ticket(orch)
        _stub_load_role(monkeypatch, "conflict_resolver")

        call_count = 0

        async def always_fail(ctx, spawned_by="orchestrator"):
            nonlocal call_count
            call_count += 1
            raise RuntimeError("persistent failure")

        orch._run_agent_with_analytics = always_fail  # type: ignore[method-assign]

        result = await orch._try_resolve_conflict(
            tid,
            ticket,
            _base_delay=2.0,
            _sleep=_noop_sleep,
        )

        assert result is False
        assert call_count == 3, (
            f"default _max_attempts must be 3; got {call_count} attempts"
        )
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# AC6: normal phase dispatch invokes the SDK exactly once (no retry leak)
# ---------------------------------------------------------------------------


async def test_normal_phase_dispatch_invokes_sdk_exactly_once(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6 behavioral: running a ticket through the normal phase dispatch path
    calls _run_agent_with_analytics exactly once.  Retry logic must not leak
    into the non-recovery code path.
    """
    orch = await _make_orch(tmp_path)
    try:
        # _make_project already wrote a single-phase workflow (spec / role-spec)
        # and the matching role config.  We stub out git worktree creation so
        # _run_ticket can proceed without a real repo.
        async def fake_ensure_worktree(ticket):
            wt = tmp_path / ".jig" / "worktrees" / ticket.id
            wt.mkdir(parents=True, exist_ok=True)
            return wt

        orch._ensure_worktree = fake_ensure_worktree  # type: ignore[method-assign]

        sdk_call_count = 0

        async def counting_sdk(ctx, spawned_by="orchestrator"):
            nonlocal sdk_call_count
            sdk_call_count += 1
            return RunAgentResult(status="success", final_text="done")

        orch._run_agent_with_analytics = counting_sdk  # type: ignore[method-assign]

        tid, _ticket = await _create_ticket(orch)

        await orch._run_ticket(tid)

        assert sdk_call_count == 1, (
            f"normal phase dispatch must invoke the SDK exactly once; "
            f"got {sdk_call_count} calls — retry logic may have leaked"
        )
    finally:
        await orch.shutdown()


# ---------------------------------------------------------------------------
# Shutdown during replan backoff — background task must not outlive orchestrator
# ---------------------------------------------------------------------------


async def test_try_replan_stops_retrying_after_shutdown(
    tmp_path: Path, monkeypatch
) -> None:
    """_try_replan must not spawn another PM SDK call after shutdown().

    The orchestrator's shutdown() cancels background tasks (including any
    in-flight _try_replan fire-and-forget task).  Additionally, after the
    backoff sleep, _try_replan must check self._running and abort rather than
    spawning another agent call.

    This test drives the scenario directly: fail attempt 1, simulate shutdown
    during the backoff sleep, then assert that attempt 2 never happens.
    """
    orch = await _make_orch(tmp_path)
    tid, ticket = await _create_ticket(orch)
    _stub_load_role(monkeypatch, "pm")

    call_count = 0

    async def fail_first_then_succeed(ctx, spawned_by="orchestrator"):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient failure on attempt 1")
        return RunAgentResult(status="success", final_text="ok")

    orch._run_agent_with_analytics = fail_first_then_succeed  # type: ignore[method-assign]

    async def shutdown_during_sleep(seconds: float) -> None:
        """Simulate shutdown happening while the backoff sleep is in progress."""
        orch._running = False

    await orch._try_replan(
        tid,
        ticket,
        ["foo.py"],
        _max_attempts=3,
        _base_delay=2.0,
        _sleep=shutdown_during_sleep,
    )

    # Only attempt 1 should have run; after the sleep _running is False so
    # _try_replan must return without making attempt 2.
    assert call_count == 1, (
        f"_try_replan must stop after shutdown during backoff sleep; "
        f"got {call_count} agent calls (expected 1)"
    )

    await orch.shutdown()


async def test_shutdown_cancels_background_replan_task(
    tmp_path: Path, monkeypatch
) -> None:
    """shutdown() must cancel any in-flight _try_replan background task.

    If _try_replan is sleeping during its backoff and shutdown() is called,
    the background task (stored in _background_tasks) must be cancelled so it
    cannot spawn another agent call after the orchestrator has stopped.
    """
    import asyncio

    orch = await _make_orch(tmp_path)
    tid, ticket = await _create_ticket(orch)
    _stub_load_role(monkeypatch, "pm")

    sleep_started = asyncio.Event()
    sleep_cancelled = asyncio.Event()
    agent_call_count = 0

    async def fail_once(ctx, spawned_by="orchestrator"):
        nonlocal agent_call_count
        agent_call_count += 1
        if agent_call_count == 1:
            raise RuntimeError("first attempt fails")
        return RunAgentResult(status="success", final_text="ok")

    orch._run_agent_with_analytics = fail_once  # type: ignore[method-assign]

    async def slow_sleep(seconds: float) -> None:
        """Block until cancelled — simulates a long backoff window."""
        sleep_started.set()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            sleep_cancelled.set()
            raise

    # Manually schedule _try_replan as a background task the same way
    # the orchestrator does, so shutdown() sees it in _background_tasks.
    _task = asyncio.create_task(
        orch._try_replan(
            tid,
            ticket,
            ["foo.py"],
            _max_attempts=3,
            _base_delay=2.0,
            _sleep=slow_sleep,
        )
    )
    orch._background_tasks.add(_task)
    _task.add_done_callback(orch._background_tasks.discard)

    # Wait until the task is sleeping inside backoff, then shut down.
    await asyncio.wait_for(sleep_started.wait(), timeout=5.0)
    await orch.shutdown()

    # The background task must have been cancelled.
    assert _task.cancelled() or _task.done(), (
        "background _try_replan task must be done after shutdown()"
    )
    # No second agent call should have been made.
    assert agent_call_count == 1, (
        f"shutdown must prevent the second agent call; got {agent_call_count} calls"
    )
