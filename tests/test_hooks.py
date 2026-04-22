"""Tests for jig.hooks + jig.project HooksConfig."""

from pathlib import Path

import pytest

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
