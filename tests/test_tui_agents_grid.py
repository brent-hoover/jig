"""Tests for the AgentsScreen card grid.

Replaces what used to be a list+detail layout — operators now see
every agent in a 3-column grid of compact cards. Card border colour
signals health (active / stuck / inactive). These tests pin the
public contract:

  - Empty state visible until the first agent arrives.
  - Each agent gets exactly one ``_AgentCard`` in the grid.
  - The card's CSS state classes (``stuck`` / ``inactive``) update
    reactively from the agent's elapsed / current_tool / active flags.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from textual.containers import Grid

from jig.tui.app import JigApp
from jig.tui.screens.agents import AgentsScreen, _AgentCard, _STUCK_THRESHOLD_S


def _agent_start(role: str, ticket_id: str, ticket_title: str = "T-test") -> dict:
    return {
        "role": role,
        "ticket_id": ticket_id,
        "ticket_title": ticket_title,
        "phase": "implement",
    }


@pytest.mark.asyncio
async def test_agents_screen_starts_empty(tmp_path: Path) -> None:
    """No agents → empty-msg visible, grid hidden."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)
        empty = screen.query_one("#empty-msg")
        grid = screen.query_one("#agents-grid", Grid)
        assert empty.display is True
        assert grid.display is False
        # No cards yet.
        cards = list(grid.query(_AgentCard))
        assert cards == []


@pytest.mark.asyncio
async def test_agents_screen_mounts_card_per_agent(tmp_path: Path) -> None:
    """Each agent_start event spawns a card; empty-msg disappears."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)

        screen.handle_agent_start(_agent_start("dev", "T-1"))
        screen.handle_agent_start(_agent_start("test", "T-2"))

        grid = screen.query_one("#agents-grid", Grid)
        empty = screen.query_one("#empty-msg")
        cards = list(grid.query(_AgentCard))
        assert len(cards) == 2
        assert {c.agent_key for c in cards} == {"T-1:dev", "T-2:test"}
        assert empty.display is False
        assert grid.display is True


@pytest.mark.asyncio
async def test_card_marked_stuck_when_idle_past_threshold(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tool result starts the idle clock (``idle_since``). When more
    than ``_STUCK_THRESHOLD_S`` wall-clock seconds pass without the
    next tool call, the card flips to the ``stuck`` class. The next
    tool call clears it.

    Uses a monkeypatched ``time.monotonic`` so the test doesn't have
    to actually wait the threshold out."""
    fake_clock = [1000.0]
    monkeypatch.setattr("jig.tui.screens.agents.time.monotonic", lambda: fake_clock[0])
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)
        screen.handle_agent_start(_agent_start("review", "T-3"))

        # Fresh-spawned: not stuck (idle_since is None).
        card = screen.query_one("#card-T-3-review", _AgentCard)
        assert "stuck" not in card.classes

        # Tool fires then completes — idle_since = 1000.0.
        screen.handle_agent_tool(
            {"role": "review", "ticket_id": "T-3", "tool": "Read", "detail": "x.py"}
        )
        assert "stuck" not in card.classes  # tool in flight, not idle
        screen.handle_agent_tool_result({"role": "review", "ticket_id": "T-3"})
        assert "stuck" not in card.classes  # just went idle, not past threshold

        # Advance the clock past the threshold; re-render via any handler.
        fake_clock[0] = 1000.0 + _STUCK_THRESHOLD_S + 1
        screen.handle_agent_thinking(
            {"role": "review", "ticket_id": "T-3", "elapsed": 0, "active": True}
        )
        assert "stuck" in card.classes
        assert "inactive" not in card.classes

        # Next tool call clears the stuck state immediately.
        screen.handle_agent_tool(
            {"role": "review", "ticket_id": "T-3", "tool": "Edit", "detail": "x.py"}
        )
        assert "stuck" not in card.classes


@pytest.mark.asyncio
async def test_tool_result_sets_idle_since_and_clears_current_tool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``handle_agent_tool_result`` is the boundary that starts the
    idle clock — pins both the AgentState mutation and the card
    re-render path."""
    fake_clock = [500.0]
    monkeypatch.setattr("jig.tui.screens.agents.time.monotonic", lambda: fake_clock[0])
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)
        screen.handle_agent_start(_agent_start("dev", "T-5"))
        screen.handle_agent_tool(
            {"role": "dev", "ticket_id": "T-5", "tool": "Read", "detail": "a.py"}
        )
        agent = screen._agents["T-5:dev"]
        assert agent.current_tool is not None
        assert agent.idle_since is None

        screen.handle_agent_tool_result({"role": "dev", "ticket_id": "T-5"})
        assert agent.current_tool is None
        assert agent.idle_since == 500.0


@pytest.mark.asyncio
async def test_tool_result_for_unknown_agent_is_a_noop(
    tmp_path: Path,
) -> None:
    """A tool_result event for an agent we never saw the start of
    should not crash and should not mutate state."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)
        # No handle_agent_start call → agent isn't in self._agents.
        screen.handle_agent_tool_result({"role": "ghost", "ticket_id": "T-ghost"})
        assert screen._agents == {}


@pytest.mark.asyncio
async def test_card_marked_inactive_when_agent_phase_ends(tmp_path: Path) -> None:
    """Active=False on the thinking event flips the card to inactive
    styling regardless of elapsed."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)
        screen.handle_agent_start(_agent_start("document", "T-4"))

        # Active and short elapsed — should NOT be stuck or inactive.
        screen.handle_agent_thinking(
            {
                "role": "document",
                "ticket_id": "T-4",
                "elapsed": 5,
                "active": True,
            }
        )
        card = screen.query_one("#card-T-4-document", _AgentCard)
        assert "inactive" not in card.classes
        assert "stuck" not in card.classes

        # Phase finishes — active=False.
        screen.handle_agent_thinking(
            {
                "role": "document",
                "ticket_id": "T-4",
                "elapsed": 12,
                "active": False,
            }
        )
        assert "inactive" in card.classes
        assert "stuck" not in card.classes


@pytest.mark.asyncio
async def test_card_id_sanitizes_colons_in_agent_key(tmp_path: Path) -> None:
    """Agent keys are ``ticket_id:role`` — colons would invalidate the
    Textual widget id. ``_slug`` translates ``:`` → ``-`` so the
    DOM query by id works."""
    from jig.tui.screens.agents import _slug

    assert _slug("T-1:dev") == "T-1-dev"
    assert _slug("ad8f5a62-T2:reviewer-generalist") == (
        "ad8f5a62-T2-reviewer-generalist"
    )
    # Pathological: dots also sanitized.
    assert _slug("foo.bar:baz") == "foo-bar-baz"
