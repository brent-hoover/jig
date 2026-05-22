"""End-to-end tests for project profiles.

Exercise the full flag → config → workflow-resolution path for each
shipping profile. These complement the unit tests in
``test_profile_loader.py`` by pinning the integration: that applying
a profile actually changes what ``resolve_workflow`` returns.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.cli import _apply_profile_at_start
from jig.config import load_config, resolve_workflow
from jig.init_workflow import _resolve_sa_role, create_stub
from jig.profile_loader import load_profile


@pytest.fixture
def fresh_project(tmp_path: Path) -> Path:
    """Stub-init a project at ``tmp_path/proj`` for a profile test."""
    project = tmp_path / "proj"
    create_stub(project, name="proj")
    return project


def test_small_profile_via_flag(fresh_project: Path) -> None:
    """``jig start --profile small`` writes the small profile end-to-end:
    config field, SA role lookup, workflow routing, template copy."""
    _apply_profile_at_start(fresh_project, "small")

    # 1. Config records the profile.
    cfg = load_config(fresh_project)
    assert cfg.profile.name == "small"
    assert cfg.profile.sa_role == "sa"

    # 2. SA role lookup reads from the profile.
    assert _resolve_sa_role(fresh_project) == "sa"

    # 3. Workflow routing: s → feature-s (the lightweight one).
    assert resolve_workflow(cfg, work_type="feature", size="s") == "feature-s"
    assert resolve_workflow(cfg, work_type="feature", size="xs") == "feature-xs"

    # 4. Profile + referenced workflows copied into .jig/.
    assert (fresh_project / ".jig" / "profiles" / "small.yaml").is_file()
    assert (fresh_project / ".jig" / "workflows" / "feature-s.yaml").is_file()
    assert (fresh_project / ".jig" / "workflows" / "feature-xs.yaml").is_file()


def test_medium_profile_via_flag(fresh_project: Path) -> None:
    """``jig start --profile medium`` writes the medium profile: sa_mvp +
    feature-s-full at s-size (the distinguishing routing decision)."""
    _apply_profile_at_start(fresh_project, "medium")

    cfg = load_config(fresh_project)
    assert cfg.profile.name == "medium"
    assert cfg.profile.sa_role == "sa_mvp"
    assert _resolve_sa_role(fresh_project) == "sa_mvp"

    # The s-size routing is the key difference vs small:
    # medium runs the full federation at s, small runs generalist only.
    assert resolve_workflow(cfg, work_type="feature", size="s") == "feature-s-full"
    assert resolve_workflow(cfg, work_type="feature", size="m") == "default"

    # The new feature-s-full workflow YAML was copied.
    assert (fresh_project / ".jig" / "workflows" / "feature-s-full.yaml").is_file()


def test_unknown_profile_raises_friendly_error(fresh_project: Path) -> None:
    """``--profile xxx`` for an unknown name surfaces a ClickException so
    the operator sees a readable CLI message, not a Pydantic traceback."""
    import click

    with pytest.raises(click.ClickException, match="profile 'large' not found"):
        _apply_profile_at_start(fresh_project, "large")


def test_profile_apply_is_idempotent(fresh_project: Path) -> None:
    """Re-running with the same profile preserves operator edits to copied
    workflow files — the copy is one-way (defaults → .jig/) and stops at
    existing files."""
    _apply_profile_at_start(fresh_project, "small")
    edited = fresh_project / ".jig" / "workflows" / "feature-s.yaml"
    edited.write_text("name: feature-s\nphases: []\n# operator-edited\n")
    _apply_profile_at_start(fresh_project, "small")
    assert "operator-edited" in edited.read_text()


def test_resolve_sa_role_falls_back_when_profile_unset(tmp_path: Path) -> None:
    """Legacy projects (no profile applied) keep the historical sa role."""
    project = tmp_path / "legacy"
    create_stub(project, name="legacy")
    assert _resolve_sa_role(project) == "sa"


def test_apply_profile_helper_consistent_with_flag(fresh_project: Path) -> None:
    """Sanity: calling apply_profile + save_config directly produces the
    same on-disk state as the flag path. Confirms there's no hidden
    flag-only side effect."""
    from jig.config import save_config
    from jig.profile_loader import apply_profile, copy_profile_templates

    profile = load_profile("medium")
    cfg = load_config(fresh_project)
    cfg = apply_profile(cfg, profile)
    save_config(fresh_project, cfg)
    copy_profile_templates(profile, fresh_project)

    reloaded = load_config(fresh_project)
    assert reloaded.profile.name == "medium"
    assert resolve_workflow(reloaded, work_type="feature", size="s") == "feature-s-full"
