"""`jig onboard` CLI command surface."""

import subprocess

from click.testing import CliRunner

from jig.cli import cli


def _git_init(path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)


def test_onboard_help_lists_options():
    runner = CliRunner()
    result = runner.invoke(cli, ["onboard", "--help"])
    assert result.exit_code == 0
    assert "--brief" in result.output
    assert "--force" in result.output
    assert "--profile" in result.output
    assert "desired-state" in result.output


def test_onboard_nonexistent_path_errors():
    runner = CliRunner()
    result = runner.invoke(cli, ["onboard", "/nonexistent-path-xyz"])
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_onboard_non_git_path_errors(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["onboard", str(tmp_path)])
    assert result.exit_code != 0
    assert "not a git repository" in result.output


def test_onboard_repo_subdirectory_errors(tmp_path):
    _git_init(tmp_path)
    subdir = tmp_path / "pkg"
    subdir.mkdir()
    runner = CliRunner()
    result = runner.invoke(cli, ["onboard", str(subdir)])
    assert result.exit_code != 0
    assert "not its root" in result.output


def test_onboard_passes_options_through(tmp_path, monkeypatch):
    _git_init(tmp_path)
    brief = tmp_path / "wish.md"
    brief.write_text("# Desired\n")
    captured = {}

    async def fake_run_onboard(**kwargs):
        captured.update(kwargs)

    import jig.onboard_workflow

    monkeypatch.setattr(jig.onboard_workflow, "run_onboard", fake_run_onboard)
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "onboard",
            str(tmp_path),
            "--brief",
            str(brief),
            "--force",
            "--profile",
            "small",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["path"] == tmp_path
    assert captured["brief_file"] == brief
    assert captured["force"] is True
    assert captured["profile_name"] == "small"


def test_onboard_defaults_to_cwd(tmp_path, monkeypatch):
    captured = {}

    async def fake_run_onboard(**kwargs):
        captured.update(kwargs)

    import jig.onboard_workflow

    monkeypatch.setattr(jig.onboard_workflow, "run_onboard", fake_run_onboard)
    runner = CliRunner()
    with runner.isolated_filesystem(temp_dir=tmp_path) as fs:
        _git_init(fs)
        result = runner.invoke(cli, ["onboard"])
    assert result.exit_code == 0, result.output
    assert str(captured["path"]) == "."
    assert captured["brief_file"] is None
    assert captured["force"] is False
    assert captured["profile_name"] is None
