"""Run pytest against tests added/modified in the current ticket's diff.

Two modes:

* ``green`` — every diff-test must pass. Exit 0 only when every
  selected node-id passes. Wires ``pytest-diff-tests`` to the
  implement-phase gate: dev's code must satisfy the tests written at
  the test phase.

* ``red`` — every diff-test must FAIL. We invoke pytest with
  ``--junit-xml`` and inspect per-test outcomes: any new test that
  passes, errors, or skips fails the check. Aggregate exit-code
  inversion is wrong here — pytest exits non-zero whenever any test
  fails, even if others passed alongside, so a partial-pass batch
  would otherwise be accepted as "red". Wires
  ``pytest-new-tests-fail`` to the test-phase gate: TDD discipline,
  new tests must fail before impl exists.

Diff scope:

The base ref is read from ``JIG_TICKET_BASE`` (set by the
orchestrator to the worktree's local base branch) with a fallback to
``origin/develop`` for manual invocation. We require the ref to
resolve via ``git rev-parse`` — silent vacuous passes (empty diff
because the ref was bogus) would disable the gate entirely.

For class membership and line ranges we parse the **current
worktree** source files with ``ast`` rather than relying on diff
context. Diff context-based class detection breaks when a test
method is modified deep inside an existing ``class Test*`` whose
header lives outside the 3-line context window. AST resolution is
robust to nesting depth and to context-window misses.

A test function is considered "in the diff" when any line in its
body (``lineno`` through ``end_lineno``) intersects the set of
added/modified line numbers reported by ``git diff``. We use the
plus-side line numbers from each hunk's ``@@`` header.

Baseline opt-out remains: a test whose immediate-preceding source
line is ``# tdd-baseline: <reason>`` or that carries
``@pytest.mark.tdd_baseline`` is dropped from the new-tests list.
Without an explicit marker, the check fires.

Empty diff (no new test functions) → exit 0 vacuously. Gates don't
fire on phases that don't add tests.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class _TestEntry:
    """One added/modified test function resolved against the source tree.

    ``node_id`` is the pytest-format identifier
    (``tests/path/test_x.py::TestY::test_z``). ``baseline`` records
    whether the function has an explicit opt-out marker.
    """

    node_id: str
    baseline: bool


_DIFF_FILE_HEADER = re.compile(r"^\+\+\+ b/(.+)$")
_TEST_FILE_PATH = re.compile(r"(?:^|/)test_[^/]+\.py$|/tests/.+_test\.py$")
# ``@@ -<old>,<oldlen> +<new>,<newlen> @@``. The ``<oldlen>`` and
# ``<newlen>`` parts are optional (git omits them when the count is
# 1). We capture the new-side start line and length.
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
# Inline baseline marker on the source line preceding the def (or its
# decorators). Matches ``#tdd-baseline: …`` and ``# tdd-baseline …``.
_BASELINE_COMMENT = re.compile(r"^\s*#\s*tdd-baseline\b")


def _detect_base() -> str:
    """Resolve the diff-base ref.

    Priority:

    1. ``JIG_TICKET_BASE`` (set by the orchestrator from the
       worktree's local base branch).
    2. ``origin/develop`` if it resolves — sensible default for
       manual invocation in a typical jig project.
    3. ``develop`` then ``main`` then ``master`` as a final
       fallback chain.

    Returns the ref. Caller must validate it resolves before using
    it as a diff base.
    """
    env_base = os.environ.get("JIG_TICKET_BASE", "").strip()
    if env_base:
        return env_base
    for candidate in ("origin/develop", "develop", "main", "master"):
        if _ref_resolves(candidate):
            return candidate
    return "HEAD~1"


def _ref_resolves(ref: str) -> bool:
    """True iff ``git rev-parse --verify`` accepts ``ref``."""
    result = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _git_diff(base: str) -> str:
    """Return the unified diff against ``base`` for test files only.

    We pass ``--unified=0`` because we no longer rely on diff context
    — class membership is resolved from the AST. ``-0`` keeps the
    output compact and makes hunk-header parsing unambiguous.
    """
    result = subprocess.run(
        [
            "git",
            "diff",
            "--unified=0",
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
    if result.returncode != 0:
        # ``git diff`` failing (e.g. bad ref) is treated as a hard
        # error by the caller — log to stderr and return empty.
        sys.stderr.write(
            f"pytest_diff: git diff against {base!r} failed: "
            f"{result.stderr.strip()}\n"
        )
    return result.stdout


def _extract_changed_lines(diff: str) -> dict[str, set[int]]:
    """For each test file in the diff, return added/modified line numbers.

    Line numbers are 1-based positions in the post-change (HEAD)
    version of the file — what ``ast.parse`` will see. We walk the
    hunk headers and record every ``+`` line position.
    """
    files: dict[str, set[int]] = {}
    current_file: str | None = None
    new_line = 0
    in_hunk = False
    for line in diff.splitlines():
        if line.startswith("+++ "):
            m = _DIFF_FILE_HEADER.match(line)
            if m and _TEST_FILE_PATH.search(m.group(1)):
                current_file = m.group(1)
                files.setdefault(current_file, set())
            else:
                current_file = None
            in_hunk = False
            continue
        if current_file is None:
            continue
        if line.startswith("@@"):
            m = _HUNK_HEADER.match(line)
            if m:
                new_line = int(m.group(1))
                in_hunk = True
            else:
                in_hunk = False
            continue
        if not in_hunk:
            continue
        # In ``--unified=0`` mode each hunk is purely ``+`` and/or
        # ``-`` lines; there are no context lines so we don't advance
        # ``new_line`` on context. The first ``+`` belongs to
        # ``new_line``; each subsequent ``+`` advances.
        if line.startswith("+") and not line.startswith("+++"):
            files[current_file].add(new_line)
            new_line += 1
        # ``-`` lines do not consume a new-side line.
    return files


def _walk_test_funcs(
    tree: ast.Module,
) -> Iterable[tuple[ast.FunctionDef | ast.AsyncFunctionDef, str | None]]:
    """Yield each pytest-collectable test function in the module.

    Includes top-level ``test_*`` functions and methods on
    ``Test*`` classes. Nested classes / functions are ignored —
    pytest doesn't collect those, so the gate shouldn't either.
    """
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test_"):
                yield node, None
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if sub.name.startswith("test_"):
                        yield sub, node.name


def _is_baseline(
    func: ast.FunctionDef | ast.AsyncFunctionDef, source_lines: list[str]
) -> bool:
    """True if the function is marked as a tdd-baseline opt-out.

    Two forms recognised:

    * ``@pytest.mark.tdd_baseline`` decorator (with or without call).
    * ``# tdd-baseline: <reason>`` comment on the source line
      immediately above the def (or its first decorator), ignoring
      blank lines and other decorators in between.
    """
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        # ``pytest.mark.tdd_baseline`` is Attribute(attr='tdd_baseline',
        # value=Attribute(attr='mark', value=Name('pytest')))
        if (
            isinstance(target, ast.Attribute)
            and target.attr == "tdd_baseline"
            and isinstance(target.value, ast.Attribute)
            and target.value.attr == "mark"
            and isinstance(target.value.value, ast.Name)
            and target.value.value.id == "pytest"
        ):
            return True
    # Comment scan: start above the first decorator (or the def if
    # there are no decorators). Walk upward, skipping blanks and
    # decorator lines.
    if func.decorator_list:
        first_line = min(d.lineno for d in func.decorator_list) - 1
    else:
        first_line = func.lineno - 1
    i = first_line - 1
    while i >= 0:
        line = source_lines[i].strip()
        if not line:
            i -= 1
            continue
        if line.startswith("@"):
            i -= 1
            continue
        if _BASELINE_COMMENT.match(line):
            return True
        break
    return False


def _resolve_test_entries(
    files_with_changes: dict[str, set[int]], worktree: Path
) -> list[_TestEntry]:
    """Build the pytest node-id list from changed line ranges + AST.

    For each changed test file we parse the **current** source
    (post-change), enumerate every test function pytest would
    collect, and check whether the function's line range overlaps
    any changed line. Overlapping functions emit one node-id each;
    baseline-marked functions are flagged for filtering downstream.
    """
    entries: list[_TestEntry] = []
    seen: set[str] = set()
    for path_rel, changed_lines in sorted(files_with_changes.items()):
        if not changed_lines:
            continue
        file_path = worktree / path_rel
        if not file_path.is_file():
            # File was deleted in the working copy (e.g. test file
            # removed). Nothing to resolve.
            continue
        try:
            source = file_path.read_text()
        except OSError:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            sys.stderr.write(
                f"pytest_diff: {path_rel} fails to parse; skipping\n"
            )
            continue
        source_lines = source.splitlines()
        for func, class_name in _walk_test_funcs(tree):
            start = func.lineno
            end = getattr(func, "end_lineno", start) or start
            if not any(start <= ln <= end for ln in changed_lines):
                continue
            parts: list[str] = [path_rel]
            if class_name is not None:
                parts.append(class_name)
            parts.append(func.name)
            node_id = "::".join(parts)
            if node_id in seen:
                continue
            seen.add(node_id)
            entries.append(
                _TestEntry(node_id=node_id, baseline=_is_baseline(func, source_lines))
            )
    return entries


def _select_node_ids(entries: Iterable[_TestEntry]) -> list[str]:
    """Filter out baseline-marked tests and return the pytest node-id list."""
    return [e.node_id for e in entries if not e.baseline]


def _run_pytest_green(node_ids: list[str]) -> int:
    """Green mode: pytest's aggregate exit code is the verdict."""
    cmd = ["uv", "run", "pytest", "-q", *node_ids]
    return subprocess.run(cmd, check=False).returncode


def _red_verdict_from_junit(xml_path: Path) -> int:
    """Compute the red-mode exit code from a pytest junit-xml file.

    Returns 0 only when every ``<testcase>`` has a ``<failure>`` or
    ``<error>`` child — both count as "did not pass". Passed and
    skipped testcases produce non-zero. An unreadable XML or one
    with no testcases also produces non-zero (a silent zero would
    disable the gate).

    Extracted from ``_run_pytest_red`` so the verdict logic is
    unit-testable without invoking pytest as a subprocess.
    """
    try:
        tree = ET.parse(xml_path)
    except (ET.ParseError, FileNotFoundError):
        sys.stderr.write(
            "pytest_diff: failed to parse pytest junit-xml output — "
            "treating as failure\n"
        )
        return 1
    root = tree.getroot()
    testcases = list(root.iter("testcase"))
    if not testcases:
        # Pytest produced an XML report with no testcases at all.
        # That means collection failed for every node-id (typos,
        # missing files). Fail loudly — a silent zero would disable
        # the gate.
        sys.stderr.write(
            "pytest_diff: pytest collected no tests from the diff "
            "node-ids — fail (gate cannot verify discipline against "
            "zero tests)\n"
        )
        return 1
    bad: list[tuple[str, str]] = []
    for tc in testcases:
        classname = tc.get("classname", "")
        name = tc.get("name", "")
        ident = f"{classname}::{name}" if classname else name
        tags = {child.tag for child in tc}
        if "failure" in tags or "error" in tags:
            # Failures and errors both count as "red" — the test
            # didn't pass, which is what TDD demands.
            continue
        if "skipped" in tags:
            bad.append((ident, "skipped"))
        else:
            bad.append((ident, "passed"))
    if bad:
        sys.stderr.write(
            "tdd: every new test must fail at commit time, but the "
            "following did not:\n"
        )
        for ident, outcome in bad:
            sys.stderr.write(f"  - {ident} ({outcome})\n")
        sys.stderr.write(
            "Mark genuinely pre-existing tests with "
            "`# tdd-baseline: <reason>` (or "
            "`@pytest.mark.tdd_baseline`) to opt out.\n"
        )
        return 1
    return 0


def _run_pytest_red(node_ids: list[str]) -> int:
    """Red mode: every node-id must fail (not pass, not skip, not error).

    We use ``--junit-xml`` and delegate verdict computation to
    ``_red_verdict_from_junit``. Aggregate exit-code inversion
    would mis-accept partial-pass batches as "red" — pytest exits
    non-zero whenever any test failed regardless of how many passed
    alongside.
    """
    with tempfile.NamedTemporaryFile(
        prefix="pytest_diff_", suffix=".xml", delete=False
    ) as f:
        xml_path = Path(f.name)
    try:
        cmd = [
            "uv",
            "run",
            "pytest",
            "-q",
            "--tb=no",
            f"--junit-xml={xml_path}",
            *node_ids,
        ]
        subprocess.run(cmd, check=False)  # ignore aggregate exit code
        return _red_verdict_from_junit(xml_path)
    finally:
        try:
            xml_path.unlink()
        except FileNotFoundError:
            pass


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
    if not _ref_resolves(base):
        sys.stderr.write(
            f"pytest_diff: base ref {base!r} does not resolve in this "
            "worktree. The diff-scoped gate cannot run; treating as "
            "failure rather than silently passing.\n"
        )
        return 1
    diff = _git_diff(base)
    files_changed = _extract_changed_lines(diff)
    entries = _resolve_test_entries(files_changed, Path.cwd())
    node_ids = _select_node_ids(entries)
    if not node_ids:
        # No new tests in the diff. Both red and green pass vacuously
        # — the gate only fires when there's something to gate on.
        return 0
    if mode == "green":
        return _run_pytest_green(node_ids)
    return _run_pytest_red(node_ids)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
