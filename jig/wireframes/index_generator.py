"""Browser-viewable ``index.html`` generator for the wireframes dir.

Per ``docs/v2.0/visual-design/design.md`` §"Browser-based viewing": jig
generates a static ``index.html`` that embeds each per-screen wireframe
HTML via ``<iframe>``, with state-toggle controls (``loading`` / ``empty``
/ ``populated``) keyed off the URL hash so vanilla JS can show/hide
state-variant content in the embedded wireframes without a framework.

The generator is deterministic: same input dir → same output. Iterates
the ``*.html`` files in ``wireframes_dir`` (excluding the index itself
and ``wireframe.css``), pulls the meta block from each so the index can
group/label by persona + journey, and emits one ``<article>`` per
screen. Empty input dir produces a valid HTML page with a "no
wireframes yet" placeholder so the operator opening the index right
after VD discovery starts gets clear feedback rather than a blank page.

Vanilla JS only (no React, no Alpine here — the embedded wireframes
already have Alpine; the index page itself stays minimal).
"""
from __future__ import annotations

import html
from pathlib import Path

from jig.wireframes.format import extract_meta

__all__ = ["DEFAULT_STATE_OPTIONS", "generate_index"]


# State-toggle options the index surfaces above each wireframe iframe.
# Mirrors the design's example (``loading`` / ``empty`` / ``populated``
# / ``error``) — wireframes opt into state-variant ``x-show`` markup
# keyed off these names. The set is curated rather than per-wireframe-
# configurable to keep the URL-hash protocol simple and uniform across
# screens; wireframes that don't use a particular state just don't
# render anything different when that toggle fires.
DEFAULT_STATE_OPTIONS: tuple[str, ...] = (
    "default",
    "loading",
    "empty",
    "error",
    "populated",
)


def _index_html_for_screen(
    wireframe_html_filename: str,
    title: str,
    persona_targets: list[str],
    journey_refs: list[str],
    notes: str | None,
) -> str:
    """Render one ``<article>`` block for one screen."""
    persona = ", ".join(persona_targets) if persona_targets else "—"
    journey = ", ".join(journey_refs) if journey_refs else "—"
    notes_block = (
        f'<details><summary>Notes</summary><p>{html.escape(notes)}</p></details>'
        if notes
        else ""
    )
    state_buttons = "".join(
        (
            f'<button data-state="{html.escape(s)}" '
            f'aria-pressed="{"true" if s == "default" else "false"}">'
            f"{html.escape(s)}</button>"
        )
        for s in DEFAULT_STATE_OPTIONS
    )
    return f"""\
<article class="screen" data-screen-id="{html.escape(wireframe_html_filename)}">
  <h3>{html.escape(title)}</h3>
  <p class="meta">Persona(s): {html.escape(persona)} · Journey(s): {html.escape(journey)}</p>
  <div class="state-toggle" role="group" aria-label="State">
    {state_buttons}
  </div>
  <iframe src="{html.escape(wireframe_html_filename)}" title="{html.escape(title)} wireframe"></iframe>
  {notes_block}
</article>"""


# Inline JS that translates state-toggle button clicks into a URL-hash
# update and shoves the hash into the iframe's location so the embedded
# wireframe's ``x-data="{state: '...'}"`` can react. Vanilla; no
# framework. The toggle's aria-pressed state is updated for accessibility.
_STATE_TOGGLE_JS = """\
<script>
(function () {
  document.addEventListener("click", function (e) {
    if (e.target.tagName !== "BUTTON" || !e.target.dataset.state) return;
    var article = e.target.closest("article.screen");
    if (!article) return;
    var state = e.target.dataset.state;
    article.querySelectorAll(".state-toggle button").forEach(function (b) {
      b.setAttribute("aria-pressed", b === e.target ? "true" : "false");
    });
    var iframe = article.querySelector("iframe");
    if (!iframe) return;
    var src = iframe.getAttribute("src").split("#")[0];
    iframe.setAttribute("src", src + "#state=" + encodeURIComponent(state));
  });
})();
</script>"""


_INDEX_CSS = """\
<style>
  body { font-family: system-ui, sans-serif; max-width: 1400px; margin: 0 auto; padding: 24px; }
  h1 { margin-bottom: 8px; }
  .empty { padding: 48px; border: 1px dashed #999; text-align: center; color: #666; }
  article.screen { border: 1px solid #ccc; margin: 24px 0; padding: 16px; }
  article.screen iframe { width: 100%; height: 600px; border: none; background: #f5f5f5; }
  .state-toggle { display: flex; gap: 8px; margin: 8px 0; }
  .state-toggle button[aria-pressed="true"] { background: #444; color: #fff; }
  .meta { color: #666; font-size: 14px; }
</style>"""


def _is_wireframe_file(p: Path) -> bool:
    """Filter for the iter step — keep ``*.html`` other than the index."""
    if p.name == "index.html":
        return False
    if p.suffix.lower() != ".html":
        return False
    return p.is_file()


def generate_index(wireframes_dir: Path) -> str:
    """Render the browser-viewable ``index.html`` for ``wireframes_dir``.

    Pulls each ``*.html`` file's meta block (or falls back to the
    filename when meta is absent / malformed — the operator still wants
    to see the screen, even if the linker can't pin it to a journey
    yet). Sorted by filename so the index ordering is stable across
    runs (deterministic output for the test).

    Empty / missing dir produces a valid page with a placeholder so
    operators opening the index at VD discovery start aren't met with
    a blank page or broken iframes.
    """
    screens = []
    if wireframes_dir.is_dir():
        for path in sorted(wireframes_dir.iterdir()):
            if not _is_wireframe_file(path):
                continue
            html_text = path.read_text()
            try:
                meta = extract_meta(html_text)
            except ValueError:
                meta = None
            title = meta.title if meta else path.stem
            personas = meta.persona_targets if meta else []
            journeys = meta.journey_refs if meta else []
            notes = meta.notes if meta else None
            screens.append(
                _index_html_for_screen(
                    wireframe_html_filename=path.name,
                    title=title,
                    persona_targets=personas,
                    journey_refs=journeys,
                    notes=notes,
                )
            )

    body_inner = (
        "\n".join(screens)
        if screens
        else (
            '<section class="empty"><p>No wireframes authored yet. '
            'VD discovery will populate this dir as screens are walked.</p></section>'
        )
    )

    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>jig wireframes</title>
{_INDEX_CSS}
</head>
<body>
  <h1>jig wireframes</h1>
  <p>Open the per-screen wireframes below. State-toggle buttons set the URL hash on the iframe so wireframes with Alpine state markup react.</p>
  {body_inner}
{_STATE_TOGGLE_JS}
</body>
</html>
"""
