"""Tests for the agent-event formatter used during ``jig init``.

By design, the printer hides everything except errors — agent text,
tool calls, and successful tool results are all duplicative noise
relative to the structured CLI prompts (questions, gap reports,
branch choices) that surface the agent's actual outputs. The
spawn-time UI (rule + spinner) is handled by ``_spawn_status`` which
uses rich's Status; not unit-tested directly because it's
display-only and Live composition is brittle to mock.
"""

from __future__ import annotations

from jig.events import JigEvent
from jig.init_workflow import _format_event


def test_format_event_text_is_suppressed():
    """Agent narrative duplicates the structured prompt content
    (e.g., the agent often restates the question it just asked).
    Hide it from the operator transcript."""
    out = _format_event(JigEvent("agent_text", {"role": "po", "text": " hello "}))
    assert out is None


def test_format_event_tool_calls_are_suppressed():
    """Tool invocations are implementation noise — the agent's actions
    surface through the structured prompts that follow."""
    assert (
        _format_event(
            JigEvent(
                "agent_tool",
                {"role": "po", "tool": "Bash", "detail": "ls -la"},
            )
        )
        is None
    )
    assert (
        _format_event(
            JigEvent(
                "agent_tool",
                {"role": "po", "tool": "mcp__jig__brief_list_sections"},
            )
        )
        is None
    )


def test_format_event_tool_result_success_is_suppressed():
    """Successful results don't render — failures are the only thing
    the operator needs to see from the tool stream."""
    out = _format_event(
        JigEvent(
            "agent_tool_result",
            {"role": "po", "tool": "Bash", "is_error": False, "excerpt": "ok"},
        )
    )
    assert out is None


def test_format_event_tool_result_error_with_excerpt():
    """Failures DO surface, with the bare tool name and excerpt."""
    out = _format_event(
        JigEvent(
            "agent_tool_result",
            {
                "role": "po",
                "tool": "Bash",
                "is_error": True,
                "excerpt": "fatal: not a git repository",
            },
        )
    )
    assert "✗ Bash" in out
    assert "not a git repository" in out


def test_format_event_unknown_type_returns_none():
    assert _format_event(JigEvent("agent_thinking", {"role": "po"})) is None


def test_format_event_suppresses_toolsearch_errors_too():
    """ToolSearch is internal Claude Code plumbing — the operator
    can't act on a ToolSearch failure, so don't surface it."""
    out = _format_event(
        JigEvent(
            "agent_tool_result",
            {
                "role": "po",
                "tool": "ToolSearch",
                "is_error": True,
                "excerpt": "schema fetch failed",
            },
        )
    )
    assert out is None


def test_format_event_strips_mcp_prefix_in_error_path():
    """Surfaced tool errors use the bare tool name, not
    ``mcp__jig__brief_set_section``."""
    out = _format_event(
        JigEvent(
            "agent_tool_result",
            {
                "role": "po",
                "tool": "mcp__jig__brief_set_section",
                "is_error": True,
                "excerpt": "section not found",
            },
        )
    )
    assert "brief_set_section" in out
    assert "mcp__" not in out
