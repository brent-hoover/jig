"""Tests for the SDK wrapper (plan step 5).

We never touch a real model in unit tests; the ``claude_agent_sdk.query``
function is monkeypatched to yield a constructed sequence of SDK messages.
Tests confirm: tokens and cost are extracted from ``ResultMessage``; the
transcript captures every message in order; nested content blocks survive
serialization.

The fakes use real SDK dataclass types (``AssistantMessage``, ``TextBlock``,
``ResultMessage``) so the production code's ``isinstance(message,
ResultMessage)`` check fires the way it does in production. Constructing
them requires their non-defaulted fields — small helpers below keep test
bodies focused.
"""

from collections.abc import AsyncIterator
from typing import Any

import pytest
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock

import evals.prompt_style_eval.sdk as sdk_module
from evals.prompt_style_eval.sdk import InvocationResult, invoke, serialize_message


_SENTINEL = object()


def _result_message(
    *,
    total_cost_usd: float | None = 0.0023,
    usage: Any = _SENTINEL,
    stop_reason: str | None = "end_turn",
) -> ResultMessage:
    resolved_usage = (
        {"input_tokens": 50, "output_tokens": 30}
        if usage is _SENTINEL
        else usage
    )
    return ResultMessage(
        subtype="success",
        duration_ms=100,
        duration_api_ms=80,
        is_error=False,
        num_turns=1,
        session_id="s1",
        stop_reason=stop_reason,
        total_cost_usd=total_cost_usd,
        usage=resolved_usage,
    )


def _assistant_message(*texts: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextBlock(text=t) for t in texts],
        model="claude-opus-4-7",
    )


def _fake_query_factory(messages: list[Any]):
    async def _query(*, prompt: str, options: Any) -> AsyncIterator[Any]:  # noqa: ARG001
        for message in messages:
            yield message

    return _query


@pytest.fixture
def patch_query(monkeypatch: pytest.MonkeyPatch):
    def install(messages: list[Any]) -> None:
        monkeypatch.setattr(sdk_module, "query", _fake_query_factory(messages))

    return install


async def test_invoke_extracts_tokens_and_cost(patch_query) -> None:
    patch_query([_assistant_message("ok"), _result_message()])
    result: InvocationResult = await invoke("Build it.", model="claude-opus-4-7")
    assert result.cost_usd == pytest.approx(0.0023)
    assert result.tokens == {"input_tokens": 50, "output_tokens": 30}
    assert result.stop_reason == "end_turn"


async def test_invoke_captures_full_transcript(patch_query) -> None:
    patch_query([_assistant_message("here is code"), _result_message()])
    result = await invoke("hi", model="claude-opus-4-7")
    assert len(result.transcript) == 2
    assistant_dict, result_dict = result.transcript
    assert assistant_dict["type"] == "AssistantMessage"
    assert result_dict["type"] == "ResultMessage"


async def test_invoke_serializes_nested_content_blocks(patch_query) -> None:
    patch_query([_assistant_message("line one", "line two"), _result_message()])
    result = await invoke("hi", model="claude-opus-4-7")
    blocks = result.transcript[0]["content"]
    assert [b["text"] for b in blocks] == ["line one", "line two"]


async def test_invoke_handles_missing_cost_and_usage(patch_query) -> None:
    patch_query(
        [
            _assistant_message("ok"),
            _result_message(total_cost_usd=None, usage=None),
        ]
    )
    result = await invoke("hi", model="claude-opus-4-7")
    assert result.cost_usd == 0.0
    assert result.tokens == {}


async def test_invoke_only_captures_int_usage_values(patch_query) -> None:
    """Non-numeric usage fields (e.g. cache strings) are dropped, not coerced."""
    patch_query(
        [
            _assistant_message("ok"),
            _result_message(
                usage={
                    "input_tokens": 12,
                    "output_tokens": 8,
                    "cache_creation": "n/a",
                }
            ),
        ]
    )
    result = await invoke("hi", model="claude-opus-4-7")
    assert result.tokens == {"input_tokens": 12, "output_tokens": 8}


def test_serialize_message_preserves_type_name() -> None:
    block = TextBlock(text="hello")
    out = serialize_message(block)
    assert out["type"] == "TextBlock"
    assert out["text"] == "hello"


def test_serialize_message_handles_non_dataclass_with_dict() -> None:
    class Bag:
        def __init__(self) -> None:
            self.a = 1
            self.b = "two"

    out = serialize_message(Bag())
    assert out == {"type": "Bag", "a": 1, "b": "two"}
