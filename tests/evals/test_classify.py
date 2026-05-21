"""Tests for the outcome classifier (plan step 6).

Table-driven cases for every bucket plus targeted tests for the ambiguous
cases the design calls out — code-with-trailing-question, refusal-with-code,
multiple code blocks, no assistant message at all.
"""

import pytest

from jig.evals.prompt_style_eval.classify import (
    PostHocOutcome,
    classify,
    extract_assistant_text,
    extract_code,
)


def _assistant(text: str) -> dict:
    return {
        "type": "AssistantMessage",
        "content": [{"type": "TextBlock", "text": text}],
    }


def _result() -> dict:
    return {
        "type": "ResultMessage",
        "subtype": "success",
        "total_cost_usd": 0.001,
    }


# --- extract_assistant_text ------------------------------------------------


def test_extract_text_returns_none_on_empty_transcript() -> None:
    assert extract_assistant_text([]) is None


def test_extract_text_returns_none_when_no_assistant_message() -> None:
    assert extract_assistant_text([_result()]) is None


def test_extract_text_returns_none_when_assistant_has_no_text_blocks() -> None:
    transcript = [{"type": "AssistantMessage", "content": [{"type": "ToolUseBlock"}]}]
    assert extract_assistant_text(transcript) is None


def test_extract_text_concatenates_multiple_text_blocks() -> None:
    transcript = [
        {
            "type": "AssistantMessage",
            "content": [
                {"type": "TextBlock", "text": "part one "},
                {"type": "TextBlock", "text": "part two"},
            ],
        }
    ]
    assert extract_assistant_text(transcript) == "part one part two"


def test_extract_text_uses_last_assistant_message() -> None:
    transcript = [
        _assistant("first"),
        _assistant("second"),
        _result(),
    ]
    assert extract_assistant_text(transcript) == "second"


# --- extract_code ----------------------------------------------------------


def test_extract_code_none_when_no_fence() -> None:
    assert extract_code("just prose without fences") is None


def test_extract_code_returns_block_contents_without_fences() -> None:
    text = "Here it is:\n```python\nprint('hi')\n```\nThat's all."
    assert extract_code(text) == "print('hi')"


def test_extract_code_accepts_no_language_tag() -> None:
    text = "```\nx = 1\n```"
    assert extract_code(text) == "x = 1"


def test_extract_code_concats_multiple_blocks() -> None:
    text = "First:\n```python\nimport os\n```\nThen:\n```python\nos.getcwd()\n```"
    code = extract_code(text)
    assert code is not None
    assert "import os" in code
    assert "os.getcwd()" in code
    assert code.count("\n\n") >= 1


# --- classify --------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("```python\nprint('hi')\n```", "code"),
        ("Should I use Python or JavaScript?", "question"),
        ("Just some prose with no clear shape.", "malformed"),
        ("I can't help with that.", "refusal"),
        ("I cannot complete this task.", "refusal"),
        ("I won't do that.", "refusal"),
        ("I'm not able to do this.", "refusal"),
        ("Sorry, I can't help here.", "refusal"),
    ],
)
def test_classify_buckets(text: str, expected: PostHocOutcome) -> None:
    assert classify([_assistant(text), _result()]) == expected


def test_classify_code_with_trailing_question_is_code() -> None:
    """Per design: code-bearing-question resolves to code, not question."""
    text = "Here:\n```python\nprint('hi')\n```\nDoes that work?"
    assert classify([_assistant(text), _result()]) == "code"


def test_classify_refusal_with_code_block_is_refusal() -> None:
    """Refusal trumps code — the model declined despite producing pseudo-code."""
    text = (
        "I cannot help build this. "
        "If I were to, it might look like:\n```python\npass\n```"
    )
    assert classify([_assistant(text), _result()]) == "refusal"


def test_classify_empty_transcript_is_malformed() -> None:
    assert classify([]) == "malformed"


def test_classify_no_assistant_is_malformed() -> None:
    assert classify([_result()]) == "malformed"


def test_classify_indented_question_mark_still_a_question() -> None:
    text = "Could you clarify what storage format you want?\n   "
    assert classify([_assistant(text), _result()]) == "question"
