"""``Scrollback`` — a ``RichLog`` that actually paints text selection.

Upstream ``textual.widgets.RichLog`` in 8.2.4 (and earlier) does not
visualise mouse-drag text selection: ``Screen`` updates the
selection range internally and ``App.copy_to_clipboard`` can read it
via ``screen.get_selected_text()``, but ``RichLog.render_line``
returns its strips with no selection-style overlay. Operators see
no highlight when they drag, conclude selection doesn't work, and
the ``ctrl+c → copy_selection`` binding looks broken even when it
isn't.

``Log`` (the plain-text sibling widget) paints selection correctly
in its ``_render_line``. This class ports that pattern onto
``RichLog`` so the scrollback's rich content (Markdown, Rules,
Rich-styled banners) keeps rendering AND the selection highlight
shows up.

Apply ``screen--selection`` styling to the cell range returned by
``Selection.get_span(y_in_widget)``. The screen's selection
coordinate is a (start, end) cell pair per line; we slice the strip
at those boundaries via ``Strip.divide`` and re-style the middle
piece.
"""

from __future__ import annotations

from textual.strip import Strip
from textual.widgets import RichLog


class Scrollback(RichLog):
    """``RichLog`` that overlays the App's text selection on render.

    Behaviour is identical to ``RichLog`` except ``render_line``
    composites the selection highlight before returning. The
    ``screen--selection`` component style (set via app CSS) drives
    the highlight colour.
    """

    def render_line(self, y: int) -> Strip:
        strip = super().render_line(y)
        selection = self.text_selection
        if selection is None:
            return strip
        # Selection spans use absolute line indices into the widget's
        # content stream. ``_start_line`` tracks how many lines have
        # been trimmed off the front of the buffer (via max_lines);
        # the absolute index for visible row ``y`` is
        # ``y + scroll_y + _start_line``.
        scroll_y = self.scroll_offset.y
        absolute_y = y + scroll_y + self._start_line
        try:
            span = selection.get_span(absolute_y)
        except Exception:
            return strip
        if span is None:
            return strip
        start, end = span
        cell_length = strip.cell_length
        if end == -1 or end > cell_length:
            end = cell_length
        # Empty / out-of-range selection on this row — nothing to paint.
        if start >= end or start >= cell_length:
            return strip
        if start < 0:
            start = 0
        selection_style = self.screen.get_component_rich_style(
            "screen--selection",
        )
        # ``Strip.divide`` cuts the strip at given cell positions and
        # returns the pieces. Cutting at [start, end, cell_length]
        # yields up to three pieces — before/inside/after the
        # selection. ``apply_style`` returns a new strip; concatenate
        # via ``Strip.join``.
        cuts = sorted({c for c in (start, end, cell_length) if 0 < c <= cell_length})
        pieces = list(strip.divide(cuts))
        # Map back: pieces are aligned to the cut boundaries in order.
        # The piece that ends at ``end`` and starts at ``start`` is
        # the one to re-style. We track the running cell position to
        # identify it.
        out: list[Strip] = []
        pos = 0
        for piece in pieces:
            piece_end = pos + piece.cell_length
            # A piece is "in the selection" if it lies entirely within
            # [start, end). Cuts are at exactly start and end, so
            # whole-piece membership is a strict comparison.
            if pos >= start and piece_end <= end:
                out.append(piece.apply_style(selection_style))
            else:
                out.append(piece)
            pos = piece_end
        return Strip.join(out)
