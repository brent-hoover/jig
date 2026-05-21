"""Tests for the MultiPaneStream widget's state + navigation API.

The widget is renderable Textual but its API is plain Python — these
tests exercise the state surface (add/append/status/focus/expand)
without mounting the app. Render-path coverage lives in integration
tests that actually drive a Textual app instance.
"""

from __future__ import annotations

import pytest

from jig.tui.widgets.multi_pane_stream import MultiPaneStream


class TestStreamRegistration:
    def test_add_stream_assigns_label_and_default_status(self) -> None:
        w = MultiPaneStream()
        w.add_stream("sec", label="reviewer-security")
        assert w.stream_ids() == ["sec"]
        assert w._streams["sec"].label == "reviewer-security"
        assert w._streams["sec"].status == "running"

    def test_add_stream_is_idempotent(self) -> None:
        w = MultiPaneStream()
        w.add_stream("sec", label="reviewer-security")
        w.add_stream("sec", label="reviewer-security (updated)", status="done")
        assert w.stream_ids() == ["sec"]
        assert w._streams["sec"].label == "reviewer-security (updated)"
        assert w._streams["sec"].status == "done"

    def test_add_stream_preserves_insertion_order(self) -> None:
        w = MultiPaneStream()
        for sid in ("a", "b", "c", "d"):
            w.add_stream(sid, label=sid)
        assert w.stream_ids() == ["a", "b", "c", "d"]

    def test_first_added_stream_becomes_focused(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.add_stream("b", label="b")
        assert w.focused_stream_id == "a"


class TestAppendLine:
    def test_append_line_accumulates_into_buffer(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.append_line("a", "first")
        w.append_line("a", "second")
        assert w.stream_lines("a") == ["first", "second"]

    def test_append_line_auto_registers_unknown_stream(self) -> None:
        w = MultiPaneStream()
        w.append_line("ghost", "hello")
        assert "ghost" in w.stream_ids()
        assert w.stream_lines("ghost") == ["hello"]

    def test_append_line_splits_embedded_newlines(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.append_line("a", "line1\nline2\nline3")
        assert w.stream_lines("a") == ["line1", "line2", "line3"]

    def test_append_line_coerces_non_string(self) -> None:
        w = MultiPaneStream()
        w.append_line("a", 42)
        assert w.stream_lines("a") == ["42"]


class TestSetStatus:
    def test_set_status_updates_existing_stream(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.set_status("a", "done", finding_count=2)
        assert w._streams["a"].status == "done"
        assert w._streams["a"].finding_count == 2

    def test_set_status_auto_registers(self) -> None:
        w = MultiPaneStream()
        w.set_status("a", "error")
        assert w._streams["a"].status == "error"

    def test_finding_count_optional(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.set_status("a", "done")
        assert w._streams["a"].finding_count is None


class TestSetLabel:
    def test_set_label_renames_existing_stream(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="old")
        w.set_label("a", "new")
        assert w._streams["a"].label == "new"

    def test_set_label_on_unknown_stream_is_noop(self) -> None:
        w = MultiPaneStream()
        w.set_label("ghost", "name")
        assert "ghost" not in w.stream_ids()


class TestRemoveStream:
    def test_remove_stream_drops_pane(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.add_stream("b", label="b")
        w.remove_stream("a")
        assert w.stream_ids() == ["b"]

    def test_remove_focused_stream_refocuses(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.add_stream("b", label="b")
        # ``a`` is the auto-focused first stream.
        w.remove_stream("a")
        assert w.focused_stream_id == "b"

    def test_remove_last_stream_clears_focus(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.remove_stream("a")
        assert w.focused_stream_id is None

    def test_remove_unknown_is_noop(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.remove_stream("ghost")
        assert w.stream_ids() == ["a"]


class TestClear:
    def test_clear_drops_all_streams_and_state(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.add_stream("b", label="b")
        w.action_expand()
        w.clear()
        assert w.stream_ids() == []
        assert w.focused_stream_id is None
        assert not w.is_expanded


class TestFocusNavigation:
    def test_focus_next_wraps_around(self) -> None:
        w = MultiPaneStream()
        for sid in ("a", "b", "c"):
            w.add_stream(sid, label=sid)
        assert w.focused_stream_id == "a"
        w.action_focus_next()
        assert w.focused_stream_id == "b"
        w.action_focus_next()
        assert w.focused_stream_id == "c"
        w.action_focus_next()
        assert w.focused_stream_id == "a"

    def test_focus_prev_wraps_around(self) -> None:
        w = MultiPaneStream()
        for sid in ("a", "b", "c"):
            w.add_stream(sid, label=sid)
        w.action_focus_prev()
        assert w.focused_stream_id == "c"

    def test_focus_n_jumps_to_pane(self) -> None:
        w = MultiPaneStream()
        for sid in ("a", "b", "c"):
            w.add_stream(sid, label=sid)
        w.action_focus_n(3)
        assert w.focused_stream_id == "c"
        w.action_focus_n(1)
        assert w.focused_stream_id == "a"

    def test_focus_n_out_of_range_is_noop(self) -> None:
        w = MultiPaneStream()
        for sid in ("a", "b"):
            w.add_stream(sid, label=sid)
        w.action_focus_n(0)
        w.action_focus_n(9)
        assert w.focused_stream_id == "a"

    def test_focus_navigation_on_empty_widget_is_noop(self) -> None:
        w = MultiPaneStream()
        w.action_focus_next()
        w.action_focus_prev()
        w.action_focus_n(1)
        assert w.focused_stream_id is None


class TestExpandCollapse:
    def test_expand_sets_expanded_flag(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.action_expand()
        assert w.is_expanded

    def test_collapse_clears_expanded_flag(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.action_expand()
        w.action_collapse()
        assert not w.is_expanded

    def test_expand_with_no_focus_is_noop(self) -> None:
        w = MultiPaneStream()
        w.action_expand()
        assert not w.is_expanded


class TestLineBuffer:
    def test_full_buffer_returned_by_stream_lines(self) -> None:
        """Even though compact mode only renders the last K lines, the
        full buffer must be retained so expanded scroll can show
        history."""
        w = MultiPaneStream(lines_per_pane=3)
        w.add_stream("a", label="a")
        for i in range(10):
            w.append_line("a", f"line-{i}")
        assert len(w.stream_lines("a")) == 10
        assert w.stream_lines("a")[0] == "line-0"
        assert w.stream_lines("a")[-1] == "line-9"

    def test_lines_per_pane_clamps_to_at_least_one(self) -> None:
        w = MultiPaneStream(lines_per_pane=0)
        assert w._lines_per_pane == 1
        w2 = MultiPaneStream(lines_per_pane=-3)
        assert w2._lines_per_pane == 1


class TestRenderingDoesNotCrash:
    """Smoke tests for the render paths — exercised without an app so we
    only verify they don't raise. Full visual coverage is integration-
    level."""

    def test_render_compact_with_no_streams(self) -> None:
        w = MultiPaneStream()
        w._render_compact()  # Should produce the "(no streams)" placeholder.

    def test_render_compact_with_streams(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.append_line("a", "hello")
        w.set_status("a", "done", finding_count=1)
        w._render_compact()

    def test_render_expanded_returns_focused_pane_only(self) -> None:
        w = MultiPaneStream()
        w.add_stream("a", label="a")
        w.add_stream("b", label="b")
        w.append_line("a", "from a")
        w.append_line("b", "from b")
        w.action_expand()
        # Expanded path must render without crashing.
        w._render_expanded()


# ── Parametrize: every documented status value renders cleanly ──────


@pytest.mark.parametrize(
    "status",
    ["running", "done", "blocked", "waiting", "error"],
)
def test_every_status_renders_without_crash(status: str) -> None:
    w = MultiPaneStream()
    w.add_stream("a", label="a", status=status)  # type: ignore[arg-type]
    w._render_pane_compact("a")
