"""Regression tests for brief auto-detection in analyze()."""

from pathlib import Path
from unittest.mock import patch

import pytest

from jig.evals.watcher.analyzer import analyze
from jig.evals.watcher.llm import LLMResult


@pytest.fixture()
def project_dir(tmp_path: Path) -> Path:
    store = tmp_path / ".jig" / "store"
    store.mkdir(parents=True)
    for name in ("tickets.jsonl", "comments.jsonl", "analytics.jsonl"):
        (store / name).write_text("")
    out_dir = tmp_path / ".jig" / "analysis" / "run-test"
    out_dir.mkdir(parents=True)
    return tmp_path


def _run_analyze(project_dir: Path, **kwargs) -> Path | None:
    captured: list[Path | None] = []

    def fake_llm(*, project_path, metrics, brief_path=None, **kw):
        captured.append(brief_path)
        return LLMResult(
            analysis_md="# Analysis\n",
            metrics_update={},
            cost_usd=0.0,
            tokens_in=0,
            tokens_out=0,
            num_turns=1,
        )

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


def test_explicit_brief_path_not_overridden_by_fallback(project_dir: Path) -> None:
    # canonical brief exists, but caller supplies a different explicit path
    (project_dir / "docs").mkdir(parents=True)
    (project_dir / "docs" / "brief.md").write_text("# Canonical")
    explicit = project_dir / "docs" / "other-brief.md"
    explicit.write_text("# Explicit")

    captured = _run_analyze(project_dir, brief_path=explicit)

    assert captured == explicit
