"""Tests for ctrl+c copy-selection in the jig TUI.

PR #52 removed ``Binding('ctrl+c', 'quit')`` on the assumption that
Textual's default behaviour for ctrl+c was "copy selection" — but
that's not the case. Textual's App-level system binding for ctrl+c
is ``action_help_quit`` (just shows a "press ctrl+q to quit"
notification). ``Input`` and ``TextArea`` widgets ship their own
``ctrl+c→copy`` for in-widget selection, but ``RichLog`` (and the
App-level cross-widget selection populated by mouse drag across
multiple widgets) had no copy binding at all. So after PR #52,
selecting scrollback text and pressing ctrl+c showed the quit
notification and put nothing in the clipboard.

These tests cover the fix: an explicit App-level
``action_copy_selection`` that reads ``screen.get_selected_text()``
and routes it through the App's ``copy_to_clipboard`` (which also
strips ZWSPs from ``ligature_safe``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.tui import ZWSP
from jig.tui.app import JigApp


@pytest.mark.asyncio
async def test_copy_selection_with_active_selection(tmp_path: Path) -> None:
    """When ``get_selected_text`` returns text, the action copies it
    to the clipboard and clears the selection."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        # Fake an active selection — get_selected_text is what
        # mouse-drag populates internally.
        app.screen.get_selected_text = lambda: "hello world"
        cleared = {"flag": False}

        original_clear = app.screen.clear_selection

        def _record_clear() -> None:
            cleared["flag"] = True
            original_clear()

        app.screen.clear_selection = _record_clear
        app.action_copy_selection()
        await app._animator.wait_until_complete()
        assert app.clipboard == "hello world"
        assert cleared["flag"] is True


@pytest.mark.asyncio
async def test_copy_selection_strips_zwsp(tmp_path: Path) -> None:
    """ZWSPs inserted by ``ligature_safe`` to defeat font ligatures
    must NOT survive a copy — pasting them into a shell would turn
    ``--limit`` into ``-<ZWSP>-limit`` and break the command. The
    action must route through ``copy_to_clipboard`` (the overridden
    method that strips ZWSP), not directly through Textual's
    ``_clipboard``.
    """
    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        app.screen.get_selected_text = lambda: f"--limit{ZWSP}-N"
        app.screen.clear_selection = lambda: None
        app.action_copy_selection()
        await app._animator.wait_until_complete()
        assert ZWSP not in app.clipboard
        assert app.clipboard == "--limit-N"


@pytest.mark.asyncio
async def test_copy_selection_with_no_selection_shows_quit_hint(
    tmp_path: Path,
) -> None:
    """No selection → action falls back to the "press ctrl+q to quit"
    notification, matching Textual's stock ``action_help_quit``
    behaviour. Verifies a reflexive ctrl+c press doesn't silently
    eat the keystroke."""
    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        app.screen.get_selected_text = lambda: ""
        notified: list[tuple[str, str | None]] = []

        def _capture_notify(msg: str, *, title: str | None = None, **kwargs):
            notified.append((msg, title))

        app.notify = _capture_notify  # type: ignore[method-assign]
        app.action_copy_selection()
        await app._animator.wait_until_complete()
        # Clipboard untouched.
        assert app.clipboard == ""
        # Either a notification was shown OR no quit binding was active
        # (depends on bindings; in this app ctrl+q is bound, so one
        # SHOULD fire).
        assert any("quit" in msg.lower() for msg, _ in notified), (
            f"expected a quit-hint notification; got {notified}"
        )


@pytest.mark.asyncio
async def test_ctrl_c_binding_registered(tmp_path: Path) -> None:
    """The ctrl+c binding must exist at the App level and target
    ``copy_selection`` — otherwise Textual's system-level
    ``ctrl+c→help_quit`` takes over and the fix doesn't fire."""
    bindings = JigApp.BINDINGS
    matches = [
        b
        for b in bindings
        if (getattr(b, "key", None) or "").lower() == "ctrl+c"
    ]
    assert matches, "no ctrl+c binding on JigApp"
    actions = [getattr(b, "action", None) for b in matches]
    assert "copy_selection" in actions, (
        f"ctrl+c is bound but not to copy_selection: {actions}"
    )
