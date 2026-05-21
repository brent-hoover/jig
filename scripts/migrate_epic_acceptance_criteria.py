"""Inject a placeholder ``acceptance_criteria`` into Epic() test fixtures.

One-shot migration for the ``feat/epic-acceptance-criteria`` PR. The
``Epic`` schema now requires ``acceptance_criteria: list[str]`` with
``min_length=1``. Existing test fixtures construct Epics without that
field — this script walks each test file, finds ``Epic(...)`` calls,
and adds ``acceptance_criteria=["placeholder bullet — replace if test
cares"]`` to any call that doesn't already have one.

Idempotent: re-runs are no-ops because the script skips calls that
already have ``acceptance_criteria=`` as a kwarg.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_PLACEHOLDER_BULLET = (
    '"Test fixture placeholder; replace if the test cares about AC content."'
)


def _is_epic_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "Epic":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "Epic":
        return True
    return False


def _scan(tree: ast.Module) -> list[tuple[int, int, int]]:
    """Return (lineno, end_lineno, end_col_offset) per ``Epic(...)``
    call that needs migration, sorted bottom-up so we can rewrite the
    source without invalidating earlier offsets."""
    calls: list[tuple[int, int, int]] = []
    for node in ast.walk(tree):
        if not _is_epic_call(node):
            continue
        assert isinstance(node, ast.Call)
        has_ac = any(kw.arg == "acceptance_criteria" for kw in node.keywords)
        if has_ac:
            continue
        assert node.end_lineno is not None
        assert node.end_col_offset is not None
        calls.append((node.lineno, node.end_lineno, node.end_col_offset))
    calls.sort(key=lambda c: (c[1], c[2]), reverse=True)
    return calls


def _inject(source: str, calls: list[tuple[int, int, int]]) -> str:
    """Inject ``acceptance_criteria=[PLACEHOLDER]`` before the closing
    ``)`` of each ``Epic(...)`` call. Handles multi-line constructions
    the same way ``migrate_test_fixtures_ac.py`` does — adds a
    trailing comma to the previous kwarg line when ``)`` is on its own
    line, otherwise inserts inline."""
    lines = source.splitlines(keepends=True)
    for _lineno, end_lineno, end_col in calls:
        idx = end_lineno - 1
        line = lines[idx]
        cut = end_col - 1
        paren_alone_on_line = line[:cut].strip() == ""
        kwarg = f"acceptance_criteria=[{_PLACEHOLDER_BULLET}]"
        if paren_alone_on_line:
            prev_idx = idx - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            interior_indent = "    "
            if prev_idx >= 0:
                stripped = lines[prev_idx].rstrip("\n")
                interior_indent = stripped[: len(stripped) - len(stripped.lstrip())]
                tail = stripped.rstrip()
                if tail and not tail.endswith(","):
                    leading_ws = stripped[: len(stripped) - len(stripped.lstrip())]
                    trailing_ws_and_nl = lines[prev_idx][len(stripped):]
                    lines[prev_idx] = (
                        leading_ws
                        + tail[len(leading_ws):]
                        + ","
                        + trailing_ws_and_nl
                    )
            injection = f"{interior_indent}{kwarg},\n"
            lines[idx] = line[:cut] + injection + line[cut:]
        else:
            injection = f", {kwarg}"
            lines[idx] = line[:cut] + injection + line[cut:]
    return "".join(lines)


def migrate_file(path: Path) -> bool:
    source = path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    calls = _scan(tree)
    if not calls:
        return False
    new_source = _inject(source, calls)
    if new_source == source:
        return False
    # Verify the result still parses before writing.
    try:
        ast.parse(new_source)
    except SyntaxError as exc:
        print(f"skip: {path}: migration produced invalid syntax ({exc})", file=sys.stderr)
        return False
    path.write_text(new_source)
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: migrate_epic_acceptance_criteria.py tests/")
        return 2
    root = Path(argv[0])
    changed = 0
    for path in sorted(root.rglob("*.py")):
        if migrate_file(path):
            changed += 1
            print(f"migrated: {path}")
    print(f"\n{changed} file(s) migrated.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
