"""Unit tests for jig.eval.runner — no real subprocess or WS required."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


from jig.eval.runner import (
    EvalOutcome,
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
        s.bind(("127.0.0.1", port))


def test_watch_completion_returns_out_dir() -> None:
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "event",
            "topic": "events",
            "kind": "project_complete",
            "data": {"id": "run1"},
        }
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
        {
            "type": "event",
            "topic": "events",
            "kind": "project_complete",
            "data": {"id": "run2"},
        }
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
    mock_proc.wait.side_effect = [
        subprocess.TimeoutExpired(cmd="jig", timeout=10),
        None,
    ]

    with patch("jig.evals.watcher.run._kill_orphan_subprocesses"):
        _teardown_proc(mock_proc, tmp_path)

    mock_proc.terminate.assert_called_once()
    mock_proc.kill.assert_called_once()
    assert mock_proc.wait.call_count == 2, (
        "wait() must be called after kill() to reap zombie"
    )


def test_tracer_skip_is_tracer_fail() -> None:
    outcome = _check_tracer_outcome(exit_code=0, stdout="")
    assert outcome == EvalOutcome.TRACER_FAIL


def test_tracer_nonzero_exit_is_tracer_fail() -> None:
    outcome = _check_tracer_outcome(exit_code=1, stdout="some output")
    assert outcome == EvalOutcome.TRACER_FAIL


def test_tracer_pass_returns_none() -> None:
    outcome = _check_tracer_outcome(
        exit_code=0, stdout="artifact found at /usr/local/bin/foo"
    )
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


def test_run_eval_none_tracer_is_tracer_fail(tmp_path: Path) -> None:
    """When collect() returns manifest.tracer=None, outcome must be TRACER_FAIL not SUCCESS."""
    import json as _json
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    proj_dir = tmp_path / "evals" / "projects" / "test-proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "brief.md").write_text("# Brief\n")
    (proj_dir / "tracer.sh").write_text("#!/bin/bash\necho ok\n")

    mock_manifest = MagicMock()
    mock_manifest.tracer = None
    mock_manifest.model_dump.return_value = {}

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.wait.return_value = None

    class _FakeWS:
        def __init__(self) -> None:
            self._frames = [
                _json.dumps(
                    {
                        "type": "event",
                        "topic": "events",
                        "kind": "project_complete",
                        "data": {},
                    }
                ),
                _json.dumps(
                    {
                        "type": "event",
                        "topic": "events",
                        "kind": "analysis_complete",
                        "data": {"out_dir": str(tmp_path)},
                    }
                ),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if self._idx < len(self._frames):
                frame = self._frames[self._idx]
                self._idx += 1
                return frame
            await asyncio.sleep(9999)  # hang until dispatch task is cancelled

        async def send(self, *args, **kwargs) -> None:
            pass

    @asynccontextmanager
    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()

    with (
        patch("jig.init_workflow.run_init", new=AsyncMock()),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch("jig.eval.collector.collect", new=AsyncMock(return_value=mock_manifest)),
        patch("jig.eval.runner._teardown_proc"),
        patch("websockets.asyncio.client.connect", new=_fake_connect),
    ):
        result = asyncio.run(
            run_eval(
                "test-proj",
                label=None,
                keep=False,
                timeout_minutes=1,
                jig_repo=tmp_path,
            )
        )

    assert result.outcome == EvalOutcome.TRACER_FAIL
    assert result.temp_dir is not None


def test_run_eval_collect_exception_tears_down_proc(tmp_path: Path) -> None:
    """collect() raising must not orphan the orchestrator subprocess."""
    import json as _json
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    proj_dir = tmp_path / "evals" / "projects" / "test-proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "brief.md").write_text("# Brief\n")
    (proj_dir / "tracer.sh").write_text("#!/bin/bash\necho ok\n")

    mock_proc = MagicMock(spec=subprocess.Popen)
    mock_proc.wait.return_value = None
    mock_teardown = MagicMock()

    class _FakeWS:
        def __init__(self) -> None:
            self._frames = [
                _json.dumps(
                    {
                        "type": "event",
                        "topic": "events",
                        "kind": "project_complete",
                        "data": {},
                    }
                ),
                _json.dumps(
                    {
                        "type": "event",
                        "topic": "events",
                        "kind": "analysis_complete",
                        "data": {"out_dir": "/tmp"},
                    }
                ),
            ]
            self._idx = 0

        def __aiter__(self):
            return self

        async def __anext__(self) -> str:
            if self._idx < len(self._frames):
                frame = self._frames[self._idx]
                self._idx += 1
                return frame
            await asyncio.sleep(9999)

        async def send(self, *args, **kwargs) -> None:
            pass

    @asynccontextmanager
    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()

    with (
        patch("jig.init_workflow.run_init", new=AsyncMock()),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.collector.collect",
            new=AsyncMock(side_effect=RuntimeError("collect failed")),
        ),
        patch("jig.eval.runner._teardown_proc", mock_teardown),
        patch("websockets.asyncio.client.connect", new=_fake_connect),
    ):
        with pytest.raises(RuntimeError, match="collect failed"):
            asyncio.run(
                run_eval(
                    "test-proj",
                    label=None,
                    keep=False,
                    timeout_minutes=1,
                    jig_repo=tmp_path,
                )
            )

    mock_teardown.assert_called_once()
