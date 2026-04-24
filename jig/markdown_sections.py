"""Read/write level-2 (``## Name``) sections of a markdown file.

Used by brief_* MCP tools. Uses atomic_write_text for persistence so
partial writes never corrupt project.md.
"""
from __future__ import annotations

import re
from pathlib import Path

from jig.atomic import atomic_write_text

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def list_sections(path: Path) -> list[str]:
    """Return the names of all level-2 sections, in document order."""
    text = path.read_text(encoding="utf-8")
    return [m.group(1) for m in _HEADING_RE.finditer(text)]


def get_section(path: Path, name: str) -> str:
    """Return the body of the named section (text between its heading
    and the next level-2 heading or EOF), without the heading line.
    Raises KeyError if the section is not present.
    """
    text = path.read_text(encoding="utf-8")
    bounds = _section_bounds(text, name)
    if bounds is None:
        raise KeyError(name)
    _, body_start, body_end = bounds
    return text[body_start:body_end]


def set_section(path: Path, name: str, body: str) -> None:
    """Atomically replace the named section's body, or append a new
    section at EOF if not present. ``body`` should not include the
    heading line. A trailing newline is ensured.
    """
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if not body.endswith("\n"):
        body = body + "\n"

    bounds = _section_bounds(text, name)
    if bounds is None:
        if text and not text.endswith("\n"):
            text += "\n"
        new_text = f"{text}\n## {name}\n\n{body}"
    else:
        heading_start, body_start, body_end = bounds
        new_text = text[:body_start] + body + text[body_end:]

    atomic_write_text(path, new_text)


def _section_bounds(text: str, name: str) -> tuple[int, int, int] | None:
    """Return (heading_start, body_start, body_end) if ``name`` is a
    level-2 section in ``text``. The body starts after the newline
    following the heading and ends at the next level-2 heading or EOF.
    """
    matches = list(_HEADING_RE.finditer(text))
    for i, m in enumerate(matches):
        if m.group(1) == name:
            heading_start = m.start()
            # Body starts after the newline that ends the heading line.
            line_end = text.find("\n", m.end())
            body_start = line_end + 1 if line_end != -1 else len(text)
            body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            return heading_start, body_start, body_end
    return None
