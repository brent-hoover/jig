"""Tests for the JSONL store (plan step 3).

Covers: round-trip append + read; empty-store reads; cell-based counting;
keyword filter queries (cell fields and top-level fields); concurrent
appends serialized via asyncio.Lock don't corrupt lines; malformed lines
raise on read.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jig.evals.prompt_style_eval.models import (
    Cell,
    JudgeScore,
    RunRecord,
    StaticMetrics,
    TestResult,
)
from jig.evals.prompt_style_eval.store import Store


def _make_cell(**overrides: object) -> Cell:
    defaults: dict[str, object] = {
        "task_id": "todo_cli",
        "task_version": "v1",
        "prompt_id": "yaml_spec",
        "prompt_version": "sha256:abc",
        "model": "claude-opus-4-7",
        "model_snapshot": None,
        "temperature": 0.0,
        "rubric_version": "v1",
    }
    defaults.update(overrides)
    return Cell(**defaults)  # type: ignore[arg-type]


def _make_code_run(run_id: str, **overrides: object) -> RunRecord:
    defaults: dict[str, object] = {
        "run_id": run_id,
        "timestamp": datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC),
        "cell": _make_cell(),
        "prompt": "Build a todo CLI.",
        "transcript": [{"role": "assistant", "content": "ok"}],
        "outcome": "code",
        "extracted_files": {"todo.py": "print('hi')"},
        "test_result": TestResult(passed=True, n_passed=5, n_failed=0, duration_s=0.5),
        "static_metrics": StaticMetrics(loc=42, ruff_findings=0, cyclomatic_max=3),
        "judge": JudgeScore(
            model="claude-sonnet-4-6",
            rubric_version="v1",
            checklist={"type_hints_present": True},
            cost_usd=0.001,
        ),
        "candidate_tokens": {"input": 100, "output": 50},
        "candidate_cost_usd": 0.01,
    }
    defaults.update(overrides)
    return RunRecord(**defaults)  # type: ignore[arg-type]


def _make_question_run(run_id: str, **overrides: object) -> RunRecord:
    defaults: dict[str, object] = {
        "run_id": run_id,
        "timestamp": datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC),
        "cell": _make_cell(),
        "prompt": "Build it.",
        "transcript": [{"role": "assistant", "content": "Which language?"}],
        "outcome": "question",
        "candidate_tokens": {"input": 10, "output": 5},
        "candidate_cost_usd": 0.0001,
    }
    defaults.update(overrides)
    return RunRecord(**defaults)  # type: ignore[arg-type]


async def test_read_all_on_missing_file_yields_nothing(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    assert list(store.read_all()) == []


async def test_append_creates_parent_directory(tmp_path: Path) -> None:
    store = Store(tmp_path / "nested" / "deeper" / "runs.jsonl")
    await store.append(_make_code_run("r1"))
    assert store.path.exists()


async def test_append_then_read_roundtrips_records(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    originals = [_make_code_run(f"r{i}") for i in range(3)]
    for record in originals:
        await store.append(record)
    read_back = list(store.read_all())
    assert read_back == originals


async def test_count_matching_counts_only_same_cell(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    cell_a = _make_cell(prompt_id="yaml_spec")
    cell_b = _make_cell(prompt_id="prose_spec")
    for i in range(3):
        await store.append(_make_code_run(f"a{i}", cell=cell_a))
    for i in range(2):
        await store.append(_make_code_run(f"b{i}", cell=cell_b))
    assert store.count_matching(cell_a) == 3
    assert store.count_matching(cell_b) == 2


async def test_query_by_cell_field(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    await store.append(_make_code_run("y", cell=_make_cell(prompt_id="yaml_spec")))
    await store.append(_make_code_run("p", cell=_make_cell(prompt_id="prose_spec")))
    matches = list(store.query(prompt_id="yaml_spec"))
    assert [r.run_id for r in matches] == ["y"]


async def test_query_by_top_level_field(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    await store.append(_make_code_run("c1"))
    await store.append(_make_question_run("q1"))
    code_only = list(store.query(outcome="code"))
    assert [r.run_id for r in code_only] == ["c1"]
    question_only = list(store.query(outcome="question"))
    assert [r.run_id for r in question_only] == ["q1"]


async def test_query_combines_filters(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    await store.append(_make_code_run("a", cell=_make_cell(prompt_id="yaml_spec")))
    await store.append(_make_question_run("b", cell=_make_cell(prompt_id="yaml_spec")))
    await store.append(_make_code_run("c", cell=_make_cell(prompt_id="prose_spec")))
    matches = list(store.query(prompt_id="yaml_spec", outcome="code"))
    assert [r.run_id for r in matches] == ["a"]


async def test_query_rejects_unknown_filter(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    with pytest.raises(KeyError, match="unknown filter"):
        list(store.query(nope="value"))


async def test_concurrent_appends_dont_corrupt_lines(tmp_path: Path) -> None:
    """50 coroutines append concurrently; every record must round-trip."""
    store = Store(tmp_path / "runs.jsonl")
    records = [_make_code_run(f"r{i:03d}") for i in range(50)]
    await asyncio.gather(*(store.append(r) for r in records))
    read_back = list(store.read_all())
    assert len(read_back) == 50
    assert {r.run_id for r in read_back} == {r.run_id for r in records}


async def test_malformed_line_raises_on_read(tmp_path: Path) -> None:
    path = tmp_path / "runs.jsonl"
    path.write_text('{"not": "a valid record"}\n', encoding="utf-8")
    store = Store(path)
    with pytest.raises(ValueError, match="malformed record"):
        list(store.read_all())


async def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    store = Store(tmp_path / "runs.jsonl")
    await store.append(_make_code_run("r1"))
    with store.path.open("a", encoding="utf-8") as f:
        f.write("\n\n")
    await store.append(_make_code_run("r2"))
    ids = [r.run_id for r in store.read_all()]
    assert ids == ["r1", "r2"]
