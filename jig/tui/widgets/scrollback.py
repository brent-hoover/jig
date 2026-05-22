"""``Scrollback`` — a ``RichLog`` that paints + extracts text selection.

Upstream ``textual.widgets.RichLog`` in 8.2.4 (and earlier) is missing
two halves of the text-selection contract:

1. **Painting.** ``Screen`` updates a per-widget ``Selection`` object on
   mouse drag, but ``RichLog.render_line`` returns its strips with no
   selection-style overlay. The sibling ``Log`` widget paints
   selection correctly in its ``_render_line``.

2. **Extraction.** ``Screen.get_selected_text()`` calls each selected
   widget's ``get_selection(selection)``. The base ``Widget``
   implementation expects ``self._render()`` to return a ``Text`` /
   ``Content`` value — true for compose-tree widgets, not true for
   line-API widgets like ``RichLog`` (whose content is a list of
   ``Strip`` objects in ``self.lines``). Inherited ``get_selection``
   therefore returns ``None``, ``screen.get_selected_text()`` is the
   empty string, and the operator's ``ctrl+c`` after a visible drag
   copies nothing.

This class ports both halves of Log's pattern onto RichLog so the
scrollback's rich content (Markdown, Rules, Rich-styled banners)
keeps rendering AND:

* the selection highlight paints during a drag (uses
  ``screen--selection`` from app CSS);
* ``get_selection`` extracts the underlying text from ``self.lines``
  via ``Selection.extract`` so the App's ``ctrl+c → copy_selection``
  action has something to put on the clipboard.
"""

from __future__ import annotations

from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog


class Scrollback(RichLog):
    """``RichLog`` with working text selection (paint + extract).

    Drop-in replacement for ``RichLog`` in the scrollback role.
    Behaviour is identical aside from the three selection overrides
    (``render_line`` paints; ``get_selection`` extracts;
    ``selection_updated`` invalidates the line cache on change).
    """

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        selection = self.text_selection
        if selection is None:
            return strip
        # Selection y is in the same coordinate space as the index
        # into ``self.lines``. ``y`` here is the viewport row; add
        # ``scroll_y`` to get the array index.
        scroll_y = self.scroll_offset.y
        line_index = y + scroll_y
        try:
            span = selection.get_span(line_index)
        except Exception:
            return strip
        if span is None:
            return strip
        start, end = span
        cell_length = strip.cell_length
        if end == -1 or end > cell_length:
            end = cell_length
        if start >= end or start >= cell_length:
            return strip
        if start < 0:
            start = 0
        selection_style = self.screen.get_component_rich_style(
            "screen--selection",
        )
        cuts = sorted({c for c in (start, end, cell_length) if 0 < c <= cell_length})
        pieces = list(strip.divide(cuts))
        out: list[Strip] = []
        pos = 0
        for piece in pieces:
            piece_end = pos + piece.cell_length
            if pos >= start and piece_end <= end:
                out.append(piece.apply_style(selection_style))
            else:
                out.append(piece)
            pos = piece_end
        return Strip.join(out)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return the plain text under ``selection``.

        Mirrors ``Log.get_selection``: concatenate the widget's lines
        with newline separators, hand them to ``Selection.extract``,
        and return the result along with the line-ending hint
        Textual uses when joining across multiple widgets.

        Without this override, the inherited ``Widget.get_selection``
        calls ``self._render()`` (designed for compose-tree widgets)
        and returns ``None`` for line-API widgets like ``RichLog`` —
        meaning ``screen.get_selected_text()`` returns the empty
        string and ``ctrl+c → action_copy_selection`` copies nothing.
        """
        if not self.lines:
            return None
        plain_lines = [
            "".join(segment.text for segment in strip) for strip in self.lines
        ]
        text = "\n".join(plain_lines)
        return selection.extract(text), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        """Refresh on selection change, invalidating the line cache.

        ``RichLog._line_cache`` keys on
        ``(absolute_y, scroll_x, width, widest_line_width)`` — none
        of which encode the selection state. Without clearing it,
        ``_render_line`` returns the cached pre-selection strip and
        the override's paint sits on top of stale dimensions. Clear
        the cache so ``_render_line`` recomputes from
        ``self.lines`` on the next render pass.
        """
        self._line_cache.clear()
        super().selection_updated(selection)
