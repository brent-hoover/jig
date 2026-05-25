"""Tests for ``jig.agent_config.ensure_agent_config_dir``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jig.agent_config import (
    SANDBOX_CLAUDE_CONFIG_PATH,
    _PLUGIN_KEY,
    _PLUGIN_VERSION,
    ensure_agent_config_dir,
)


@pytest.fixture()
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect Path.home() to a temp dir so tests don't write to ~/.jig."""
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    return tmp_path


@pytest.fixture()
def fake_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect _defaults_dir() to a temp dir so CLAUDE.md tests don't depend on
    whatever happens to be shipped in jig/defaults at the moment."""
    fake = tmp_path / "fake-defaults"
    fake.mkdir()
    monkeypatch.setattr("jig.agent_config._defaults_dir", lambda: fake)
    return fake


class TestEnsureAgentConfigDir:
    def test_creates_settings_json(self, config_home: Path) -> None:
        ensure_agent_config_dir()
        settings = config_home / ".jig" / "claude-agent-config" / "settings.json"
        assert settings.exists()
        assert json.loads(settings.read_text()) == {}

    def test_creates_installed_plugins_json(self, config_home: Path) -> None:
        ensure_agent_config_dir()
        installed = (
            config_home
            / ".jig"
            / "claude-agent-config"
            / "plugins"
            / "installed_plugins.json"
        )
        assert installed.exists()
        data = json.loads(installed.read_text())
        assert data["version"] == 2
        assert _PLUGIN_KEY in data["plugins"]

    def test_all_skills_installed_when_no_filter(self, config_home: Path) -> None:
        ensure_agent_config_dir()
        skills_dir = (
            config_home
            / ".jig"
            / "claude-agent-config"
            / "plugins"
            / "cache"
            / "jig"
            / "jig-skills"
            / _PLUGIN_VERSION
            / "skills"
        )
        skill_names = {p.name for p in skills_dir.iterdir() if p.is_dir()}
        assert "git-conventions" in skill_names
        assert "jig-mcp-tools" in skill_names
        assert "python" in skill_names

    def test_skill_filter_installs_only_listed(self, config_home: Path) -> None:
        ensure_agent_config_dir(skill_names=["git-conventions"])
        skills_dir = (
            config_home
            / ".jig"
            / "claude-agent-config"
            / "plugins"
            / "cache"
            / "jig"
            / "jig-skills"
            / _PLUGIN_VERSION
            / "skills"
        )
        skill_names = {p.name for p in skills_dir.iterdir() if p.is_dir()}
        assert skill_names == {"git-conventions"}

    def test_empty_skill_names_installs_all(self, config_home: Path) -> None:
        ensure_agent_config_dir(skill_names=[])
        skills_dir = (
            config_home
            / ".jig"
            / "claude-agent-config"
            / "plugins"
            / "cache"
            / "jig"
            / "jig-skills"
            / _PLUGIN_VERSION
            / "skills"
        )
        skill_names = {p.name for p in skills_dir.iterdir() if p.is_dir()}
        assert "git-conventions" in skill_names
        assert "python" in skill_names

    def test_sandbox_config_path_used_in_install_path(self, config_home: Path) -> None:
        ensure_agent_config_dir(sandbox_config_path=SANDBOX_CLAUDE_CONFIG_PATH)
        installed = (
            config_home
            / ".jig"
            / "claude-agent-config"
            / "plugins"
            / "installed_plugins.json"
        )
        data = json.loads(installed.read_text())
        entry = data["plugins"][_PLUGIN_KEY][0]
        assert entry["installPath"].startswith(SANDBOX_CLAUDE_CONFIG_PATH)

    def test_host_path_used_in_install_path_by_default(self, config_home: Path) -> None:
        host_dir = ensure_agent_config_dir()
        installed = host_dir / "plugins" / "installed_plugins.json"
        data = json.loads(installed.read_text())
        entry = data["plugins"][_PLUGIN_KEY][0]
        assert entry["installPath"].startswith(str(config_home))

    def test_stale_skill_dirs_removed_on_second_call(self, config_home: Path) -> None:
        ensure_agent_config_dir()
        skills_dir = (
            config_home
            / ".jig"
            / "claude-agent-config"
            / "plugins"
            / "cache"
            / "jig"
            / "jig-skills"
            / _PLUGIN_VERSION
            / "skills"
        )
        assert (skills_dir / "python").exists()
        ensure_agent_config_dir(skill_names=["git-conventions"])
        assert not (skills_dir / "python").exists()
        assert (skills_dir / "git-conventions").exists()

    def test_spawn_dir_name_uses_per_spawn_subdir(self, config_home: Path) -> None:
        result = ensure_agent_config_dir(spawn_dir_name="abc123")
        expected = config_home / ".jig" / "claude-agent-configs" / "abc123"
        assert result == expected
        assert (result / "settings.json").exists()

    def test_spawn_dir_name_isolated_from_shared_path(self, config_home: Path) -> None:
        shared = ensure_agent_config_dir()
        spawn = ensure_agent_config_dir(spawn_dir_name="xyz")
        assert shared != spawn

    def test_returns_host_path(self, config_home: Path) -> None:
        result = ensure_agent_config_dir()
        assert result == config_home / ".jig" / "claude-agent-config"


class TestWriteGlobalClaudeMd:
    def test_writes_shipped_global_when_present(
        self, config_home: Path, fake_defaults: Path
    ) -> None:
        (fake_defaults / "agent_claude_md.md").write_text("# global content\n")
        host = ensure_agent_config_dir()
        assert (host / "CLAUDE.md").read_text() == "# global content\n"

    def test_skipped_when_shipped_global_missing(
        self, config_home: Path, fake_defaults: Path
    ) -> None:
        # No agent_claude_md.md in fake_defaults; should log warning + skip.
        host = ensure_agent_config_dir()
        assert not (host / "CLAUDE.md").exists()

    def test_role_none_writes_global_only(
        self, config_home: Path, fake_defaults: Path
    ) -> None:
        (fake_defaults / "agent_claude_md.md").write_text("# global content\n")
        (fake_defaults / "roles" / "reviewer").mkdir(parents=True)
        (fake_defaults / "roles" / "reviewer" / "CLAUDE.md").write_text("# reviewer\n")
        host = ensure_agent_config_dir(role=None)
        assert (host / "CLAUDE.md").read_text() == "# global content\n"

    def test_role_with_addendum_concats_global_then_role(
        self, config_home: Path, fake_defaults: Path
    ) -> None:
        (fake_defaults / "agent_claude_md.md").write_text("# global content\n")
        (fake_defaults / "roles" / "reviewer").mkdir(parents=True)
        (fake_defaults / "roles" / "reviewer" / "CLAUDE.md").write_text(
            "# reviewer addendum\n"
        )
        host = ensure_agent_config_dir(role="reviewer")
        text = (host / "CLAUDE.md").read_text()
        assert text == "# global content\n\n# reviewer addendum\n"

    def test_role_without_addendum_falls_back_to_global(
        self, config_home: Path, fake_defaults: Path
    ) -> None:
        (fake_defaults / "agent_claude_md.md").write_text("# global content\n")
        # No roles/builder/CLAUDE.md created.
        host = ensure_agent_config_dir(role="builder")
        assert (host / "CLAUDE.md").read_text() == "# global content\n"

    def test_refreshed_on_repeated_calls(
        self, config_home: Path, fake_defaults: Path
    ) -> None:
        (fake_defaults / "agent_claude_md.md").write_text("# v1\n")
        host = ensure_agent_config_dir()
        assert (host / "CLAUDE.md").read_text() == "# v1\n"
        (fake_defaults / "agent_claude_md.md").write_text("# v2\n")
        ensure_agent_config_dir()
        assert (host / "CLAUDE.md").read_text() == "# v2\n"
