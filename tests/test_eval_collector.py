"""Unit tests for jig.eval.collector — tracer environment threading."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from jig.eval.collector import collect


def _stub_project(tmp_path: Path) -> Path:
    """Minimal .jig/store layout so collect() can read its stores."""
    store = tmp_path / ".jig" / "store"
    store.mkdir(parents=True)
    for name in (
        "tickets.jsonl",
        "threads.jsonl",
        "review_comments.jsonl",
        "analytics.jsonl",
    ):
        (store / name).write_text("")
    return tmp_path


def _run_collect(project: Path, **kwargs):
    return asyncio.run(collect(project, run_id="r1", project_id="p1", **kwargs))


def test_tracer_env_reaches_subprocess(tmp_path: Path) -> None:
    project = _stub_project(tmp_path)
    env = {"PATH": "/fake/venv/bin:/usr/bin"}
    tr = MagicMock(returncode=0, stdout="PASS\n", stderr="")

    with patch("jig.eval.collector.subprocess.run", return_value=tr) as mock_run:
        manifest = _run_collect(project, tracer_cmd=["bash", "t.sh"], tracer_env=env)

    assert mock_run.call_args.kwargs["env"] == env
    assert manifest.tracer is not None
    assert manifest.tracer.passed is True


def test_tracer_env_omitted_inherits(tmp_path: Path) -> None:
    project = _stub_project(tmp_path)
    tr = MagicMock(returncode=0, stdout="PASS\n", stderr="")

    with patch("jig.eval.collector.subprocess.run", return_value=tr) as mock_run:
        _run_collect(project, tracer_cmd=["bash", "t.sh"])

    assert mock_run.call_args.kwargs["env"] is None
