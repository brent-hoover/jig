"""Unit tests for jig.eval.runner — no real subprocess or WS required."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jig.eval.runner import (
    EvalOutcome,
    RunResult,
    _check_tracer_outcome,
    _free_port,
    _teardown_proc,
    _watch_completion,
    _watch_stall,
    run_eval,
)
from jig.evals.watcher.heuristics import StallThresholds
from jig.evals.watcher.stall_detector import StallDetector


def test_free_port_returns_usable_port() -> None:
    port = _free_port()
    import socket

    with socket.socket() as s:
        s.bind(("", port))


def test_watch_completion_returns_out_dir() -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(
        {"type": "event", "topic": "events", "kind": "project_complete", "data": {"id": "run1"}}
    )
    queue.put_nowait(
        {
            "type": "event",
            "topic": "events",
            "kind": "analysis_complete",
            "data": {"out_dir": "/tmp/analysis"},
        }
    )

    data, out_dir = asyncio.run(_watch_completion(queue))

    assert out_dir == "/tmp/analysis"
    assert data == {"id": "run1"}


def test_watch_completion_timeout_no_analysis() -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(
        {"type": "event", "topic": "events", "kind": "project_complete", "data": {"id": "run2"}}
    )

    # Use a tiny analysis_wait so we don't actually wait 60s
    data, out_dir = asyncio.run(_watch_completion(queue, analysis_wait=0.05))

    assert out_dir is None
    assert data == {"id": "run2"}


def test_watch_stall_poll_fires_on_silence() -> None:
    thresholds = StallThresholds(bus_silence_seconds=0.1, poll_interval_seconds=0.05)
    detector = StallDetector(thresholds=thresholds)
    queue: asyncio.Queue[dict] = asyncio.Queue()

    verdict = asyncio.run(_watch_stall(queue, detector))

    assert verdict.signal == "bus_silence"  # type: ignore[attr-defined]


def test_stall_teardown_sigterms_subprocess(tmp_path: Path) -> None:
    mock_proc = MagicMock(spec=subprocess.Popen)
    # First wait() (with timeout) times out; second wait() (bare, after SIGKILL) succeeds.
    mock_proc.wait.side_effect = [subprocess.TimeoutExpired(cmd="jig", timeout=10), None]

    with patch("jig.evals.watcher.run._kill_orphan_subprocesses"):
        _teardown_proc(mock_proc, tmp_path)

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    assert mock_proc.wait.call_count == 2, "wait() must be called after kill() to reap zombie"


def test_tracer_skip_is_tracer_fail() -> None:
    outcome = _check_tracer_outcome(exit_code=0, stdout="")
    assert outcome == EvalOutcome.TRACER_FAIL


def test_tracer_nonzero_exit_is_tracer_fail() -> None:
    outcome = _check_tracer_outcome(exit_code=1, stdout="some output")
    assert outcome == EvalOutcome.TRACER_FAIL


def test_tracer_pass_returns_none() -> None:
    outcome = _check_tracer_outcome(exit_code=0, stdout="artifact found at /usr/local/bin/foo")
    assert outcome is None


def test_run_eval_missing_project_returns_init_error(tmp_path: Path) -> None:
    result = asyncio.run(
        run_eval(
            "nonexistent-project",
            label=None,
            keep=False,
            timeout_minutes=90,
            jig_repo=tmp_path,
        )
    )

    assert result.outcome == EvalOutcome.INIT_ERROR
    assert result.manifest_path is None
