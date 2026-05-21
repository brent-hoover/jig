"""Tests for the runner (plan step 10).

Integration with the SDK is mocked; the rest of the pipeline (classifier,
sandbox, metrics, judge) runs for real because each is already covered by
its own unit tests and exercising them here flushes out any wiring issues.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

import jig.evals.prompt_style_eval.sdk as sdk_module
from jig.evals.prompt_style_eval.loaders import (
    load_prompt,
    load_rubric,
    load_task,
    task_tests_dir,
)
from jig.evals.prompt_style_eval.models import Cell
from jig.evals.prompt_style_eval.runner import run_cell


def _cell(prompt_id: str = "yaml_spec", prompt_version: str = "sha256:test") -> Cell:
    return Cell(
        task_id="todo_cli",
        task_version="v1",
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        model="claude-opus-4-7",
        model_snapshot=None,
        temperature=0.0,
        rubric_version="v1",
    )


def _result(*, cost: float = 0.001) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=cost,
        usage={"input_tokens": 100, "output_tokens": 50},
    )


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="claude-opus-4-7",
    )


def _make_query(candidate_messages: list[Any], judge_messages: list[Any]):
    """Return a fake ``query`` that alternates between candidate and judge
    invocations on successive calls."""
    call_count = {"n": 0}

    async def _query(*, prompt: str, options: Any) -> AsyncIterator[Any]:  # noqa: ARG001
        call_count["n"] += 1
        messages = candidate_messages if call_count["n"] == 1 else judge_messages
        for message in messages:
            yield message

    return _query


_TODO_REF_CODE = (Path(__file__).resolve().parent.parent.parent
                  / "jig" / "evals" / "prompt_style_eval" / "tasks" / "todo_cli" / "reference.py"
                  ).read_text(encoding="utf-8")


def _code_block(body: str) -> str:
    return f"Here's the solution:\n```python\n{body}\n```"


async def test_run_cell_code_outcome_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reference solution → expect outcome=code, tests passing, judge populated."""
    judge_response = (
        '{"type_hints_present": true, "no_bare_except": true, '
        '"function_names_descriptive": 5, "scope_appropriate": 5, '
        '"no_obvious_dead_code": true}'
    )
    monkeypatch.setattr(
        sdk_module,
        "query",
        _make_query(
            candidate_messages=[_assistant(_code_block(_TODO_REF_CODE)), _result()],
            judge_messages=[_assistant(judge_response), _result(cost=0.0003)],
        ),
    )

    task = load_task("todo_cli")
    prompt = load_prompt("todo_cli", "yaml_spec")
    rubric = load_rubric("v1")

    record = await run_cell(
        _cell(prompt_id=prompt.prompt_id, prompt_version=prompt.content_hash),
        prompt.text,
        task,
        task_tests_dir("todo_cli"),
        rubric,
        judge_model="claude-sonnet-4-6",
    )

    assert record.outcome == "code"
    assert record.test_result is not None
    assert record.test_result.passed is True
    assert record.test_result.n_passed >= 6
    assert record.static_metrics is not None
    assert record.judge is not None
    assert record.judge.checklist["type_hints_present"] is True


async def test_run_cell_question_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sdk_module,
        "query",
        _make_query(
            candidate_messages=[
                _assistant("Should I use SQLite or a JSON file for persistence?"),
                _result(),
            ],
            judge_messages=[],  # not reached
        ),
    )

    task = load_task("todo_cli")
    prompt = load_prompt("todo_cli", "prose_spec")
    rubric = load_rubric("v1")

    record = await run_cell(
        _cell(prompt_id=prompt.prompt_id, prompt_version=prompt.content_hash),
        prompt.text,
        task,
        task_tests_dir("todo_cli"),
        rubric,
        judge_model="claude-sonnet-4-6",
    )
    assert record.outcome == "question"
    assert record.test_result is None
    assert record.judge is None
    assert record.extracted_files is None


async def test_run_cell_sdk_error_becomes_error_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _explode(*, prompt: str, options: Any) -> AsyncIterator[Any]:  # noqa: ARG001
        raise RuntimeError("network down")
        yield  # unreachable, satisfies AsyncIterator

    monkeypatch.setattr(sdk_module, "query", _explode)

    task = load_task("todo_cli")
    prompt = load_prompt("todo_cli", "yaml_spec")
    rubric = load_rubric("v1")

    record = await run_cell(
        _cell(prompt_id=prompt.prompt_id, prompt_version=prompt.content_hash),
        prompt.text,
        task,
        task_tests_dir("todo_cli"),
        rubric,
        judge_model="claude-sonnet-4-6",
    )
    assert record.outcome == "error"
    assert record.transcript == []
    assert record.candidate_cost_usd == 0.0


async def test_run_cell_judge_failure_keeps_code_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sdk_module,
        "query",
        _make_query(
            candidate_messages=[_assistant(_code_block(_TODO_REF_CODE)), _result()],
            judge_messages=[
                _assistant("I'm not sure how to evaluate this code."),
                _result(),
            ],
        ),
    )

    task = load_task("todo_cli")
    prompt = load_prompt("todo_cli", "yaml_spec")
    rubric = load_rubric("v1")

    record = await run_cell(
        _cell(prompt_id=prompt.prompt_id, prompt_version=prompt.content_hash),
        prompt.text,
        task,
        task_tests_dir("todo_cli"),
        rubric,
        judge_model="claude-sonnet-4-6",
    )
    assert record.outcome == "code"
    assert record.test_result is not None
    assert record.judge is None
