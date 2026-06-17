"""Add a placeholder AC section to test-fixture Ticket constructions.

One-shot migration script for the `feat/ticket-ac-required` PR. The
Ticket model now requires a discoverable Acceptance Criteria section
for work-type tickets (FEATURE / BUGFIX / REFACTOR / SPIKE / PERF /
MIGRATION). Test fixtures construct hundreds of such tickets with
minimal kwargs — this script injects a placeholder description into
those call sites in-place.

Strategy:
  1. Parse each test file with ``ast`` to find every ``Ticket(...)``
     call.
  2. Check whether the call's ``work_type`` kwarg is a work type.
  3. Check whether ``description`` is already supplied (any non-empty
     value).
  4. If a work-type ticket has no description, inject
     ``description=_PLACEHOLDER_AC`` immediately before the closing
     parenthesis, preserving formatting otherwise.

Idempotent: re-runs are no-ops because we skip calls that already
have a ``description`` kwarg.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_WORK_TYPE_VALUES: frozenset[str] = frozenset(
    {"FEATURE", "BUGFIX", "REFACTOR", "SPIKE", "PERF", "MIGRATION"}
)
_WORK_TYPE_STRINGS: frozenset[str] = frozenset(
    {"feature", "bugfix", "refactor", "spike", "perf", "migration"}
)

# String constant referenced by the injection so tests reading
# ``description`` against this placeholder don't need to inline the
# bytes. Imported from a tiny helper module the migration also drops
# into ``tests/``.
_PLACEHOLDER_CONST = "TICKET_AC_PLACEHOLDER"


def _is_work_type_kw(value: ast.expr) -> bool:
    """``work_type=WorkType.FEATURE`` or ``work_type="feature"``."""
    if isinstance(value, ast.Attribute):
        if isinstance(value.value, ast.Name) and value.value.id == "WorkType":
            return value.attr in _WORK_TYPE_VALUES
    if isinstance(value, ast.Constant) and isinstance(value.value, str):
        return value.value in _WORK_TYPE_STRINGS
    return False


def _is_ticket_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name) and func.id == "Ticket":
        return True
    if isinstance(func, ast.Attribute) and func.attr == "Ticket":
        return True
    return False


def _scan(
    tree: ast.Module,
) -> list[tuple[int, int, int, str, int]]:
    """Return one entry per ``Ticket(...)`` call that needs migration.

    Each entry is ``(lineno, end_lineno, end_col_offset, kind, start_col)``:

    - ``kind="inject"``: the call has no ``description`` kwarg — we
      need to inject ``description=PLACEHOLDER`` before the closing
      ``)`` of the call.  ``start_col`` is unused (set to ``-1``).
    - ``kind="replace"``: the call has a string-literal ``description``
      that lacks an AC section.  ``start_col`` is the literal's start
      column so ``_inject`` can splice an ``+ PLACEHOLDER`` onto the
      end of the existing string.

    The list is sorted by end position descending so we can rewrite
    the source bottom-up without invalidating earlier offsets.
    """
    calls: list[tuple[int, int, int, str, int]] = []
    for node in ast.walk(tree):
        if not _is_ticket_call(node):
            continue
        assert isinstance(node, ast.Call)
        work_type_kw = None
        description_kw = None
        for kw in node.keywords:
            if kw.arg == "work_type":
                work_type_kw = kw.value
            elif kw.arg == "description":
                description_kw = kw.value
        if work_type_kw is None:
            continue
        if not _is_work_type_kw(work_type_kw):
            continue
        # ``description`` already supplied? Inject only when it's
        # demonstrably AC-less. We can only check string literals; for
        # non-literal values (variable refs, calls) skip migration and
        # let the test fail loudly so the operator decides what to do.
        if description_kw is not None:
            if not isinstance(description_kw, ast.Constant) or not isinstance(
                description_kw.value, str
            ):
                continue
            from re import search as _re_search

            if _re_search(
                r"(?im)^[ \t]*(?:\#{2,3}[ \t]+(?:acceptance|done.when|acs)|"
                r"\*\*(?:acceptance|done.when|acs)[^*]*\*\*)",
                description_kw.value,
            ):
                # Already has an AC section; nothing to do.
                continue
            # Existing literal lacks AC. Replace the literal's value with
            # the placeholder by emitting a call-range that points at the
            # string-literal node, plus a flag. We piggyback on the same
            # tuple shape but with the description node's end offset.
            assert description_kw.end_lineno is not None
            assert description_kw.end_col_offset is not None
            calls.append(
                (
                    description_kw.lineno,
                    description_kw.end_lineno,
                    description_kw.end_col_offset,
                    "replace",
                    description_kw.col_offset,
                )
            )
            continue
        assert node.end_lineno is not None
        assert node.end_col_offset is not None
        calls.append((node.lineno, node.end_lineno, node.end_col_offset, "inject", -1))
    calls.sort(key=lambda c: (c[1], c[2]), reverse=True)
    return calls


def _last_nonspace_char_before(source_lines: list[str], idx: int, cut: int) -> str:
    """Find the last non-whitespace character before position ``cut`` on
    line ``idx``, walking backward across lines if needed. Returns ``""``
    if no such character exists (paren-on-its-own line with empty body).
    """
    line = source_lines[idx]
    head = line[:cut].rstrip()
    if head:
        return head[-1]
    for back in range(idx - 1, -1, -1):
        prev = source_lines[back].rstrip("\n").rstrip()
        if prev:
            return prev[-1]
    return ""


def _inject(source: str, calls: list[tuple[int, int, int, str, int]]) -> str:
    """Inject ``description=TICKET_AC_PLACEHOLDER`` just before the
    closing ``)`` of each call. The call list MUST be sorted bottom up
    so earlier offsets stay valid as we rewrite.

    Three formatting variants to preserve:
      * Multi-line, trailing comma — last kwarg ends with ``,`` and
        ``)`` sits on its own indented line. Inject on a new line
        above the ``)`` matching the interior indent, ending in ``,``.
      * Multi-line, NO trailing comma — last kwarg has no trailing
        comma but ``)`` is on its own line. Add a trailing ``,`` to
        the previous content line and then inject as multi-line.
      * Single-line — all args inline before ``)``. Inject
        ``, description=...`` immediately before the ``)``.
    """
    lines = source.splitlines(keepends=True)
    for entry in calls:
        _lineno, end_lineno, end_col, kind, start_col = entry
        idx = end_lineno - 1
        line = lines[idx]
        cut = end_col - 1  # position of ``)`` (for inject) or last char (replace)

        if kind == "replace":
            # APPEND a placeholder AC to the existing string literal.
            # The literal spans (lineno-1, start_col) .. (end_lineno-1,
            # end_col). For simplicity we only handle single-line
            # literals — multi-line string descriptions are rare in
            # tests and easier to fix by hand.
            #
            # We preserve the original content (tests may assert on it)
            # by concatenating the placeholder constant after the
            # literal: ``"original text" + TICKET_AC_PLACEHOLDER``.
            if _lineno != end_lineno:
                continue
            line_idx = _lineno - 1
            ln = lines[line_idx]
            literal = ln[start_col:end_col]
            replacement = f"{literal} + '\\n' + {_PLACEHOLDER_CONST}"
            lines[line_idx] = ln[:start_col] + replacement + ln[end_col:]
            continue

        # Is ``)`` on its own line (preceded only by whitespace on this
        # line)? That's the multi-line signal — the user clearly chose
        # to spread the call across lines and wants the injection to
        # follow suit.
        paren_alone_on_line = line[:cut].strip() == ""

        if paren_alone_on_line:
            # Multi-line. Inject on its own line above the ``)``.
            # Find interior indent from the most recent non-blank line
            # above the ``)``.
            prev_idx = idx - 1
            while prev_idx >= 0 and not lines[prev_idx].strip():
                prev_idx -= 1
            interior_indent = "    "
            if prev_idx >= 0:
                stripped = lines[prev_idx].rstrip("\n")
                interior_indent = stripped[: len(stripped) - len(stripped.lstrip())]
                # If that previous line doesn't end with a comma, add
                # one so our injection sits cleanly after it.
                tail = stripped.rstrip()
                if tail and not tail.endswith(","):
                    insert_at = len(tail)
                    # Reconstruct the line with the comma inserted.
                    leading_ws = stripped[: len(stripped) - len(stripped.lstrip())]
                    trailing_ws_and_nl = lines[prev_idx][len(stripped) :]
                    lines[prev_idx] = (
                        leading_ws + tail[len(leading_ws) :] + "," + trailing_ws_and_nl
                    )
                    # Recompute stripped/insert just for clarity; not
                    # used further below.
                    _ = insert_at
            injection = f"{interior_indent}description={_PLACEHOLDER_CONST},\n"
            lines[idx] = line[:cut] + injection + line[cut:]
        else:
            # Single-line — inject inline before the close paren with
            # a leading ``, ``.
            injection = f", description={_PLACEHOLDER_CONST}"
            lines[idx] = line[:cut] + injection + line[cut:]
    return "".join(lines)


def _needs_import(source: str) -> bool:
    """True iff our placeholder constant is used but not yet imported."""
    if _PLACEHOLDER_CONST not in source:
        return False
    # Naive — looks for any import that brings the name into scope.
    import_re = f"from tests._test_ticket import .*{_PLACEHOLDER_CONST}"
    return import_re not in source and f"import {_PLACEHOLDER_CONST}" not in source


def _add_import(source: str) -> str:
    """Insert the helper import after the last existing top-level import."""
    import_line = f"from tests._test_ticket import {_PLACEHOLDER_CONST}\n"
    tree = ast.parse(source)
    last_import_end = 0
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            assert node.end_lineno is not None
            last_import_end = max(last_import_end, node.end_lineno)
    if last_import_end == 0:
        return import_line + source
    lines = source.splitlines(keepends=True)
    lines.insert(last_import_end, import_line)
    return "".join(lines)


def migrate_file(path: Path) -> bool:
    """Returns True iff the file was modified."""
    source = path.read_text()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    calls = _scan(tree)
    if not calls:
        return False
    new_source = _inject(source, calls)
    if _needs_import(new_source):
        new_source = _add_import(new_source)
    if new_source == source:
        return False
    path.write_text(new_source)
    return True


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: migrate_test_fixtures_ac.py tests/")
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
