"""Regression tests for brief auto-detection in analyze()."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jig.evals.watcher.analyzer import analyze


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    store = tmp_path / ".jig" / "store"
    store.mkdir(parents=True)
    for name in ("tickets.jsonl", "comments.jsonl", "analytics.jsonl"):
        (store / name).write_text("")
    out_dir = tmp_path / ".jig" / "analysis" / "run-test"
    out_dir.mkdir(parents=True)
    return tmp_path


def _run_analyze(project_dir: Path, **kwargs) -> MagicMock:
    captured: list[Path | None] = []

    def fake_llm(*, project_path, metrics, brief_path=None, **kw):
        captured.append(brief_path)
        result = MagicMock()
        result.metrics_update = {}
        result.narrative = ""
        result.analysis_md = ""
        return result

    with patch("jig.evals.watcher.llm.run_llm_analysis", side_effect=fake_llm):
        analyze(
            project_path=project_dir,
            run_id="run-test",
            out_dir=project_dir / ".jig" / "analysis" / "run-test",
            jig_repo=project_dir,
            project_name="test-project",
            use_llm=True,
            **kwargs,
        )

    assert captured, "run_llm_analysis was not called"
    return captured[0]


def test_brief_auto_detected(project_dir: Path) -> None:
    brief = project_dir / "docs" / "brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# Brief\nDo the thing.")

    captured = _run_analyze(project_dir)

    assert captured == brief


def test_brief_absent_preserves_behavior(project_dir: Path) -> None:
    captured = _run_analyze(project_dir)

    assert captured is None
