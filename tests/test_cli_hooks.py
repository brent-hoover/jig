"""CLI-level tests for `jig hooks ...`."""

import subprocess
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from jig.hooks import HOOK_NAMES, _is_jig_managed


def _init_git(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True
    )


def test_hooks_install_cli(tmp_path: Path):
    _init_git(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["hooks", "install", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "installed pre-commit" in result.output
    for name in HOOK_NAMES:
        assert _is_jig_managed(tmp_path / ".git" / "hooks" / name)


def test_hooks_uninstall_cli(tmp_path: Path):
    _init_git(tmp_path)
    runner = CliRunner()
    runner.invoke(cli, ["hooks", "install", "--path", str(tmp_path)])
    result = runner.invoke(cli, ["hooks", "uninstall", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    for name in HOOK_NAMES:
        assert not (tmp_path / ".git" / "hooks" / name).exists()


def test_hooks_status_cli(tmp_path: Path):
    _init_git(tmp_path)
    runner = CliRunner()
    result = runner.invoke(cli, ["hooks", "status", "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "pre-commit" in result.output
    assert "not installed" in result.output


def test_hooks_install_refuses_without_force_on_backup_collision(tmp_path: Path):
    _init_git(tmp_path)
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "pre-commit").write_text("#!/usr/bin/env bash\necho x\n")
    (hooks_dir / "pre-commit.jig-backup").write_text("#!/usr/bin/env bash\necho old\n")

    runner = CliRunner()
    result = runner.invoke(cli, ["hooks", "install", "--path", str(tmp_path)])
    assert result.exit_code != 0
    assert "backup already exists" in result.output or "backup already exists" in str(
        result.exception
    )
