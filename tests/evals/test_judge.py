"""Tests for the LLM-as-judge (plan step 8).

Most tests exercise ``parse_judge_response`` and ``build_judge_prompt``
directly; the ``score()`` async test patches ``judge.invoke`` to return a
canned transcript so we never hit a real model.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

import jig.evals.prompt_style_eval.judge as judge_module
import jig.evals.prompt_style_eval.sdk as sdk_module
from jig.evals.prompt_style_eval.judge import (
    JudgeError,
    build_judge_prompt,
    parse_judge_response,
    score,
)
from jig.evals.prompt_style_eval.models import Rubric, RubricItem


def _rubric_bool_only() -> Rubric:
    return Rubric(
        version="v1",
        items=[
            RubricItem(id="type_hints_present", prompt="?", scale="bool"),
            RubricItem(id="no_bare_except", prompt="?", scale="bool"),
        ],
    )


def _rubric_mixed() -> Rubric:
    return Rubric(
        version="v1",
        items=[
            RubricItem(id="type_hints_present", prompt="?", scale="bool"),
            RubricItem(id="function_names_descriptive", prompt="?", scale="likert_5"),
        ],
    )


# --- build_judge_prompt ----------------------------------------------------


def test_build_prompt_includes_each_item_id() -> None:
    prompt = build_judge_prompt("x = 1", _rubric_mixed())
    assert "type_hints_present" in prompt
    assert "function_names_descriptive" in prompt


def test_build_prompt_uses_bool_and_likert_hints() -> None:
    prompt = build_judge_prompt("x = 1", _rubric_mixed())
    assert "true/false" in prompt
    assert "1-5" in prompt


def test_build_prompt_embeds_code_in_python_fence() -> None:
    prompt = build_judge_prompt("def f(): return 1", _rubric_bool_only())
    assert "```python\ndef f(): return 1\n```" in prompt


# --- parse_judge_response: happy paths -------------------------------------


def test_parse_bare_json_object() -> None:
    raw = '{"type_hints_present": true, "no_bare_except": false}'
    out = parse_judge_response(raw, _rubric_bool_only())
    assert out == {"type_hints_present": True, "no_bare_except": False}


def test_parse_json_wrapped_in_fence() -> None:
    raw = '```json\n{"type_hints_present": true, "no_bare_except": true}\n```'
    out = parse_judge_response(raw, _rubric_bool_only())
    assert out == {"type_hints_present": True, "no_bare_except": True}


def test_parse_json_wrapped_in_unlabeled_fence() -> None:
    raw = '```\n{"type_hints_present": true, "no_bare_except": true}\n```'
    out = parse_judge_response(raw, _rubric_bool_only())
    assert out["type_hints_present"] is True


def test_parse_mixed_scale_response() -> None:
    raw = '{"type_hints_present": true, "function_names_descriptive": 4}'
    out = parse_judge_response(raw, _rubric_mixed())
    assert out == {"type_hints_present": True, "function_names_descriptive": 4}


def test_parse_ignores_extra_keys() -> None:
    raw = (
        '{"type_hints_present": true, "function_names_descriptive": 3, '
        '"commentary": "looks good"}'
    )
    out = parse_judge_response(raw, _rubric_mixed())
    assert "commentary" not in out
    assert out["function_names_descriptive"] == 3


# --- parse_judge_response: error paths -------------------------------------


def test_parse_raises_on_malformed_json() -> None:
    with pytest.raises(JudgeError, match="parse JSON"):
        parse_judge_response("not even close to json", _rubric_bool_only())


def test_parse_raises_on_non_object_root() -> None:
    with pytest.raises(JudgeError, match="expected JSON object"):
        parse_judge_response("[true, false]", _rubric_bool_only())


def test_parse_raises_on_missing_item() -> None:
    raw = '{"type_hints_present": true}'  # missing no_bare_except
    with pytest.raises(JudgeError, match="missing rubric item: no_bare_except"):
        parse_judge_response(raw, _rubric_bool_only())


def test_parse_raises_on_wrong_type_for_bool() -> None:
    raw = '{"type_hints_present": "yes", "no_bare_except": true}'
    with pytest.raises(JudgeError, match="type_hints_present: expected bool"):
        parse_judge_response(raw, _rubric_bool_only())


def test_parse_raises_on_int_disguised_as_bool() -> None:
    """1/0 are not valid booleans for our checklist."""
    raw = '{"type_hints_present": 1, "no_bare_except": 0}'
    with pytest.raises(JudgeError):
        parse_judge_response(raw, _rubric_bool_only())


def test_parse_raises_on_bool_disguised_as_likert() -> None:
    """``True``/``False`` are not valid likert scores (and `isinstance(True, int)`
    is True, so we must guard against this)."""
    raw = '{"type_hints_present": true, "function_names_descriptive": true}'
    with pytest.raises(JudgeError, match="function_names_descriptive: expected int"):
        parse_judge_response(raw, _rubric_mixed())


def test_parse_raises_on_likert_out_of_range() -> None:
    raw = '{"type_hints_present": true, "function_names_descriptive": 7}'
    with pytest.raises(JudgeError, match="out of range"):
        parse_judge_response(raw, _rubric_mixed())


def test_parse_raises_on_likert_zero() -> None:
    raw = '{"type_hints_present": true, "function_names_descriptive": 0}'
    with pytest.raises(JudgeError, match="out of range"):
        parse_judge_response(raw, _rubric_mixed())


# --- score() integration with mocked SDK -----------------------------------


def _fake_query_factory(messages: list[Any]):
    async def _query(*, prompt: str, options: Any) -> AsyncIterator[Any]:  # noqa: ARG001
        for message in messages:
            yield message

    return _query


def _result_message(*, cost: float = 0.0012, usage: dict[str, Any] | None = None) -> ResultMessage:
    return ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=False,
        num_turns=1,
        session_id="s1",
        total_cost_usd=cost,
        usage=usage or {"input_tokens": 30, "output_tokens": 20},
    )


def _assistant(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model="claude-sonnet-4-6",
    )


async def test_score_returns_judge_score_with_tokens_and_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rubric = _rubric_mixed()
    monkeypatch.setattr(
        sdk_module,
        "query",
        _fake_query_factory(
            [
                _assistant(
                    '{"type_hints_present": true, "function_names_descriptive": 5}'
                ),
                _result_message(cost=0.002, usage={"input_tokens": 100, "output_tokens": 20}),
            ]
        ),
    )
    result = await score("x: int = 1", rubric, model="claude-sonnet-4-6")
    assert result.model == "claude-sonnet-4-6"
    assert result.rubric_version == "v1"
    assert result.checklist == {
        "type_hints_present": True,
        "function_names_descriptive": 5,
    }
    assert result.cost_usd == pytest.approx(0.002)
    assert result.tokens == {"input_tokens": 100, "output_tokens": 20}


async def test_score_raises_judge_error_on_unparseable_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rubric = _rubric_bool_only()
    monkeypatch.setattr(
        sdk_module,
        "query",
        _fake_query_factory(
            [
                _assistant("Hmm, hard to say. Maybe true?"),
                _result_message(),
            ]
        ),
    )
    with pytest.raises(JudgeError):
        await score("x = 1", rubric, model="claude-sonnet-4-6")


async def test_score_raises_judge_error_when_no_assistant_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rubric = _rubric_bool_only()
    monkeypatch.setattr(
        sdk_module,
        "query",
        _fake_query_factory([_result_message()]),
    )
    with pytest.raises(JudgeError, match="no assistant text"):
        await score("x = 1", rubric, model="claude-sonnet-4-6")


# --- silence unused-import lint --------------------------------------------


_ = judge_module  # ensure the module import path is exercised at test time
