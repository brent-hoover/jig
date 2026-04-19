"""Tests for jig CLI commands."""

import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.persistence import list_roles, load_workflow


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_new_jig_project(tmp_path: Path) -> Path:
    """A git repo with the NEW .jig/ layout (project.json, no issues/, no config.yaml)."""
    (tmp_path / ".git").mkdir()
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir()
    project_data = {
        "id": tmp_path.name,
        "name": tmp_path.name,
        "path": str(tmp_path),
        "default_branch": "main",
    }
    (jig_dir / "project.json").write_text(json.dumps(project_data))
    (jig_dir / "roles").mkdir()
    (jig_dir / "workflows").mkdir()
    (jig_dir / "worktrees").mkdir()
    (jig_dir / "store").mkdir()
    return tmp_path


class TestInit:
    def test_init_creates_project_json(self, tmp_path: Path, runner: CliRunner) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=tmp_path, check=True,
        )
        result = runner.invoke(cli, ["init", "--path", str(tmp_path), "--no-input"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".jig" / "project.json").is_file()
        assert not (tmp_path / ".jig" / "issues").exists()
        assert (tmp_path / ".jig" / "worktrees").is_dir()
        assert (tmp_path / ".jig" / "roles").is_dir()
        assert (tmp_path / ".jig" / "workflows").is_dir()
        assert (tmp_path / ".jig" / "store").is_dir()

    def test_init_project_json_contains_branch(self, tmp_path: Path, runner: CliRunner) -> None:
        subprocess.run(["git", "init", "-q", "-b", "develop"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=tmp_path, check=True,
        )
        result = runner.invoke(
            cli, ["init", "--path", str(tmp_path), "--branch", "develop", "--no-input"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads((tmp_path / ".jig" / "project.json").read_text())
        assert data["default_branch"] == "develop"

    def test_already_initialized(self, runner: CliRunner, tmp_new_jig_project: Path) -> None:
        result = runner.invoke(cli, ["init", "--path", str(tmp_new_jig_project)])
        assert result.exit_code != 0
        assert "already" in result.output.lower()

    def test_not_git_repo_no_input_errors(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(cli, ["init", "--path", str(tmp_path), "--no-input"])
        assert result.exit_code != 0
        assert "git" in result.output.lower()

    def test_not_git_repo_prompt_accept_creates_repo(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        # "y" to confirm git init, then blank lines to accept project-context defaults
        result = runner.invoke(
            cli, ["init", "--path", str(tmp_path)], input="y\n" + "\n" * 20
        )
        assert result.exit_code == 0, result.output
        assert (tmp_path / ".git").is_dir()
        assert (tmp_path / ".jig").is_dir()
        assert "Initialized empty git repository" in result.output

    def test_not_git_repo_prompt_decline_aborts(
        self, runner: CliRunner, tmp_path: Path
    ) -> None:
        result = runner.invoke(cli, ["init", "--path", str(tmp_path)], input="n\n")
        assert result.exit_code != 0
        assert not (tmp_path / ".jig").exists()
        assert "Aborted" in result.output


class TestInitCreatesAgentTypes:
    def test_init_creates_default_roles(self, runner: CliRunner, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=tmp_path, check=True,
        )
        result = runner.invoke(cli, ["init", "--path", str(tmp_path), "--no-input"])
        assert result.exit_code == 0, result.output
        types = list_roles(tmp_path)
        roles = {t.role for t in types}
        assert roles == {"spec", "test", "dev", "review", "validate", "document", "pm"}


class TestInitCreatesWorkflow:
    def test_init_creates_default_workflow(self, runner: CliRunner, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "init"],
            cwd=tmp_path, check=True,
        )
        result = runner.invoke(cli, ["init", "--path", str(tmp_path), "--no-input"])
        assert result.exit_code == 0, result.output
        workflow = load_workflow(tmp_path, "default")
        assert workflow.name == "default"
        assert len(workflow.phases) == 6


class TestStart:
    @patch("jig.cli.WebSocketServer")
    @patch("jig.cli.Orchestrator")
    def test_starts_orchestrator(
        self,
        MockOrchestrator: MagicMock,
        MockWsServer: MagicMock,
        runner: CliRunner,
        tmp_new_jig_project: Path,
    ) -> None:
        mock_orch = MockOrchestrator.return_value
        mock_orch.startup = AsyncMock()
        mock_orch.shutdown = AsyncMock()
        # Make startup raise KeyboardInterrupt so the loop exits
        mock_orch.startup.side_effect = KeyboardInterrupt

        mock_ws = MockWsServer.return_value
        mock_ws.start = AsyncMock()
        mock_ws.stop = AsyncMock()
        mock_ws.port = 0

        result = runner.invoke(cli, [
            "start", "--path", str(tmp_new_jig_project), "--ws-port", "0",
        ])
        # KeyboardInterrupt exits cleanly
        assert result.exit_code == 0, result.output
        MockOrchestrator.assert_called_once()
        call_kwargs = MockOrchestrator.call_args
        assert call_kwargs.kwargs.get("project_path") == tmp_new_jig_project
        # finally-block guarantees shutdown + stop run even on KeyboardInterrupt
        mock_orch.shutdown.assert_called_once()
        mock_ws.stop.assert_called_once()

    def test_not_initialized(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()  # git repo but no .jig/
        result = runner.invoke(cli, ["start", "--path", str(tmp_path)])
        assert result.exit_code != 0
        assert "not initialized" in result.output.lower()


class TestValidate:
    @pytest.fixture
    def git_jig_project(self, tmp_path: Path) -> Path:
        """A real git repo with .jig/ initialized (new layout)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=repo, check=True, capture_output=True
        )
        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
        )

        result = CliRunner().invoke(cli, ["init", "--path", str(repo), "--no-input"])
        assert result.exit_code == 0, result.output
        # jig init creates its own commit; nothing further to stage.
        return repo

    def test_validate_ticket(self, runner: CliRunner, git_jig_project: Path) -> None:
        result = runner.invoke(cli, [
            "validate", "--path", str(git_jig_project), "--ticket-id", "ticket-1",
        ])
        assert result.exit_code == 0
        assert "validated" in result.output.lower()

    def test_not_initialized(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(cli, [
            "validate", "--path", str(tmp_path), "--ticket-id", "ticket-1",
        ])
        assert result.exit_code != 0
        assert "not initialized" in result.output.lower()
