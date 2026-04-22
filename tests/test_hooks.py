"""Tests for jig.hooks + jig.project HooksConfig."""

import subprocess
from pathlib import Path

import pytest

from jig.hooks import (
    HOOK_NAMES,
    HOOK_SCRIPTS,
    SENTINEL_LINE,
    _git_common_dir,
    _is_jig_managed,
    _resolve_ticket_worktree,
)
from jig.project import HooksConfig, Project, load_project, save_project


def test_hooks_config_defaults():
    cfg = HooksConfig()
    assert cfg.pre_push_command is None


def test_project_has_hooks_field_with_default():
    project = Project(id="p1", name="p1", path="/tmp/p1")
    assert isinstance(project.hooks, HooksConfig)
    assert project.hooks.pre_push_command is None


def test_project_roundtrip_with_hooks(tmp_path: Path):
    (tmp_path / ".jig").mkdir()
    project = Project(
        id="p1",
        name="p1",
        path=str(tmp_path),
        hooks=HooksConfig(pre_push_command="uv run pytest -q"),
    )
    save_project(tmp_path, project)
    loaded = load_project(tmp_path)
    assert loaded.hooks.pre_push_command == "uv run pytest -q"


def test_project_legacy_config_without_hooks_block_loads_cleanly(tmp_path: Path):
    """Existing .jig/config.yaml files with no 'hooks:' section must keep working."""
    (tmp_path / ".jig").mkdir()
    (tmp_path / ".jig" / "config.yaml").write_text(
        "project:\n  id: legacy\n  name: legacy\n  path: " + str(tmp_path) + "\n"
    )
    loaded = load_project(tmp_path)
    assert isinstance(loaded.hooks, HooksConfig)
    assert loaded.hooks.pre_push_command is None


def test_hook_names_is_canonical_triple():
    assert HOOK_NAMES == ("pre-commit", "pre-push", "commit-msg")


def test_hook_scripts_cover_every_hook():
    for name in HOOK_NAMES:
        assert name in HOOK_SCRIPTS
        script = HOOK_SCRIPTS[name]
        assert script.startswith("#!/usr/bin/env bash")
        # Sentinel must be byte-exact on line 2.
        assert script.splitlines()[1] == SENTINEL_LINE
        # Every script forwards to `jig hooks run <name>`.
        assert f"jig hooks run {name}" in script


def test_is_jig_managed_detects_sentinel(tmp_path: Path):
    managed = tmp_path / "pre-commit"
    managed.write_text(f"#!/usr/bin/env bash\n{SENTINEL_LINE}\nexit 0\n")
    assert _is_jig_managed(managed) is True


def test_is_jig_managed_rejects_foreign_hook(tmp_path: Path):
    foreign = tmp_path / "pre-commit"
    foreign.write_text("#!/usr/bin/env bash\necho hi\nexit 0\n")
    assert _is_jig_managed(foreign) is False


def test_is_jig_managed_rejects_missing_file(tmp_path: Path):
    assert _is_jig_managed(tmp_path / "missing") is False


def test_is_jig_managed_rejects_short_file(tmp_path: Path):
    short = tmp_path / "pre-commit"
    short.write_text("#!/usr/bin/env bash\n")
    assert _is_jig_managed(short) is False


def test_is_jig_managed_follows_symlink(tmp_path: Path):
    real = tmp_path / "real"
    real.write_text("#!/usr/bin/env bash\necho foreign\n")
    link = tmp_path / "pre-commit"
    link.symlink_to(real)
    assert _is_jig_managed(link) is False


def test_is_jig_managed_rejects_empty_file(tmp_path: Path):
    empty = tmp_path / "pre-commit"
    empty.touch()
    assert _is_jig_managed(empty) is False


def test_is_jig_managed_rejects_binary_file(tmp_path: Path):
    binary = tmp_path / "pre-commit"
    binary.write_bytes(b"\x7fELF\x02\x01\x01\x00\x00\x00" * 10)
    assert _is_jig_managed(binary) is False


def _git_init(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True
    )


def test_git_common_dir_in_main_repo(tmp_path: Path):
    _git_init(tmp_path)
    common = _git_common_dir(tmp_path)
    assert common == (tmp_path / ".git").resolve()


def test_git_common_dir_raises_outside_repo(tmp_path: Path):
    with pytest.raises(RuntimeError, match="not inside a git repository"):
        _git_common_dir(tmp_path)


def test_resolve_ticket_worktree_detects_jig_layout(tmp_path: Path):
    _git_init(tmp_path)
    # Simulate jig's layout: <project>/.jig/worktrees/<ticket_id>/
    wt = tmp_path / ".jig" / "worktrees" / "t-123"
    wt.mkdir(parents=True)
    # Real jig worktrees have a .git FILE (not dir) — mimic that.
    (wt / ".git").write_text("gitdir: ../../../.git/worktrees/t-123\n")
    ticket_id = _resolve_ticket_worktree(wt)
    assert ticket_id == "t-123"


def test_resolve_ticket_worktree_returns_none_for_main_repo(tmp_path: Path):
    _git_init(tmp_path)
    assert _resolve_ticket_worktree(tmp_path) is None


def test_resolve_ticket_worktree_false_positive_guard(tmp_path: Path):
    """A dir literally named 'worktrees' without the .jig parent must not match."""
    _git_init(tmp_path)
    sneaky = tmp_path / "worktrees" / "t-fake"
    sneaky.mkdir(parents=True)
    assert _resolve_ticket_worktree(sneaky) is None
