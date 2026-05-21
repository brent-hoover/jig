"""Tests for the prompt-style-eval pydantic models (plan step 2).

Focus on the load-bearing validators (outcome ↔ code-fields consistency,
duplicate rubric item ids) and JSON round-trip for the main record type.
"""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from jig.evals.prompt_style_eval.models import (
    Cell,
    JudgeScore,
    Rubric,
    RubricItem,
    RunRecord,
    StaticMetrics,
    Task,
    TestResult,
)


def _make_cell(**overrides: object) -> Cell:
    defaults: dict[str, object] = {
        "task_id": "todo_cli",
        "task_version": "v1",
        "prompt_id": "yaml_spec",
        "prompt_version": "sha256:abc123",
        "model": "claude-opus-4-7",
        "model_snapshot": None,
        "temperature": 0.0,
        "rubric_version": "v1",
    }
    defaults.update(overrides)
    return Cell(**defaults)  # type: ignore[arg-type]


def _make_code_run(**overrides: object) -> RunRecord:
    defaults: dict[str, object] = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "timestamp": datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC),
        "cell": _make_cell(),
        "prompt": "Build a todo CLI.",
        "transcript": [{"role": "assistant", "content": "..."}],
        "outcome": "code",
        "extracted_files": {"todo.py": "print('hi')"},
        "test_result": TestResult(passed=True, n_passed=5, n_failed=0, duration_s=0.5),
        "static_metrics": StaticMetrics(loc=42, ruff_findings=0, cyclomatic_max=3),
        "judge": JudgeScore(
            model="claude-sonnet-4-6",
            rubric_version="v1",
            checklist={"type_hints_present": True, "scope_appropriate": 4},
            cost_usd=0.001,
        ),
        "candidate_tokens": {"input": 100, "output": 50},
        "candidate_cost_usd": 0.01,
    }
    defaults.update(overrides)
    return RunRecord(**defaults)  # type: ignore[arg-type]


def test_task_rejects_zero_timeout() -> None:
    with pytest.raises(ValidationError):
        Task(
            id="t",
            version="v1",
            entrypoint="t.py",
            test_command=["pytest"],
            timeout_s=0,
        )


def test_rubric_rejects_duplicate_item_ids() -> None:
    items = [
        RubricItem(id="a", prompt="?", scale="bool"),
        RubricItem(id="a", prompt="?", scale="bool"),
    ]
    with pytest.raises(ValidationError) as exc:
        Rubric(version="v1", items=items)
    assert "duplicate" in str(exc.value).lower()


def test_code_outcome_requires_all_code_specific_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        _make_code_run(extracted_files=None)
    assert "extracted_files" in str(exc.value)


def test_code_outcome_requires_test_result() -> None:
    with pytest.raises(ValidationError) as exc:
        _make_code_run(test_result=None)
    assert "test_result" in str(exc.value)


def test_non_code_outcome_rejects_code_specific_fields() -> None:
    with pytest.raises(ValidationError) as exc:
        _make_code_run(outcome="question")
    assert "must not have code-specific fields" in str(exc.value)


def test_question_outcome_is_valid_without_code_fields() -> None:
    record = RunRecord(
        run_id="00000000-0000-0000-0000-000000000002",
        timestamp=datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC),
        cell=_make_cell(),
        prompt="Build a todo CLI.",
        transcript=[],
        outcome="question",
        candidate_tokens={"input": 50, "output": 5},
        candidate_cost_usd=0.001,
    )
    assert record.outcome == "question"
    assert record.test_result is None
    assert record.judge is None


def test_run_record_json_roundtrip_code_outcome() -> None:
    original = _make_code_run()
    payload = original.model_dump_json()
    restored = RunRecord.model_validate_json(payload)
    assert restored == original


def test_run_record_json_roundtrip_question_outcome() -> None:
    original = RunRecord(
        run_id="00000000-0000-0000-0000-000000000003",
        timestamp=datetime(2026, 5, 12, 12, 0, 0, tzinfo=UTC),
        cell=_make_cell(),
        prompt="Build it.",
        transcript=[{"role": "assistant", "content": "Which language?"}],
        outcome="question",
        candidate_tokens={"input": 10, "output": 5},
        candidate_cost_usd=0.0001,
    )
    payload = original.model_dump_json()
    restored = RunRecord.model_validate_json(payload)
    assert restored == original


def test_judge_checklist_accepts_mixed_bool_and_int() -> None:
    score = JudgeScore(
        model="claude-sonnet-4-6",
        rubric_version="v1",
        checklist={"a_bool": True, "a_likert": 3},
        cost_usd=0.001,
    )
    assert score.checklist["a_bool"] is True
    assert score.checklist["a_likert"] == 3


def test_cell_equality_uses_all_fields() -> None:
    a = _make_cell()
    b = _make_cell()
    assert a == b
    c = _make_cell(temperature=0.7)
    assert a != c


def test_code_outcome_allows_missing_judge() -> None:
    """Judge failures shouldn't invalidate a code-outcome run record."""
    record = _make_code_run(judge=None)
    assert record.outcome == "code"
    assert record.judge is None
    assert record.test_result is not None


def test_derived_record_carries_derived_from() -> None:
    derived = _make_code_run(
        run_id="00000000-0000-0000-0000-000000000004",
        derived_from="00000000-0000-0000-0000-000000000001",
        candidate_tokens={"input": 0, "output": 0},
        candidate_cost_usd=0.0,
    )
    assert derived.derived_from == "00000000-0000-0000-0000-000000000001"
    assert derived.candidate_cost_usd == 0.0
