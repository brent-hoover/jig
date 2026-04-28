"""Tests for the CLI event printer used during ``jig init``.

The printer subscribes to an EventEmitter and renders agent events
to stdout — heartbeats keep the user from thinking the run froze
during long Claude API calls or tool executions; tool-result events
show whether each tool succeeded.
"""
from __future__ import annotations

import asyncio

import click
import pytest

from jig.events import EventEmitter, JigEvent
from jig.init_workflow import _drain_emitter_to_stdout, _format_event


def test_format_event_text():
    out = _format_event(JigEvent("agent_text", {"role": "po", "text": " hello "}))
    assert out == "[po] hello"


def test_format_event_tool_calls_are_suppressed():
    """Tool invocations are implementation noise — the agent's text
    turns narrate what's happening. Operator transcript stays focused
    on the conversation."""
    out = _format_event(
        JigEvent(
            "agent_tool",
            {"role": "po", "tool": "Bash", "detail": "ls -la"},
        )
    )
    assert out is None
    out2 = _format_event(
        JigEvent(
            "agent_tool",
            {"role": "po", "tool": "mcp__jig__brief_list_sections"},
        )
    )
    assert out2 is None


def test_format_event_tool_result_success_is_suppressed():
    """Successful tool results don't render — the call event already
    showed the invocation, and the agent's next turn implicitly confirms
    success. Only failures need to surface."""
    out = _format_event(
        JigEvent(
            "agent_tool_result",
            {"role": "po", "tool": "Bash", "is_error": False, "excerpt": "ok"},
        )
    )
    assert out is None


def test_format_event_tool_result_error_with_excerpt():
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
    """ToolSearch is Claude Code's deferred-schema loader — internal
    plumbing. Even an error in it isn't operator-actionable."""
    out = _format_event(
        JigEvent(
            "agent_tool_result",
            {"role": "po", "tool": "ToolSearch", "is_error": True,
             "excerpt": "schema fetch failed"},
        )
    )
    assert out is None


def test_format_event_strips_mcp_prefix_in_error_path():
    """Tool errors that DO surface use the bare tool name, not the
    namespaced ``mcp__jig__brief_set_section`` form."""
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


async def _start_printer(emitter: EventEmitter, *, heartbeat: float) -> asyncio.Task:
    """Start the printer task and wait until it has subscribed so the
    next ``emit`` lands in its queue rather than being dropped."""
    task = asyncio.create_task(
        _drain_emitter_to_stdout(emitter, heartbeat_seconds=heartbeat)
    )
    # Yield until the subscriber registers; the task subscribes
    # synchronously on its first run.
    while not emitter._subscribers:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    return task


@pytest.mark.asyncio
async def test_heartbeat_fires_after_timeout(monkeypatch):
    """When no event arrives for the heartbeat window, the printer
    must echo a 'still working…' line so the user knows the agent
    isn't frozen."""
    echoes: list[str] = []
    monkeypatch.setattr(click, "echo", lambda s, *a, **kw: echoes.append(s))

    emitter = EventEmitter()
    task = await _start_printer(emitter, heartbeat=0.05)
    # Seed last_role so the heartbeat is labelled.
    await emitter.emit(JigEvent("agent_text", {"role": "po", "text": "hi"}))
    # Wait long enough for at least one heartbeat to fire.
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any("still working" in s for s in echoes), echoes
    assert any("[po]" in s for s in echoes if "still working" in s)


@pytest.mark.asyncio
async def test_heartbeat_uses_initial_role_when_no_event_yet(monkeypatch):
    """The first heartbeat fires before any agent event arrives, so it
    must use the role passed in by the spawn helper rather than a
    generic 'agent' fallback (otherwise the operator sees a misleading
    [agent] still working… line at the start of every spawn)."""
    echoes: list[str] = []
    monkeypatch.setattr(click, "echo", lambda s, *a, **kw: echoes.append(s))

    emitter = EventEmitter()
    task = await _start_printer(emitter, heartbeat=0.05)
    # No event emitted before the heartbeat window elapses.
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # initial_role is "agent" by default in this helper — pin the
    # custom-role test below.
    assert any("[agent] still working" in s for s in echoes), echoes


async def _start_printer_with_role(
    emitter: EventEmitter, role: str, *, heartbeat: float
) -> asyncio.Task:
    task = asyncio.create_task(
        _drain_emitter_to_stdout(
            emitter, heartbeat_seconds=heartbeat, initial_role=role
        )
    )
    while not emitter._subscribers:  # type: ignore[attr-defined]
        await asyncio.sleep(0)
    return task


@pytest.mark.asyncio
async def test_heartbeat_with_initial_role_label(monkeypatch):
    """When the spawn helper passes initial_role='po', the very first
    heartbeat says [po] still working — not [agent]."""
    echoes: list[str] = []
    monkeypatch.setattr(click, "echo", lambda s, *a, **kw: echoes.append(s))

    emitter = EventEmitter()
    task = await _start_printer_with_role(emitter, "po", heartbeat=0.05)
    await asyncio.sleep(0.15)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert any("[po] still working" in s for s in echoes), echoes
    assert not any("[agent] still working" in s for s in echoes)


@pytest.mark.asyncio
async def test_heartbeat_resets_after_event(monkeypatch):
    """Receiving an event resets the heartbeat — no spurious
    'still working' line right after activity."""
    echoes: list[str] = []
    monkeypatch.setattr(click, "echo", lambda s, *a, **kw: echoes.append(s))

    emitter = EventEmitter()
    task = await _start_printer(emitter, heartbeat=0.2)

    for i in range(4):
        await asyncio.sleep(0.05)
        await emitter.emit(
            JigEvent("agent_text", {"role": "po", "text": f"line {i}"})
        )
    # Drain whatever's queued before cancelling.
    await asyncio.sleep(0.05)

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not any("still working" in s for s in echoes)
    assert sum(1 for s in echoes if "line " in s) == 4
