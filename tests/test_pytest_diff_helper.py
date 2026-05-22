"""Tests for the diff-parsing helper that drives the test-rigor gates.

The helper is invoked as a subprocess via ``python -m
jig.check_helpers.pytest_diff <red|green>`` from a scripted check.
These tests exercise the internal extraction logic against synthetic
diff inputs — we don't shell out to git for unit-test isolation.
"""

from __future__ import annotations

from jig.check_helpers.pytest_diff import (
    _extract_test_entries,
    _select_node_ids,
)


def test_extract_simple_added_function() -> None:
    diff = """\
diff --git a/tests/test_foo.py b/tests/test_foo.py
+++ b/tests/test_foo.py
@@ -0,0 +1,3 @@
+def test_alpha():
+    assert False
+
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == ["tests/test_foo.py::test_alpha"]


def test_extract_added_function_inside_existing_class() -> None:
    # The class header is context (``  class``); the function is
    # added (``+ def``). The node-id must still include the class.
    diff = """\
+++ b/tests/test_foo.py
@@ -10,3 +10,6 @@
 class TestFoo:
     def test_existing(self):
         pass
+    def test_new(self):
+        assert False
+
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == ["tests/test_foo.py::TestFoo::test_new"]


def test_extract_added_class_and_function() -> None:
    diff = """\
+++ b/tests/test_bar.py
@@ -0,0 +1,4 @@
+class TestBar:
+    def test_one(self):
+        assert False
+
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == ["tests/test_bar.py::TestBar::test_one"]


def test_extract_async_function() -> None:
    diff = """\
+++ b/tests/test_baz.py
@@ -0,0 +1,3 @@
+async def test_async_alpha():
+    assert False
+
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == ["tests/test_baz.py::test_async_alpha"]


def test_baseline_comment_marker_excludes_function() -> None:
    diff = """\
+++ b/tests/test_x.py
@@ -0,0 +1,4 @@
+# tdd-baseline: smoke test for pre-scaffolded transport
+def test_smoke():
+    assert True
+
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == []
    # Entry exists but is flagged baseline.
    assert len(entries) == 1
    assert entries[0].baseline is True


def test_baseline_decorator_marker_excludes_function() -> None:
    diff = """\
+++ b/tests/test_x.py
@@ -0,0 +1,4 @@
+@pytest.mark.tdd_baseline
+def test_smoke():
+    assert True
+
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == []


def test_modified_function_body_counts_as_new() -> None:
    # ``def`` line is context (existed before); body changed. The
    # function was modified, so the gate should fire on it.
    diff = """\
+++ b/tests/test_x.py
@@ -5,3 +5,4 @@
 def test_existing():
     x = 1
+    y = 2
     assert x == 1
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == ["tests/test_x.py::test_existing"]


def test_non_test_file_ignored() -> None:
    diff = """\
+++ b/src/hn_cli/client.py
@@ -0,0 +1,2 @@
+def test_helper():
+    pass
"""
    entries = _extract_test_entries(diff)
    assert _select_node_ids(entries) == []


def test_non_test_function_in_test_file_ignored() -> None:
    diff = """\
+++ b/tests/test_foo.py
@@ -0,0 +1,3 @@
+def _helper_fixture():
+    return 42
+
"""
    entries = _extract_test_entries(diff)
    assert _select_node_ids(entries) == []


def test_multiple_files_multiple_tests() -> None:
    diff = """\
+++ b/tests/test_alpha.py
@@ -0,0 +1,3 @@
+def test_a():
+    pass
+
+++ b/tests/test_beta.py
@@ -0,0 +1,3 @@
+class TestB:
+    def test_x(self):
+        pass
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == [
        "tests/test_alpha.py::test_a",
        "tests/test_beta.py::TestB::test_x",
    ]


def test_nested_test_directory() -> None:
    diff = """\
+++ b/tests/integration/test_flow.py
@@ -0,0 +1,3 @@
+def test_e2e():
+    pass
"""
    entries = _extract_test_entries(diff)
    ids = _select_node_ids(entries)
    assert ids == ["tests/integration/test_flow.py::test_e2e"]


def test_empty_diff_returns_no_entries() -> None:
    entries = _extract_test_entries("")
    assert entries == []


def test_diff_with_only_context_changes_returns_no_entries() -> None:
    diff = """\
+++ b/tests/test_foo.py
@@ -5,3 +5,3 @@
 def test_existing():
     x = 1
     assert x == 1
"""
    entries = _extract_test_entries(diff)
    assert _select_node_ids(entries) == []
