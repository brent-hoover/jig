"""Tests for the diff helper that drives the test-rigor gates.

The helper has three separable pieces, each unit-testable in
isolation:

* ``_extract_changed_lines`` — pure function from unified diff text
  to ``{file: {added_line_numbers}}``. No filesystem.
* ``_resolve_test_entries`` — AST-based: given the changed-line map
  and a worktree path, resolves to pytest node-ids by reading the
  current source. Filesystem-backed via ``tmp_path``.
* ``_red_verdict_from_junit`` — pure function from a junit-xml path
  to a red-mode exit code. Tested with synthetic XML files.

The integration of these pieces (subprocess + git diff) is covered
by ``test_test_rigor_gating.py``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from jig.check_helpers.pytest_diff import (
    _detect_base,
    _extract_changed_lines,
    _red_verdict_from_junit,
    _ref_resolves,
    _resolve_test_entries,
    _select_node_ids,
    main,
)


# ---------------------------------------------------------------------------
# _extract_changed_lines
# ---------------------------------------------------------------------------


def test_changed_lines_single_hunk_single_add() -> None:
    diff = """\
+++ b/tests/test_foo.py
@@ -0,0 +1,3 @@
+def test_alpha():
+    assert False
+
"""
    assert _extract_changed_lines(diff) == {"tests/test_foo.py": {1, 2, 3}}


def test_changed_lines_multiple_hunks_same_file() -> None:
    diff = """\
+++ b/tests/test_foo.py
@@ -5,0 +6,1 @@
+    new_line_a
@@ -20,0 +22,2 @@
+    new_line_b
+    new_line_c
"""
    assert _extract_changed_lines(diff) == {"tests/test_foo.py": {6, 22, 23}}


def test_changed_lines_omits_non_test_files() -> None:
    diff = """\
+++ b/src/hn_cli/client.py
@@ -0,0 +1,1 @@
+def helper(): pass
+++ b/tests/test_x.py
@@ -0,0 +1,1 @@
+def test_x(): pass
"""
    assert _extract_changed_lines(diff) == {"tests/test_x.py": {1}}


def test_changed_lines_minus_lines_dont_advance_new_lineno() -> None:
    # A pure deletion hunk reports ``+1,0`` (no new content). We
    # should not record any new-side line numbers for it.
    diff = """\
+++ b/tests/test_foo.py
@@ -5,2 +5,0 @@
-old_a
-old_b
@@ -10,0 +9,1 @@
+new_x
"""
    assert _extract_changed_lines(diff) == {"tests/test_foo.py": {9}}


def test_changed_lines_empty_diff() -> None:
    assert _extract_changed_lines("") == {}


def test_changed_lines_short_form_hunk_header() -> None:
    # Git omits the ``,N`` count when it's 1.
    diff = """\
+++ b/tests/test_foo.py
@@ -5 +5 @@
-old
+new
"""
    assert _extract_changed_lines(diff) == {"tests/test_foo.py": {5}}


def test_changed_lines_nested_test_directory() -> None:
    diff = """\
+++ b/tests/integration/test_flow.py
@@ -0,0 +10,2 @@
+def test_e2e():
+    pass
"""
    assert _extract_changed_lines(diff) == {"tests/integration/test_flow.py": {10, 11}}


# ---------------------------------------------------------------------------
# _resolve_test_entries
# ---------------------------------------------------------------------------


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _resolve(files_with_changes: dict[str, set[int]], worktree: Path):
    """Test-side wrapper that asserts no fatal errors and returns entries.

    Most tests are happy-path and expect a clean (entries, []) tuple;
    asserting in the helper keeps each call site terse. Tests that
    exercise the fatal path call ``_resolve_test_entries`` directly.
    """
    entries, fatal = _resolve_test_entries(files_with_changes, worktree)
    assert fatal == [], f"unexpected fatal errors: {fatal}"
    return entries


def test_resolve_top_level_function(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests" / "test_foo.py",
        "def test_alpha():\n    assert False\n",
    )
    entries = _resolve({"tests/test_foo.py": {1, 2}}, tmp_path)
    assert _select_node_ids(entries) == ["tests/test_foo.py::test_alpha"]


def test_resolve_method_inside_existing_class(tmp_path: Path) -> None:
    # The class header is at line 1; the changed method body is much
    # deeper. The old diff-context approach would miss the class
    # header. AST resolution must still bind the method to the class.
    body = "\n".join(
        [
            "class TestFoo:",
            "    def test_existing(self):",
            "        pass",
            "",
            "    def test_other(self):",
            "        pass",
            "",
            "    def test_deep_method(self):",
            "        # method added far below the class header",
            "        assert False",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_foo.py", body)
    # Lines 8-10 are the new method.
    entries = _resolve({"tests/test_foo.py": {8, 9, 10}}, tmp_path)
    assert _select_node_ids(entries) == ["tests/test_foo.py::TestFoo::test_deep_method"]


def test_resolve_only_overlapping_functions_selected(tmp_path: Path) -> None:
    # Three test functions; the change touches one. Only that one
    # emits a node-id.
    body = "\n".join(
        [
            "def test_a():",
            "    pass",
            "",
            "def test_b():",
            "    pass",
            "",
            "def test_c():",
            "    pass",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_foo.py", body)
    # Line 5 is inside test_b.
    entries = _resolve({"tests/test_foo.py": {5}}, tmp_path)
    assert _select_node_ids(entries) == ["tests/test_foo.py::test_b"]


def test_resolve_async_function(tmp_path: Path) -> None:
    body = "async def test_alpha():\n    assert False\n"
    _write(tmp_path / "tests" / "test_foo.py", body)
    entries = _resolve({"tests/test_foo.py": {1, 2}}, tmp_path)
    assert _select_node_ids(entries) == ["tests/test_foo.py::test_alpha"]


def test_resolve_baseline_comment_marker(tmp_path: Path) -> None:
    body = "\n".join(
        [
            "# tdd-baseline: smoke test for prior scaffolding",
            "def test_smoke():",
            "    assert True",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_x.py", body)
    entries = _resolve({"tests/test_x.py": {1, 2, 3}}, tmp_path)
    assert _select_node_ids(entries) == []
    assert len(entries) == 1
    assert entries[0].baseline is True


def test_resolve_baseline_decorator_plain(tmp_path: Path) -> None:
    body = "\n".join(
        [
            "import pytest",
            "",
            "@pytest.mark.tdd_baseline",
            "def test_smoke():",
            "    assert True",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_x.py", body)
    entries = _resolve({"tests/test_x.py": {3, 4, 5}}, tmp_path)
    assert _select_node_ids(entries) == []


def test_resolve_baseline_decorator_call(tmp_path: Path) -> None:
    body = "\n".join(
        [
            "import pytest",
            "",
            '@pytest.mark.tdd_baseline(reason="prior fixture")',
            "def test_smoke():",
            "    assert True",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_x.py", body)
    entries = _resolve({"tests/test_x.py": {3, 4, 5}}, tmp_path)
    assert _select_node_ids(entries) == []


def test_resolve_baseline_with_other_decorators(tmp_path: Path) -> None:
    # ``@pytest.mark.tdd_baseline`` mixed with other decorators still
    # opts out.
    body = "\n".join(
        [
            "import pytest",
            "",
            "@pytest.mark.parametrize('x', [1, 2])",
            "@pytest.mark.tdd_baseline",
            "def test_smoke(x):",
            "    assert True",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_x.py", body)
    entries = _resolve({"tests/test_x.py": {3, 4, 5, 6}}, tmp_path)
    assert _select_node_ids(entries) == []


def test_resolve_baseline_comment_above_decorator(tmp_path: Path) -> None:
    # Comment marker sits above the FIRST decorator, not directly
    # above ``def``. Still opts out.
    body = "\n".join(
        [
            "import pytest",
            "",
            "# tdd-baseline: scaffolding from ticket-005",
            "@pytest.mark.parametrize('x', [1])",
            "def test_smoke(x):",
            "    assert True",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_x.py", body)
    entries = _resolve({"tests/test_x.py": {3, 4, 5, 6}}, tmp_path)
    assert _select_node_ids(entries) == []


def test_resolve_decorator_only_change_selects_function(tmp_path: Path) -> None:
    """A change touching only a decorator line (e.g. adding a new
    ``parametrize`` case above an existing ``def``) must still
    select the function. Otherwise a developer can add a new
    parametrized case without it being gated."""
    body = "\n".join(
        [
            "import pytest",
            "",
            "@pytest.mark.parametrize('x', [1, 2, 3])",
            "def test_smoke(x):",
            "    assert x > 0",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_x.py", body)
    # Only the decorator line (3) is in the change set; the def
    # itself (4) and the body (5) are unchanged. Pre-fix this
    # produced an empty selection because the overlap window
    # started at ``def``.
    entries = _resolve({"tests/test_x.py": {3}}, tmp_path)
    assert _select_node_ids(entries) == ["tests/test_x.py::test_smoke"]


def test_resolve_non_test_function_ignored(tmp_path: Path) -> None:
    body = "def _helper():\n    return 1\n"
    _write(tmp_path / "tests" / "test_foo.py", body)
    entries = _resolve({"tests/test_foo.py": {1, 2}}, tmp_path)
    assert _select_node_ids(entries) == []


def test_resolve_non_test_class_ignored(tmp_path: Path) -> None:
    # ``Helper`` is not a ``Test*`` class so pytest wouldn't collect
    # its methods; nor should the gate.
    body = "\n".join(
        [
            "class Helper:",
            "    def test_inside_helper(self):",
            "        pass",
            "",
        ]
    )
    _write(tmp_path / "tests" / "test_foo.py", body)
    entries = _resolve({"tests/test_foo.py": {2, 3}}, tmp_path)
    assert _select_node_ids(entries) == []


def test_resolve_missing_source_file_is_fatal(tmp_path: Path) -> None:
    """A changed file the helper can't read is a fatal error.

    Pre-fix this was silently skipped — if the file was the only
    one in the diff, ``main`` returned 0 vacuously and disabled
    the gate. Now ``_resolve_test_entries`` reports the failure
    via the ``fatal`` return list, and ``main`` propagates it as
    a non-zero exit.
    """
    entries, fatal = _resolve_test_entries({"tests/test_gone.py": {1, 2}}, tmp_path)
    assert entries == []
    assert len(fatal) == 1
    assert "tests/test_gone.py" in fatal[0]


def test_resolve_syntax_error_is_fatal(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_broken.py", "def test_x(:\n    pass\n")
    entries, fatal = _resolve_test_entries({"tests/test_broken.py": {1, 2}}, tmp_path)
    assert entries == []
    assert len(fatal) == 1
    assert "tests/test_broken.py" in fatal[0]
    assert "parse" in fatal[0]


def test_resolve_one_broken_file_does_not_silence_other_good_files(
    tmp_path: Path,
) -> None:
    """Mixed input: a parseable file and a broken file. The good
    file still yields its entry; the broken one is reported as
    fatal so the caller can fail the run rather than silently
    proceeding with a partial answer.
    """
    _write(tmp_path / "tests" / "test_ok.py", "def test_a():\n    pass\n")
    _write(tmp_path / "tests" / "test_broken.py", "def test_x(:\n    pass\n")
    entries, fatal = _resolve_test_entries(
        {
            "tests/test_ok.py": {1, 2},
            "tests/test_broken.py": {1, 2},
        },
        tmp_path,
    )
    assert _select_node_ids(entries) == ["tests/test_ok.py::test_a"]
    assert len(fatal) == 1
    assert "tests/test_broken.py" in fatal[0]


def test_resolve_multiple_files(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_alpha.py", "def test_a():\n    pass\n")
    _write(
        tmp_path / "tests" / "test_beta.py",
        "class TestB:\n    def test_x(self):\n        pass\n",
    )
    entries = _resolve(
        {
            "tests/test_alpha.py": {1, 2},
            "tests/test_beta.py": {2, 3},
        },
        tmp_path,
    )
    assert sorted(_select_node_ids(entries)) == [
        "tests/test_alpha.py::test_a",
        "tests/test_beta.py::TestB::test_x",
    ]


def test_resolve_empty_changes_returns_empty(tmp_path: Path) -> None:
    entries, fatal = _resolve_test_entries({}, tmp_path)
    assert entries == []
    assert fatal == []


# ---------------------------------------------------------------------------
# _red_verdict_from_junit
# ---------------------------------------------------------------------------


def _junit(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "report.xml"
    p.write_text(body)
    return p


_FAIL = "<failure message='boom'>traceback</failure>"
_ERR = "<error message='boom'>traceback</error>"
_SKIP = "<skipped message='reason' />"


def test_red_verdict_all_failures_passes(tmp_path: Path) -> None:
    xml = (
        "<testsuites><testsuite>"
        f"<testcase classname='tests.test_x' name='test_a'>{_FAIL}</testcase>"
        f"<testcase classname='tests.test_x' name='test_b'>{_FAIL}</testcase>"
        "</testsuite></testsuites>"
    )
    assert _red_verdict_from_junit(_junit(tmp_path, xml)) == 0


def test_red_verdict_one_passed_fails(tmp_path: Path) -> None:
    # ``test_b`` has no children — that's pytest's "passed" outcome.
    # Mixed batch must produce non-zero (the gate's whole point).
    xml = (
        "<testsuites><testsuite>"
        f"<testcase classname='tests.test_x' name='test_a'>{_FAIL}</testcase>"
        "<testcase classname='tests.test_x' name='test_b' />"
        "</testsuite></testsuites>"
    )
    assert _red_verdict_from_junit(_junit(tmp_path, xml)) == 1


def test_red_verdict_skipped_fails(tmp_path: Path) -> None:
    # Skipped tests don't count as "red" — the test didn't fail, it
    # didn't run. Gate must reject.
    xml = (
        "<testsuites><testsuite>"
        f"<testcase classname='tests.test_x' name='test_a'>{_FAIL}</testcase>"
        f"<testcase classname='tests.test_x' name='test_b'>{_SKIP}</testcase>"
        "</testsuite></testsuites>"
    )
    assert _red_verdict_from_junit(_junit(tmp_path, xml)) == 1


def test_red_verdict_errored_does_not_satisfy_gate(tmp_path: Path) -> None:
    """``<error>`` is a collection / fixture / import failure — the
    test never reached its assertions, so it can't have proved the
    new behaviour fails. The gate must reject errored testcases
    (the helper's stated contract). A fixture that explicitly
    raises before the assertion would otherwise satisfy red mode
    without exercising the code under test.
    """
    xml = (
        "<testsuites><testsuite>"
        f"<testcase classname='tests.test_x' name='test_a'>{_ERR}</testcase>"
        f"<testcase classname='tests.test_x' name='test_b'>{_FAIL}</testcase>"
        "</testsuite></testsuites>"
    )
    assert _red_verdict_from_junit(_junit(tmp_path, xml)) == 1


def test_red_verdict_empty_xml_fails(tmp_path: Path) -> None:
    xml = "<testsuites><testsuite></testsuite></testsuites>"
    assert _red_verdict_from_junit(_junit(tmp_path, xml)) == 1


def test_red_verdict_unreadable_xml_fails(tmp_path: Path) -> None:
    p = tmp_path / "broken.xml"
    p.write_text("not valid xml")
    assert _red_verdict_from_junit(p) == 1


def test_red_verdict_missing_xml_fails(tmp_path: Path) -> None:
    assert _red_verdict_from_junit(tmp_path / "doesnotexist.xml") == 1


# ---------------------------------------------------------------------------
# _detect_base / _ref_resolves / main base-validation
# ---------------------------------------------------------------------------


def _git_init_with_commit(path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "--allow-empty",
            "-qm",
            "init",
        ],
        cwd=path,
        check=True,
    )


def test_detect_base_prefers_env_var(monkeypatch) -> None:
    monkeypatch.setenv("JIG_TICKET_BASE", "feat/whatever")
    assert _detect_base() == "feat/whatever"


def test_detect_base_strips_whitespace(monkeypatch) -> None:
    monkeypatch.setenv("JIG_TICKET_BASE", "  develop\n")
    assert _detect_base() == "develop"


def test_ref_resolves_true_for_existing(tmp_path: Path) -> None:
    _git_init_with_commit(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert _ref_resolves("main") is True
        assert _ref_resolves("HEAD") is True
    finally:
        os.chdir(cwd)


def test_ref_resolves_false_for_missing(tmp_path: Path) -> None:
    _git_init_with_commit(tmp_path)
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert _ref_resolves("does-not-exist") is False
        # Also: ``origin/develop`` doesn't resolve when there's no
        # remote. This is the exact case PR #74's first reviewer
        # flagged — orchestrator must not pass ``origin/<branch>``.
        assert _ref_resolves("origin/develop") is False
    finally:
        os.chdir(cwd)


def test_main_fails_loudly_when_base_ref_missing(tmp_path: Path, monkeypatch) -> None:
    """A bogus ``JIG_TICKET_BASE`` must fail the check, not vacuously pass.

    Pre-fix behaviour: ``git diff origin/missing..HEAD`` returned
    empty stdout, the helper saw no test entries, and exited 0.
    The gate disabled itself silently. Now ``main`` validates the
    ref via ``_ref_resolves`` and exits 1 with a clear message.
    """
    _git_init_with_commit(tmp_path)
    monkeypatch.setenv("JIG_TICKET_BASE", "definitely-not-a-real-ref")
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert main(["pytest_diff", "red"]) == 1
        assert main(["pytest_diff", "green"]) == 1
    finally:
        os.chdir(cwd)


def test_main_passes_vacuously_when_no_diff_tests(tmp_path: Path, monkeypatch) -> None:
    """A real ref resolving to no new test functions still passes 0.

    This is the documented "empty diff → vacuous pass" path.
    """
    _git_init_with_commit(tmp_path)
    monkeypatch.setenv("JIG_TICKET_BASE", "HEAD")
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        # ``HEAD..HEAD`` is empty by construction.
        assert main(["pytest_diff", "red"]) == 0
        assert main(["pytest_diff", "green"]) == 0
    finally:
        os.chdir(cwd)


def test_main_rejects_unknown_mode() -> None:
    assert main(["pytest_diff", "bogus"]) == 2
    assert main(["pytest_diff"]) == 2


def test_main_fails_when_changed_test_file_unparseable(
    tmp_path: Path, monkeypatch
) -> None:
    """If a changed test file fails to parse, ``main`` must exit
    non-zero even if no other test files were changed.

    Pre-fix the helper silently skipped the file, ended up with
    zero node-ids, and exited 0 — disabling the gate. The fix
    propagates the parse failure as a hard error.
    """
    _git_init_with_commit(tmp_path)
    # Author and commit a broken test file so it shows up in the
    # diff against the initial commit.
    broken = tmp_path / "tests" / "test_broken.py"
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("def test_x(:\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=t@t",
            "-c",
            "user.name=t",
            "commit",
            "-qm",
            "broken",
        ],
        cwd=tmp_path,
        check=True,
    )
    monkeypatch.setenv("JIG_TICKET_BASE", "HEAD~1")
    cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        assert main(["pytest_diff", "red"]) == 1
        assert main(["pytest_diff", "green"]) == 1
    finally:
        os.chdir(cwd)
