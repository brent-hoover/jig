import pytest
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


class TestInit:
    def test_initializes_project(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_project)])
        assert result.exit_code == 0
        assert (tmp_project / ".jig").is_dir()
        assert "Initialized" in result.output

    def test_custom_branch(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(
            cli, ["init", "--path", str(tmp_project), "--branch", "develop"]
        )
        assert result.exit_code == 0
        config_text = (tmp_project / ".jig" / "config.yaml").read_text()
        assert "develop" in config_text

    def test_already_initialized(self, runner: CliRunner, tmp_jig_project: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_jig_project)])
        assert result.exit_code != 0
        assert "already" in result.output.lower()

    def test_not_git_repo(self, runner: CliRunner, tmp_path: Path):
        result = runner.invoke(cli, ["init", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "git" in result.output.lower()


from jig.models import Issue, IssueStatus
from jig.persistence import save_issue


class TestStatus:
    def test_no_issues(self, runner: CliRunner, tmp_jig_project: Path):
        result = runner.invoke(cli, ["status", "--path", str(tmp_jig_project)])
        assert result.exit_code == 0
        assert "No issues" in result.output

    def test_with_issues(self, runner: CliRunner, tmp_jig_project: Path):
        save_issue(tmp_jig_project, Issue(id="issue-1", title="Add auth"))
        save_issue(
            tmp_jig_project,
            Issue(
                id="issue-2",
                title="Fix bug",
                status=IssueStatus.IN_PROGRESS,
                current_phase="implement",
            ),
        )
        result = runner.invoke(cli, ["status", "--path", str(tmp_jig_project)])
        assert result.exit_code == 0
        assert "issue-1" in result.output
        assert "Add auth" in result.output
        assert "pending" in result.output
        assert "issue-2" in result.output
        assert "Fix bug" in result.output
        assert "in_progress" in result.output
        assert "implement" in result.output

    def test_not_initialized(self, runner: CliRunner, tmp_project: Path):
        result = runner.invoke(cli, ["status", "--path", str(tmp_project)])
        assert result.exit_code != 0
        assert "not initialized" in result.output.lower()
