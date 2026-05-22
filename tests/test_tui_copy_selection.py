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
async def test_real_scrollback_selection_extracts_text(tmp_path: Path) -> None:
    """Drive ``screen.get_selected_text`` through the real selection
    plumbing — set a ``Selection`` on the scrollback widget the way
    a live mouse drag would, then assert the action copies the
    actual scrollback content (not a monkey-patched return).

    Pre-fix this returned ``""`` because ``Widget.get_selection``
    (inherited by ``RichLog``) calls ``self._render()`` and returns
    ``None`` for line-API widgets — so even when Textual's selection
    machinery tracked the drag correctly, the extracted text was
    empty.
    """
    from textual.geometry import Offset
    from textual.selection import Selection
    from textual.widgets import RichLog

    from jig.tui.screens.now import NowScreen
    from jig.tui.widgets.scrollback import Scrollback

    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(95, 30)):
        now = app.query_one(NowScreen)
        scrollback = now.query_one("#scrollback", RichLog)
        # Verify wiring: NowScreen yields the Scrollback subclass, not
        # plain RichLog. Without this, the rest of the test would
        # exercise the upstream-broken path.
        assert isinstance(scrollback, Scrollback)

        # Clear the welcome-banner line so our test content is at a
        # known y coordinate.
        scrollback.clear()
        scrollback.write("hello world this is a test")
        await app._animator.wait_until_complete()

        # Locate the line index our content landed on (after wrap
        # accounting). Should be line 0 post-clear.
        target_y: int | None = None
        for idx, strip in enumerate(scrollback.lines):
            text = "".join(seg.text for seg in strip)
            if "world" in text:
                target_y = idx
                world_x = text.index("world")
                break
        assert target_y is not None, "test content not found in scrollback"

        # Set a selection covering exactly "world" on that line.
        # This is what ``_select_start`` / ``_select_end`` populate
        # internally on a real mouse drag.
        app.screen.selections = {
            scrollback: Selection(
                start=Offset(world_x, target_y),
                end=Offset(world_x + 5, target_y),
            ),
        }
        extracted = app.screen.get_selected_text()
        assert extracted == "world", f"expected 'world', got {extracted!r}"

        # End-to-end: ctrl+c action sees the selection and routes it
        # to the clipboard. ``copy_to_clipboard`` strips ZWSPs (none
        # here, but the path is identical).
        app.action_copy_selection()
        await app._animator.wait_until_complete()
        assert app.clipboard == "world"


@pytest.mark.asyncio
async def test_scrollback_strips_carry_selection_offset_metadata(
    tmp_path: Path,
) -> None:
    """The strips rendered by Scrollback must carry the per-cell
    offset metadata that ``Screen.get_widget_and_offset_at`` reads
    to compute ``select_offset`` on mouse-down.

    Without this metadata (Textual's ``Strip.apply_offsets`` step,
    which upstream ``RichLog`` doesn't apply), a real mouse drag
    produces ``select_offset=None`` → ``_select_start`` never gets
    populated → no Selection is ever created → both the painting
    and extraction code paths sit unreachable. ``Log`` applies
    these offsets in its ``_render_line``; this regression test
    asserts ``Scrollback`` does too.
    """
    from textual.widgets import RichLog

    from jig.tui.screens.now import NowScreen
    from jig.tui.widgets.scrollback import Scrollback

    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        now = app.query_one(NowScreen)
        scrollback = now.query_one("#scrollback", RichLog)
        assert isinstance(scrollback, Scrollback)
        scrollback.clear()
        scrollback.write("aaaa bbbb cccc")
        await pilot.pause()

        # Each segment in the rendered strip must carry an
        # ``offset=(x, y)`` style-meta entry — the metadata
        # apply_offsets writes. Without it,
        # ``get_widget_and_offset_at`` returns no offset and the
        # selection chain breaks.
        rendered = scrollback.render_line(0)
        offsets_seen: list[tuple[int, int] | None] = []
        for seg in rendered:
            if seg.style is None:
                offsets_seen.append(None)
                continue
            meta = seg.style.meta or {}
            offsets_seen.append(meta.get("offset"))
        # At least one segment in the visible content must have an
        # offset tuple — if none do, the metadata is absent.
        assert any(o is not None for o in offsets_seen), (
            "no segment in the rendered strip carries selection "
            "offset metadata — Scrollback.render_line is missing "
            "Strip.apply_offsets. Offsets seen: "
            f"{offsets_seen}"
        )


@pytest.mark.asyncio
async def test_empty_rows_below_content_carry_no_offset_metadata(
    tmp_path: Path,
) -> None:
    """Viewport rows past the end of ``self.lines`` must NOT carry
    selection offset metadata. If they did, a drag starting from
    blank space below the transcript would create a Selection over
    content that doesn't exist. ``Log`` returns ``Strip.blank``
    without offsets for these rows; ``Scrollback`` should match.
    """
    from textual.widgets import RichLog

    from jig.tui.screens.now import NowScreen
    from jig.tui.widgets.scrollback import Scrollback

    app = JigApp(project_path=tmp_path)
    async with app.run_test(size=(120, 40)) as pilot:
        now = app.query_one(NowScreen)
        scrollback = now.query_one("#scrollback", RichLog)
        assert isinstance(scrollback, Scrollback)
        scrollback.clear()
        scrollback.write("only one line of content")
        await pilot.pause()

        # Row 0 is the populated line — should carry metadata.
        populated = scrollback.render_line(0)
        has_offset = any(
            (seg.style.meta or {}).get("offset") is not None
            for seg in populated
            if seg.style is not None
        )
        assert has_offset, "populated row should carry offset metadata"

        # Row 5 is past the buffer — should be blank without metadata.
        empty = scrollback.render_line(5)
        for seg in empty:
            if seg.style is None:
                continue
            assert (seg.style.meta or {}).get("offset") is None, (
                "empty viewport row below content must not carry "
                f"offset metadata; got {seg.style.meta}"
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
