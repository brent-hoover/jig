"""Inject ``acceptance_criteria`` into Epic blocks in scenario YAML.

The Epic schema now requires ``acceptance_criteria: list[str]`` with
at least one entry. The ~34 sim-scenario YAML fixtures under
``tests/scenarios/`` each contain one or more inline Epic definitions
that predate the new field — this script appends a placeholder bullet
to every Epic block that lacks one.

Text-level migration (preserves formatting, comments, anchors) rather
than load → mutate → dump. The Epic block in these fixtures is a
predictable shape: an ``- id:`` mapping with a known set of sibling
keys including ``intent:``. We locate each ``intent:`` block,
determine its end (the next line at strictly less indentation, or the
next ``- id:`` at the same epic-list indent), and inject the AC
mapping in the right place.

Idempotent: re-runs are no-ops because the script skips any Epic
block that already has an ``acceptance_criteria:`` sibling key.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Bullet stays generic — every scenario's intent prose already
# describes what the epic is supposed to do, so a placeholder bullet
# is enough for the validator. Authors revising a scenario should
# rewrite the bullet to match the test intent.
_PLACEHOLDER_BULLET = "Scenario placeholder; refine per test intent."


def _measure_indent(line: str) -> int:
    """Number of leading-space characters on ``line`` (tabs not expected
    in scenario YAML)."""
    stripped = line.lstrip(" ")
    return len(line) - len(stripped)


def _find_epic_starts(lines: list[str]) -> list[int]:
    """Return indices of ``- id: ...`` lines that introduce an Epic.

    An Epic is identified by being directly under an ``epics:`` mapping
    key. We walk linearly and toggle ``in_epics`` whenever we encounter
    ``epics:`` at any indent; the toggle clears when we hit a sibling
    key at the same indent.
    """
    starts: list[int] = []
    in_epics = False
    epics_indent: int | None = None
    for idx, line in enumerate(lines):
        if not line.strip():
            continue
        indent = _measure_indent(line)
        stripped = line.strip()

        if not in_epics:
            if stripped == "epics:" or stripped.startswith("epics:"):
                in_epics = True
                epics_indent = indent
            continue

        # in_epics
        assert epics_indent is not None
        if indent <= epics_indent and not stripped.startswith("- "):
            # left the epics mapping
            in_epics = False
            epics_indent = None
            continue
        if indent == epics_indent + 2 and stripped.startswith("- id:"):
            starts.append(idx)
            continue
        # other content within the epics list — ignore for boundary
        # detection (could be sub-keys of an Epic).
    return starts


def _epic_block_end(lines: list[str], start: int) -> int:
    """Return the exclusive end index of the Epic block starting at
    ``lines[start]`` (an ``- id: ...`` line).

    The block ends at the next sibling ``- id:`` at the same indent,
    or at the first line whose indent is less than or equal to the
    list-item dash indent (closing the list)."""
    dash_indent = _measure_indent(lines[start])
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        indent = _measure_indent(line)
        stripped = line.strip()
        if indent <= dash_indent:
            # Sibling list item ``- ...`` or end of list.
            return i
    return len(lines)


def _epic_has_ac(lines: list[str], start: int, end: int) -> bool:
    """Return True if the Epic block (lines[start:end]) already contains
    an ``acceptance_criteria:`` key at the field-level indent.

    Field-level indent = list-item-dash indent + 2 ("- id: ..." is
    the first field, subsequent fields are indented 2 more spaces under
    the dash)."""
    dash_indent = _measure_indent(lines[start])
    field_indent = dash_indent + 2
    for i in range(start, end):
        line = lines[i]
        stripped = line.lstrip(" ")
        indent = _measure_indent(line)
        if indent == field_indent and stripped.startswith("acceptance_criteria:"):
            return True
    return False


def _epic_intent_end(lines: list[str], start: int, end: int) -> int | None:
    """Return the exclusive end index of the ``intent:`` sub-block, or
    None if the epic has no ``intent:`` key.

    The intent block ends at the first line whose indent is <= the
    field-level indent (a sibling field of the epic, like
    ``risks_addressed:``)."""
    dash_indent = _measure_indent(lines[start])
    field_indent = dash_indent + 2
    intent_idx: int | None = None
    for i in range(start, end):
        line = lines[i]
        if not line.strip():
            continue
        indent = _measure_indent(line)
        stripped = line.lstrip(" ")
        if intent_idx is None:
            if indent == field_indent and stripped.startswith("intent:"):
                intent_idx = i
                continue
        else:
            if indent <= field_indent:
                # left the intent block
                return i
    if intent_idx is not None:
        return end
    return None


def _build_injection(indent: int) -> str:
    """Render the AC mapping at the given field indent."""
    pad = " " * indent
    return (
        f"{pad}acceptance_criteria:\n"
        f'{pad}  - "{_PLACEHOLDER_BULLET}"\n'
    )


def migrate_file(path: Path) -> bool:
    raw = path.read_text()
    lines = raw.splitlines(keepends=True)
    epic_starts = _find_epic_starts(lines)
    if not epic_starts:
        return False

    # Process bottom-up so insertions don't shift later indices.
    edits: list[tuple[int, str]] = []
    for start in epic_starts:
        end = _epic_block_end(lines, start)
        if _epic_has_ac(lines, start, end):
            continue
        dash_indent = _measure_indent(lines[start])
        field_indent = dash_indent + 2
        insert_at = _epic_intent_end(lines, start, end)
        if insert_at is None:
            # No ``intent:`` block found — insert just before the end
            # of the epic block (still inside this epic).
            insert_at = end
        edits.append((insert_at, _build_injection(field_indent)))

    if not edits:
        return False
    edits.sort(key=lambda e: e[0], reverse=True)
    for insert_at, text in edits:
        lines.insert(insert_at, text)
    path.write_text("".join(lines))
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: migrate_scenario_epic_ac.py tests/scenarios/")
        return 2
    root = Path(argv[0])
    changed = 0
    for path in sorted(root.rglob("*.yaml")):
        if migrate_file(path):
            changed += 1
            print(f"migrated: {path}")
    print(f"\n{changed} file(s) migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
