"""Tests for the centralized safe-path validation primitive.

These tests pin down both the per-segment validator and the
``safe_join`` containment check used to defend every id-derived path
in the codebase. The validator is intentionally strict so that file
paths derived from operator-supplied or agent-supplied identifiers
cannot escape their intended root or interact with shell/git refs in
surprising ways.
"""

from pathlib import Path

import pytest

from jig.safe_path import (
    is_safe_filename,
    is_safe_path_segment,
    safe_join,
    safe_resolve_within,
    validate_safe_path_segment,
)


# ---------------------------------------------------------------------------
# is_safe_path_segment — accept cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "segment",
    [
        "tb-001",
        "ticket-007",
        "spike-r-shopify-delta",
        "module1",
        "kebab-case",
        "snake_case",
        "alphanumeric123",
        "a",  # single char
        "0abc",  # leading digit is allowed
        "x" * 100,  # exactly at the cap
    ],
)
def test_safe_path_segment_accepts_kebab_snake_alphanumeric(segment: str) -> None:
    assert is_safe_path_segment(segment) is True


# ---------------------------------------------------------------------------
# is_safe_path_segment — reject cases
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "segment",
    [
        "..",
        "../etc",
        "foo/bar",
        "foo\\bar",
        "/abs",
        "\\abs",
        ".hidden",
        "-leading-dash",
        "UPPERCASE",
        "Mixed-Case",
        "",  # empty
        " ",  # space
        "with space",
        "weird!chars",
        "name@thing",
        "x" * 101,  # over the cap
        "foo.bar",  # dot not allowed in plain segment (use is_safe_filename)
        "_leading_underscore_is_not_a_letter_or_digit",
    ],
)
def test_safe_path_segment_rejects_unsafe_inputs(segment: str) -> None:
    assert is_safe_path_segment(segment) is False


def test_safe_path_segment_rejects_non_str() -> None:
    assert is_safe_path_segment(None) is False  # type: ignore[arg-type]
    assert is_safe_path_segment(123) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# validate_safe_path_segment — error semantics
# ---------------------------------------------------------------------------


def test_validate_returns_segment_unchanged_when_safe() -> None:
    assert validate_safe_path_segment("tb-001", "ticket.id") == "tb-001"


def test_validate_raises_with_field_name_in_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_safe_path_segment("../etc/passwd", "module_id")
    msg = str(exc_info.value)
    assert "module_id" in msg


def test_validate_raises_for_empty_string_with_name() -> None:
    with pytest.raises(ValueError) as exc_info:
        validate_safe_path_segment("", "ticket.id")
    assert "ticket.id" in str(exc_info.value)


# ---------------------------------------------------------------------------
# safe_join — happy path + containment
# ---------------------------------------------------------------------------


def test_safe_join_returns_path_under_root(tmp_path: Path) -> None:
    result = safe_join(tmp_path, "tickets", "tb-001")
    assert result == tmp_path / "tickets" / "tb-001"


def test_safe_join_rejects_unsafe_segment(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_join(tmp_path, "tickets", "../etc")


def test_safe_join_rejects_traversal_via_parent_dir(tmp_path: Path) -> None:
    # Even if a segment looks safe individually, the join must remain
    # under the root after resolve().
    with pytest.raises(ValueError):
        safe_join(tmp_path, "..")


def test_safe_join_rejects_absolute_segment(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_join(tmp_path, "/etc/passwd")


def test_safe_join_rejects_symlink_escape(tmp_path: Path) -> None:
    # Set up: <tmp>/root/, <tmp>/outside/secret.txt, link <root>/escape -> <outside>
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("hi")
    (root / "escape").symlink_to(outside)
    # The segment "escape" is "safe" in shape, but resolves outside root.
    with pytest.raises(ValueError):
        safe_resolve_within(root, "escape/secret.txt")


# ---------------------------------------------------------------------------
# is_safe_filename — accepts dotted filenames in addition to plain segments
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "foo.py",
        "module.contracts.yaml",
        "screen-001.html",
        "readme",
        "tb-001.jsonl",
    ],
)
def test_safe_filename_accepts_dotted_names(name: str) -> None:
    assert is_safe_filename(name) is True


@pytest.mark.parametrize(
    "name",
    [
        "..",
        ".env",  # leading dot still rejected
        "foo/bar.py",
        "..\\..\\foo",
        "foo bar.py",
        "",
    ],
)
def test_safe_filename_rejects_unsafe_names(name: str) -> None:
    assert is_safe_filename(name) is False


# ---------------------------------------------------------------------------
# safe_resolve_within — multi-segment relative paths
# ---------------------------------------------------------------------------


def test_safe_resolve_within_legit_path(tmp_path: Path) -> None:
    target = tmp_path / "src" / "foo" / "bar.py"
    target.parent.mkdir(parents=True)
    target.write_text("x")
    result = safe_resolve_within(tmp_path, "src/foo/bar.py")
    assert result == target.resolve()


def test_safe_resolve_within_rejects_dotdot(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_resolve_within(tmp_path, "src/../../etc/passwd")


def test_safe_resolve_within_rejects_absolute(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_resolve_within(tmp_path, "/etc/passwd")


def test_safe_resolve_within_rejects_empty(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_resolve_within(tmp_path, "")


def test_safe_resolve_within_allows_filename_with_dot_in_last_segment(
    tmp_path: Path,
) -> None:
    target = tmp_path / "tickets" / "tb-001.jsonl"
    target.parent.mkdir()
    target.write_text("[]")
    result = safe_resolve_within(tmp_path, "tickets/tb-001.jsonl")
    assert result == target.resolve()


def test_safe_resolve_within_rejects_dotted_directory_segment(tmp_path: Path) -> None:
    # Only the last segment may contain dots (filenames). Intermediate
    # segments must pass the strict per-segment validator.
    with pytest.raises(ValueError):
        safe_resolve_within(tmp_path, "weird.dir/foo.py")
