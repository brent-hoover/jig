"""Run pytest against tests added/modified in the current ticket's diff.

Two modes:

* ``green`` — every diff-test must pass. Exit code is pytest's exit
  code (0 on pass, non-zero on fail). Wires the ``pytest-diff-tests``
  catalog entry to the implement-phase gate: dev must produce code
  that satisfies the tests written at the test phase.

* ``red`` — every diff-test must FAIL. Exit 0 when pytest's exit is
  non-zero (all tests failed); exit 1 when pytest exits clean (some
  tests passed, which means they're not actually testing new
  behavior). Wires ``pytest-new-tests-fail`` to the test-phase gate:
  TDD discipline — new tests must fail before impl exists.

Diff scope:

The helper parses ``git diff <base> HEAD`` against the worktree's
base branch (read from ``JIG_TICKET_BASE`` env var, falling back to
``git merge-base HEAD origin/develop``) and collects pytest node-ids
for every test function added or modified in the diff. Tests outside
the diff are not checked — full-suite execution lives at the
validate-phase ``pytest-all`` catalog entry.

A test function whose immediate preceding comment is
``# tdd-baseline: <reason>`` is excluded from the new-tests list, as
is any function decorated with ``@pytest.mark.tdd_baseline``. This
opt-out is for tests that legitimately pass at commit time (e.g.
exercising scaffolding from a prior ticket). Without an explicit
marker, the check fires.

Empty diff (no new test functions) → exit 0 vacuously. Gates don't
fire on phases that don't add tests.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class _TestEntry:
    """One added/modified test function from the diff.

    ``node_id`` is the pytest-format identifier
    (``tests/path/test_x.py::TestY::test_z``). ``baseline`` records
    whether the function has an explicit opt-out marker.
    """

    node_id: str
    baseline: bool


# Matches the ``--- a/<path>`` or ``+++ b/<path>`` lines that open
# each diff hunk for a file.
_DIFF_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")
# Test-file path predicate. We accept both flat layouts
# (``tests/test_x.py``) and nested (``tests/sub/test_x.py``).
_TEST_FILE_PATH = re.compile(r"(?:^|/)test_[^/]+\.py$|/tests/.+_test\.py$")
# Matches a class definition line in the diff (added or context). We
# only care about ``Test*`` classes — pytest's collection convention.
# ``\s*`` after the diff marker handles indented nested classes
# (uncommon for test files but legal).
_CLASS_DEF = re.compile(r"^[+ ]\s*class\s+(Test[A-Za-z0-9_]*)")
# Matches a test function definition (added or context). ``\s*`` after
# the diff marker handles methods nested inside a class — every method
# is indented relative to its enclosing class header, so the diff line
# is ``+    def test_*`` or ``     def test_*``.
_DEF_LINE = re.compile(r"^[+ ]\s*(?:async\s+)?def\s+(test_[A-Za-z0-9_]+)")
# Baseline marker forms: ``# tdd-baseline: reason`` (comment above the
# def) or ``@pytest.mark.tdd_baseline`` (decorator).
_BASELINE_COMMENT = re.compile(r"^[+ ]\s*#\s*tdd-baseline\b")
_BASELINE_DECORATOR = re.compile(r"^[+ ]\s*@pytest\.mark\.tdd_baseline\b")


def _detect_base() -> str:
    """Resolve the diff-base branch.

    ``JIG_TICKET_BASE`` (set by the orchestrator from the worktree's
    ``base_branch``) is the canonical source. The ``origin/develop``
    fallback covers manual invocation by an operator with a sensible
    default for most jig projects.

    Returns the base as a git ref string. The diff is computed via
    ``git diff <base>..HEAD``.
    """
    env_base = os.environ.get("JIG_TICKET_BASE", "").strip()
    if env_base:
        return env_base
    # Fallback: assume origin/develop.
    result = subprocess.run(
        ["git", "merge-base", "HEAD", "origin/develop"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode == 0:
        return result.stdout.strip()
    # Last-ditch: HEAD~1. Useful when run outside the orchestrator on
    # a fresh checkout with no origin/develop ref.
    return "HEAD~1"


def _git_diff(base: str) -> str:
    """Return the unified diff against ``base``.

    ``--unified=0`` keeps the output compact and removes contextual
    lines that would confuse the function-tracking pass. The patterns
    we care about (``def test_*``, ``class Test*``, baseline markers)
    only need the added lines themselves.

    We DO want context for ``class Test*`` lines because a new test
    inside an existing class won't have a ``+`` class line — the
    enclosing class is unchanged. Asking for ``--unified=3`` brings
    in three lines of context which is usually enough to capture the
    class header. Tradeoff: more context = more lines to scan. Three
    lines is the git default and a safe compromise.
    """
    result = subprocess.run(
        [
            "git",
            "diff",
            "--unified=3",
            f"{base}..HEAD",
            "--",
            "tests/",
            "**/test_*.py",
            "**/*_test.py",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout


def _extract_test_entries(diff: str) -> list[_TestEntry]:
    """Walk the diff and collect added/modified test functions.

    A test function is recorded when we see an added (``+ def
    test_*``) line. Tests added inside an existing (context) class
    use the class name in the node-id. Tests outside any class use
    the bare function name.

    Modified-but-not-newly-added bodies count too: any ``+`` line
    inside a function's diff hunks marks that function. We detect
    this by tracking the most-recent function we've seen and flagging
    it when we see new ``+`` lines that aren't header comments,
    decorators, or function defs of their own.
    """
    entries: list[_TestEntry] = []
    seen_node_ids: set[str] = set()
    current_file: str | None = None
    current_class: str | None = None
    # Whether the most-recent function we saw was added/modified in
    # the diff (any ``+`` line in its hunk). Lets us defer node-id
    # emission until we know it's actually changed, not just context.
    current_func: str | None = None
    current_func_changed = False
    # Whether the function above us had a baseline marker (comment or
    # decorator) immediately preceding its ``def``. We track the most
    # recent baseline-marker line and clear it on any non-marker,
    # non-decorator, non-blank intervening line.
    baseline_pending = False

    def _flush_current_func() -> None:
        nonlocal current_func, current_func_changed, baseline_pending
        if current_func is None or current_file is None:
            current_func = None
            current_func_changed = False
            baseline_pending = False
            return
        if not current_func_changed:
            current_func = None
            current_func_changed = False
            baseline_pending = False
            return
        parts: list[str] = [current_file]
        if current_class is not None:
            parts.append(current_class)
        parts.append(current_func)
        node_id = "::".join(parts)
        if node_id not in seen_node_ids:
            seen_node_ids.add(node_id)
            entries.append(_TestEntry(node_id=node_id, baseline=baseline_pending))
        current_func = None
        current_func_changed = False
        baseline_pending = False

    for line in diff.splitlines():
        # File header — start of a new file's diff. Flush the previous
        # function and reset class context.
        if line.startswith("+++ b/"):
            _flush_current_func()
            m = _DIFF_FILE_HEADER.match(line)
            if m and _TEST_FILE_PATH.search(m.group(1)):
                current_file = m.group(1)
                current_class = None
            else:
                current_file = None
                current_class = None
            continue
        if current_file is None:
            continue
        # Skip hunk headers (``@@ ... @@``).
        if line.startswith("@@"):
            continue
        # Track the enclosing class. We accept both added (``+ class``)
        # and context (`` class``) lines so a new test inside an
        # existing class binds to that class.
        cls_match = _CLASS_DEF.match(line)
        if cls_match:
            _flush_current_func()
            current_class = cls_match.group(1)
            continue
        # Test function definition.
        def_match = _DEF_LINE.match(line)
        if def_match:
            # ``_flush_current_func`` resets ``baseline_pending``, so
            # capture it first — the marker collected above belongs to
            # this incoming def, not the outgoing one.
            marker_for_new_def = baseline_pending
            _flush_current_func()
            current_func = def_match.group(1)
            current_func_changed = line.startswith("+")
            baseline_pending = marker_for_new_def
            continue
        # Baseline markers — set the pending flag. Only relevant
        # immediately above a def, so clear on intervening "real"
        # lines.
        if _BASELINE_COMMENT.match(line) or _BASELINE_DECORATOR.match(line):
            baseline_pending = True
            continue
        # Blank lines and other decorators are intervening but don't
        # invalidate the baseline marker.
        if line.strip() in {"", "+", " "}:
            continue
        if line.startswith("+ @") or line.startswith("  @"):
            # Other decorators (parametrize, fixture, etc.) — leave
            # the baseline marker alone, it might still apply to the
            # def we're heading toward.
            continue
        # Any other added line inside the current function counts as
        # a modification of its body.
        if line.startswith("+") and current_func is not None:
            current_func_changed = True
            continue
        # Context line that's not blank, not class, not def. Clears
        # baseline pending if we hadn't reached a def yet.
        if (
            not line.startswith(("+", "-"))
            and baseline_pending
            and current_func is None
        ):
            baseline_pending = False

    _flush_current_func()
    return entries


def _select_node_ids(entries: Iterable[_TestEntry]) -> list[str]:
    """Filter out baseline-marked tests and return the pytest node-id list."""
    return [e.node_id for e in entries if not e.baseline]


def _run_pytest(node_ids: list[str]) -> int:
    """Invoke ``uv run pytest -q`` on the given node-ids; return its exit code."""
    cmd = ["uv", "run", "pytest", "-q", *node_ids]
    result = subprocess.run(cmd, check=False)
    return result.returncode


def main(argv: list[str]) -> int:
    """Entry point. ``argv`` is the full sys.argv (program + args).

    Returns the exit code the check should emit. Caller wires this
    via ``sys.exit(main(sys.argv))``.
    """
    if len(argv) < 2 or argv[1] not in {"red", "green"}:
        sys.stderr.write("usage: pytest_diff <red|green>\n")
        return 2
    mode = argv[1]
    base = _detect_base()
    diff = _git_diff(base)
    entries = _extract_test_entries(diff)
    node_ids = _select_node_ids(entries)
    if not node_ids:
        # No new tests in the diff. Both red and green pass vacuously
        # — the gate only fires when there's something to gate on.
        return 0
    pytest_rc = _run_pytest(node_ids)
    if mode == "green":
        # Green discipline: pytest must succeed. Pass-through.
        return pytest_rc
    # Red discipline: pytest must FAIL. Invert the verdict.
    if pytest_rc == 0:
        sys.stderr.write(
            "tdd: every new test passed at commit time. Either the "
            "implementation already exists or the test isn't actually "
            "exercising new behavior. Mark genuinely pre-existing tests "
            "with `# tdd-baseline: <reason>` to opt out.\n"
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
