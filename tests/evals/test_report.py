"""Tests for the report module (plan step 12).

Synthetic records exercise: per-cell aggregation, outcome bucketing,
pass-rate over all runs (not just code outcomes), bootstrap CI shape,
static-metric and judge-mean computation, judge coverage tracking.
"""

from datetime import UTC, datetime

from jig.evals.prompt_style_eval.models import (
    Cell,
    JudgeScore,
    RunRecord,
    StaticMetrics,
    TestResult,
)
from jig.evals.prompt_style_eval.report import (
    aggregate,
    bootstrap_ci,
    render_json,
    render_text,
)


def _cell(
    prompt_id: str = "yaml_spec",
    task_version: str = "v1",
    model_snapshot: str | None = None,
    temperature: float = 0.0,
) -> Cell:
    return Cell(
        task_id="todo_cli",
        task_version=task_version,
        prompt_id=prompt_id,
        prompt_version="sha256:x",
        model="claude-opus-4-7",
        model_snapshot=model_snapshot,
        temperature=temperature,
        rubric_version="v1",
    )


def _code_run(
    run_id: str,
    *,
    prompt_id: str = "yaml_spec",
    task_version: str = "v1",
    model_snapshot: str | None = None,
    temperature: float = 0.0,
    passed: bool = True,
    loc: int = 50,
    judge: bool = True,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        timestamp=datetime(2026, 5, 12, tzinfo=UTC),
        cell=_cell(prompt_id, task_version, model_snapshot, temperature),
        prompt="x",
        transcript=[],
        outcome="code",
        extracted_files={"todo.py": "print('hi')"},
        test_result=TestResult(
            passed=passed,
            n_passed=5 if passed else 4,
            n_failed=0 if passed else 1,
            duration_s=0.1,
        ),
        static_metrics=StaticMetrics(
            loc=loc,
            ruff_findings=0,
            cyclomatic_max=2,
        ),
        judge=(
            JudgeScore(
                model="claude-sonnet-4-6",
                rubric_version="v1",
                checklist={"type_hints_present": True, "scope_appropriate": 4},
                cost_usd=0.001,
            )
            if judge
            else None
        ),
        candidate_tokens={"input": 100, "output": 50},
        candidate_cost_usd=0.01,
    )


def _question_run(
    run_id: str,
    prompt_id: str = "yaml_spec",
    task_version: str = "v1",
    model_snapshot: str | None = None,
    temperature: float = 0.0,
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        timestamp=datetime(2026, 5, 12, tzinfo=UTC),
        cell=_cell(prompt_id, task_version, model_snapshot, temperature),
        prompt="x",
        transcript=[],
        outcome="question",
        candidate_tokens={"input": 50, "output": 5},
        candidate_cost_usd=0.001,
    )


def test_aggregate_groups_by_task_and_prompt() -> None:
    records = [
        _code_run("a", prompt_id="yaml_spec"),
        _code_run("b", prompt_id="yaml_spec"),
        _code_run("c", prompt_id="prose_spec"),
    ]
    report = aggregate(records)
    assert len(report.cells) == 2
    keys = {(c.task_id, c.prompt_id) for c in report.cells}
    assert keys == {("todo_cli", "yaml_spec"), ("todo_cli", "prose_spec")}


def test_aggregate_groups_by_task_version() -> None:
    records = [
        _code_run("a", task_version="v1"),
        _code_run("b", task_version="v2"),
    ]

    report = aggregate(records)

    assert len(report.cells) == 2
    assert {cell.task_version for cell in report.cells} == {"v1", "v2"}


def test_aggregate_groups_by_model_snapshot_and_temperature() -> None:
    records = [
        _code_run("a", model_snapshot="s1", temperature=0.0),
        _code_run("b", model_snapshot="s1", temperature=0.7),
        _code_run("c", model_snapshot="s2", temperature=0.0),
    ]

    report = aggregate(records)

    assert len(report.cells) == 3
    keys = {(cell.model_snapshot, cell.temperature) for cell in report.cells}
    assert keys == {("s1", 0.0), ("s1", 0.7), ("s2", 0.0)}


def test_aggregate_sorts_mixed_snapshot_values() -> None:
    records = [
        _code_run("a", model_snapshot=None),
        _code_run("b", model_snapshot="s1"),
    ]

    report = aggregate(records)

    assert [cell.model_snapshot for cell in report.cells] == [None, "s1"]


def test_pass_rate_counts_non_code_outcomes_as_failures() -> None:
    records = [
        _code_run("a", passed=True),
        _code_run("b", passed=True),
        _question_run("q"),
    ]
    [cell] = aggregate(records).cells
    assert cell.n == 3
    assert cell.pass_rate == 2 / 3
    assert cell.outcomes["code"] == 2
    assert cell.outcomes["question"] == 1


def test_static_mean_only_over_code_outcomes() -> None:
    records = [
        _code_run("a", loc=40),
        _code_run("b", loc=60),
        _question_run("q"),
    ]
    [cell] = aggregate(records).cells
    assert cell.static_mean["loc"] == 50.0


def test_judge_mean_only_over_runs_with_judge() -> None:
    records = [
        _code_run("a", judge=True),
        _code_run("b", judge=True),
        _code_run("c", judge=False),  # judge failed
    ]
    [cell] = aggregate(records).cells
    assert cell.judge_coverage == 2
    # Mean over the two judged runs, both have scope_appropriate=4.
    assert cell.judge_mean["scope_appropriate"] == 4.0


def test_total_cost_sums_candidate_and_judge() -> None:
    records = [_code_run("a"), _code_run("b")]
    [cell] = aggregate(records).cells
    # candidate: 0.01 each = 0.02; judge: 0.001 each = 0.002.
    assert abs(cell.total_cost_usd - 0.022) < 1e-9


def test_bootstrap_ci_is_inclusive_when_all_pass() -> None:
    lo, hi = bootstrap_ci([1.0, 1.0, 1.0])
    assert lo == 1.0
    assert hi == 1.0


def test_bootstrap_ci_widens_with_variance() -> None:
    lo, hi = bootstrap_ci([1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0])
    assert lo < hi
    assert 0.0 <= lo <= hi <= 1.0


def test_render_text_includes_cell_summary() -> None:
    records = [_code_run("a"), _code_run("b")]
    text = render_text(aggregate(records))
    assert "todo_cli" in text
    assert "v1" in text
    assert "yaml_spec" in text
    assert "temp=0" in text
    assert "pass rate" in text


def test_render_text_handles_empty_report() -> None:
    text = render_text(aggregate([]))
    assert "no records" in text


def test_render_json_roundtrips() -> None:
    import json as _json

    records = [_code_run("a"), _code_run("b"), _question_run("q")]
    payload = render_json(aggregate(records))
    data = _json.loads(payload)
    assert "cells" in data
    assert data["cells"][0]["n"] == 3
    assert data["cells"][0]["task_version"] == "v1"
    assert data["cells"][0]["temperature"] == 0.0
    assert data["cells"][0]["model_snapshot"] is None
