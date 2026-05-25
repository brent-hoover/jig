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
async def test_card_marked_stuck_when_elapsed_exceeds_threshold_with_no_current_tool(
    tmp_path: Path,
) -> None:
    """Active agent past the stuck threshold with no current tool gets
    the ``stuck`` class on its card; receiving a tool call clears it."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test():
        screen = app.query_one(AgentsScreen)
        screen.handle_agent_start(_agent_start("review", "T-3"))

        # Advance elapsed past the stuck threshold; no current_tool set.
        screen.handle_agent_thinking(
            {
                "role": "review",
                "ticket_id": "T-3",
                "elapsed": _STUCK_THRESHOLD_S + 5,
                "active": True,
            }
        )
        card = screen.query_one("#card-T-3-review", _AgentCard)
        assert "stuck" in card.classes
        assert "inactive" not in card.classes

        # Receiving a tool call clears the stuck state.
        screen.handle_agent_tool(
            {
                "role": "review",
                "ticket_id": "T-3",
                "tool": "Read",
                "detail": "src/foo.py",
            }
        )
        assert "stuck" not in card.classes


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
