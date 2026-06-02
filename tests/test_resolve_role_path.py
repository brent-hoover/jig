"""Tests for ``resolve_role_path`` — must mirror ``load_role`` semantics.

The hyphenated-id-with-underscored-filename pattern (``reviewer-test-adequacy``
→ ``reviewer_test_adequacy.yaml``) is the load_role fallback path that
real callers actually hit. resolve_role_path is a public role-resolver
helper and would mislead callers if it returned ``None`` for inputs that
``load_role`` successfully resolves.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.persistence import resolve_role_path


@pytest.mark.parametrize(
    "role_id",
    [
        "reviewer-test-adequacy",
        "reviewer-pattern-conformance",
        "reviewer-error-handling",
    ],
)
def test_resolve_role_path_via_filename_stem(tmp_path: Path, role_id: str) -> None:
    """Filename-stem lookup succeeds for the hyphen→underscore aliases —
    the shipped files exist at their underscored names so the direct
    filename branch hits before the role-field fallback."""
    # Snapshot recording uses the underscored stem.
    path = resolve_role_path(tmp_path, role_id.replace("-", "_"))
    assert path is not None
    assert path.is_file()


def test_resolve_role_path_via_role_field_fallback(tmp_path: Path) -> None:
    """Passing the *hyphenated* id (not the filename stem) must still
    resolve via the role-field fallback, matching ``load_role``."""
    path = resolve_role_path(tmp_path, "reviewer-test-adequacy")
    assert path is not None
    assert path.is_file()
    # Sanity: the file's role: field matches what we asked for.
    import yaml

    data = yaml.safe_load(path.read_text())
    assert data["role"] == "reviewer-test-adequacy"


def test_resolve_role_path_unknown_returns_none(tmp_path: Path) -> None:
    """Unknown role id returns None — never raises."""
    assert resolve_role_path(tmp_path, "definitely-not-a-real-role") is None


def test_resolve_role_path_project_override_wins(tmp_path: Path) -> None:
    """A project override at ``.jig/roles/<hyphenated-id>.yaml`` MUST beat
    the shipped default, even though the shipped file uses an underscored
    filename. Without this, role_versions in QualitySnapshot would silently
    record the shipped hash for projects that customised the reviewer.

    ``save_role`` writes overrides using ``config.role`` (hyphenated) as
    the filename, so the canonical id is the right lookup key.
    """
    project_roles = tmp_path / ".jig" / "roles"
    project_roles.mkdir(parents=True)
    override = project_roles / "reviewer-security.yaml"
    override.write_text("role: reviewer-security\ndev_tier: customised\n")

    path = resolve_role_path(tmp_path, "reviewer-security")
    assert path is not None
    assert path == override, f"expected project override, got {path}"
