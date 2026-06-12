"""Unit tests for jig.eval.runner — no real subprocess or WS required."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch

import pytest


from jig.eval.runner import (
    _classify_completion,
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

    kind, data, out_dir = asyncio.run(_watch_completion(queue))

    assert kind == "project_complete"
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
    kind, data, out_dir = asyncio.run(_watch_completion(queue, analysis_wait=0.05))

    assert kind == "project_complete"
    assert out_dir is None
    assert data == {"id": "run2"}


def test_watch_completion_project_stuck_terminates_promptly() -> None:
    """A project_stuck frame must end the watch immediately with the
    stuck payload — falling through to the stall detector's catch-all is
    exactly the misclassification being fixed."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(
        {
            "type": "event",
            "topic": "events",
            "kind": "project_stuck",
            "data": {"stuck_tickets": {"x": {"status": "open", "blocked_by": ["y"]}}},
        }
    )

    kind, data, out_dir = asyncio.run(
        asyncio.wait_for(_watch_completion(queue), timeout=1.0)
    )

    assert kind == "project_stuck"
    assert out_dir is None
    assert "x" in data["stuck_tickets"]


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

    mock_run_init = AsyncMock()
    with (
        patch("jig.init_workflow.run_init", mock_run_init),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ),
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
    mock_run_init.assert_awaited_once_with(
        name=ANY,
        force=False,
        brief_file=ANY,
        prompts=ANY,
        profile_name="small",
    )


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

    mock_run_init = AsyncMock()
    with (
        patch("jig.init_workflow.run_init", mock_run_init),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ),
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
    mock_run_init.assert_awaited_once_with(
        name=ANY,
        force=False,
        brief_file=ANY,
        prompts=ANY,
        profile_name="small",
    )


def test_run_eval_auto_responds_to_question_prompt(tmp_path: Path) -> None:
    """A question_answer prompt_request frame must produce a prompt_reply send."""
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
            self.sent: list[str] = []
            # Prompt frame FIRST: it must flow through _dispatch into
            # responder_q while the responder task is still alive (i.e.
            # before project_complete settles the race).
            self._frames = [
                _json.dumps(
                    {
                        "type": "event",
                        "topic": "prompts",
                        "kind": "request",
                        "data": {
                            "prompt_id": "prompt-1",
                            "prompt_type": "question_answer",
                            "ticket_id": "planning",
                            "asker": "pm",
                            "question_text": "Approve the plan?",
                            "question": "Approve the plan?",
                        },
                    }
                ),
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
            if self._idx == 1:
                # Gate project_complete until the prompt_reply send is
                # observed — otherwise the completion race can settle and
                # cancel the responder before it processes the prompt frame.
                while not self.sent:
                    await asyncio.sleep(0.01)
            if self._idx < len(self._frames):
                frame = self._frames[self._idx]
                self._idx += 1
                return frame
            await asyncio.sleep(9999)  # hang until dispatch task is cancelled

        async def send(self, raw: str) -> None:
            self.sent.append(raw)

    fake_ws = _FakeWS()

    @asynccontextmanager
    async def _fake_connect(*args, **kwargs):
        yield fake_ws

    with (
        patch("jig.init_workflow.run_init", new=AsyncMock()),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ),
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

    assert result.outcome == EvalOutcome.TRACER_FAIL  # tracer=None path
    replies = [
        m
        for m in (_json.loads(s) for s in fake_ws.sent)
        if m.get("type") == "command" and m.get("name") == "prompt_reply"
    ]
    assert len(replies) == 1
    assert replies[0]["args"]["args"][0] == "prompt-1"


def _success_path_fakes(tmp_path: Path):
    """Shared fixtures for success-path tests: project files, proc, FakeWS."""
    import json as _json

    proj_dir = tmp_path / "evals" / "projects" / "test-proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "brief.md").write_text("# Brief\n")
    (proj_dir / "tracer.sh").write_text("#!/bin/bash\necho ok\n")

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
            await asyncio.sleep(9999)

        async def send(self, *args, **kwargs) -> None:
            pass

    return mock_proc, _FakeWS


def test_run_eval_build_failure_is_tracer_fail(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """uv sync failing must yield TRACER_FAIL with stderr logged, no collect()."""
    import logging
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    mock_proc, _FakeWS = _success_path_fakes(tmp_path)
    mock_teardown = MagicMock()
    mock_collect = AsyncMock()

    @asynccontextmanager
    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()

    build_result = MagicMock(returncode=1, stderr="error: no solution found")

    with (
        patch("jig.init_workflow.run_init", new=AsyncMock()),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.runner.subprocess.run", return_value=build_result
        ) as mock_build,
        patch("jig.eval.collector.collect", mock_collect),
        patch("jig.eval.runner._teardown_proc", mock_teardown),
        patch("websockets.asyncio.client.connect", new=_fake_connect),
        caplog.at_level(logging.ERROR, logger="jig.eval.runner"),
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
    assert result.manifest_path is None
    mock_collect.assert_not_awaited()
    mock_teardown.assert_called_once()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("no solution found" in m for m in errors), (
        "build stderr must be logged on the TRACER_FAIL path"
    )
    build_call = mock_build.call_args
    assert build_call.args[0] == ["uv", "sync"]
    assert build_call.kwargs["cwd"].name == "test-proj"


def test_run_eval_passes_tracer_env_with_venv_path(tmp_path: Path) -> None:
    """collect() must receive tracer_env whose PATH starts with the project venv bin."""
    import os as _os
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    mock_proc, _FakeWS = _success_path_fakes(tmp_path)

    mock_manifest = MagicMock()
    mock_manifest.tracer = None  # short-circuits via TRACER_FAIL after collect
    mock_manifest.model_dump.return_value = {}
    mock_collect = AsyncMock(return_value=mock_manifest)

    @asynccontextmanager
    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()

    with (
        patch("jig.init_workflow.run_init", new=AsyncMock()),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.runner.subprocess.run",
            return_value=MagicMock(returncode=0),
        ),
        patch("jig.eval.collector.collect", mock_collect),
        patch("jig.eval.runner._teardown_proc"),
        patch("websockets.asyncio.client.connect", new=_fake_connect),
    ):
        asyncio.run(
            run_eval(
                "test-proj",
                label=None,
                keep=False,
                timeout_minutes=1,
                jig_repo=tmp_path,
            )
        )

    mock_collect.assert_awaited_once()
    call = mock_collect.await_args
    project_dir = call.args[0]
    assert project_dir.name == "test-proj", "collect must target the project subdir"
    tracer_env = call.kwargs["tracer_env"]
    expected_prefix = str(project_dir / ".venv" / "bin") + _os.pathsep
    assert tracer_env["PATH"].startswith(expected_prefix)


def test_run_eval_build_timeout_is_tracer_fail(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A hung uv sync must map to TRACER_FAIL, not crash the runner."""
    import logging
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock

    mock_proc, _FakeWS = _success_path_fakes(tmp_path)
    mock_teardown = MagicMock()
    mock_collect = AsyncMock()

    @asynccontextmanager
    async def _fake_connect(*args, **kwargs):
        yield _FakeWS()

    with (
        patch("jig.init_workflow.run_init", new=AsyncMock()),
        patch("jig.eval.runner.subprocess.Popen", return_value=mock_proc),
        patch(
            "jig.eval.runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="uv sync", timeout=300),
        ) as mock_build,
        patch("jig.eval.collector.collect", mock_collect),
        patch("jig.eval.runner._teardown_proc", mock_teardown),
        patch("websockets.asyncio.client.connect", new=_fake_connect),
        caplog.at_level(logging.ERROR, logger="jig.eval.runner"),
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
    mock_collect.assert_not_awaited()
    mock_teardown.assert_called_once()
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("timed out" in m for m in errors)
    build_call = mock_build.call_args
    assert build_call.args[0] == ["uv", "sync"]
    assert build_call.kwargs["cwd"].name == "test-proj"


def test_classify_completion_outcomes() -> None:
    assert _classify_completion("project_stuck", {}) == EvalOutcome.STUCK
    assert (
        _classify_completion("project_complete", {"tickets_failed": 2})
        == EvalOutcome.COMPLETED_WITH_FAILURES
    )
    assert (
        _classify_completion("project_complete", {"tickets_failed": 0})
        == EvalOutcome.SUCCESS
    )
    # Older daemons without the field classify as clean completion.
    assert _classify_completion("project_complete", {}) == EvalOutcome.SUCCESS
