"""Tests for jig.hooks + jig.project HooksConfig."""

from pathlib import Path

from jig.hooks import (
    HOOK_NAMES,
    HOOK_SCRIPTS,
    SENTINEL_LINE,
    _is_jig_managed,
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
        "project:\n"
        "  id: legacy\n"
        "  name: legacy\n"
        "  path: " + str(tmp_path) + "\n"
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
