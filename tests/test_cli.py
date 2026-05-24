"""Tests for jig CLI commands."""

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from jig.cli import cli
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def tmp_new_jig_project(tmp_path: Path) -> Path:
    """A git repo with the NEW .jig/ layout (config.yaml, no issues/, no project.json)."""
    (tmp_path / ".git").mkdir()
    jig_dir = tmp_path / ".jig"
    jig_dir.mkdir()
    config_data = {
        "project": {
            "id": tmp_path.name,
            "name": tmp_path.name,
            "path": str(tmp_path),
            "default_branch": "main",
        }
    }
    (jig_dir / "config.yaml").write_text(yaml.safe_dump(config_data))
    (jig_dir / "roles").mkdir()
    (jig_dir / "workflows").mkdir()
    (jig_dir / "worktrees").mkdir()
    (jig_dir / "store").mkdir()
    return tmp_path


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

        result = runner.invoke(
            cli,
            [
                "start",
                "--path",
                str(tmp_new_jig_project),
                "--ws-port",
                "0",
                "--no-docker",
            ],
        )
        # KeyboardInterrupt exits cleanly
        assert result.exit_code == 0, result.output
        MockOrchestrator.assert_called_once()
        call_kwargs = MockOrchestrator.call_args
        assert call_kwargs.kwargs.get("project_path") == tmp_new_jig_project
        # finally-block guarantees shutdown + stop run even on KeyboardInterrupt
        mock_orch.shutdown.assert_called_once()
        mock_ws.stop.assert_called_once()

    @patch("jig.cli.WebSocketServer")
    @patch("jig.cli.Orchestrator")
    def test_starts_in_unconfigured_mode_when_no_jig_dir(
        self,
        MockOrchestrator: MagicMock,
        MockWsServer: MagicMock,
        runner: CliRunner,
        tmp_path: Path,
    ) -> None:
        """Guard removed: jig start on an uninitialized dir runs in unconfigured mode."""
        (tmp_path / ".git").mkdir()  # git repo but no .jig/
        mock_orch = MockOrchestrator.return_value
        mock_orch.startup = AsyncMock(side_effect=KeyboardInterrupt)
        mock_orch.shutdown = AsyncMock()

        mock_ws = MockWsServer.return_value
        mock_ws.start = AsyncMock()
        mock_ws.stop = AsyncMock()
        mock_ws.port = 0

        result = runner.invoke(
            cli,
            ["start", "--path", str(tmp_path), "--ws-port", "0", "--no-docker"],
        )
        # Exits cleanly — no longer rejected for missing .jig/
        assert result.exit_code == 0, result.output
        MockOrchestrator.assert_called_once()


class TestValidate:
    @pytest.fixture
    def git_jig_project(self, tmp_path: Path) -> Path:
        """A real git repo with .jig/ initialized (new layout)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        (repo / "README.md").write_text("# Test\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True
        )

        from jig.persistence import init_project

        init_project(repo, default_branch="main")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True)
        subprocess.run(
            ["git", "commit", "--no-verify", "-m", "chore: init jig"],
            cwd=repo,
            capture_output=True,
        )
        return repo

    def test_validate_ticket(self, runner: CliRunner, git_jig_project: Path) -> None:
        result = runner.invoke(
            cli,
            [
                "validate",
                "--path",
                str(git_jig_project),
                "--ticket-id",
                "ticket-1",
            ],
        )
        assert result.exit_code == 0
        assert "validated" in result.output.lower()

    def test_not_initialized(self, runner: CliRunner, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        result = runner.invoke(
            cli,
            [
                "validate",
                "--path",
                str(tmp_path),
                "--ticket-id",
                "ticket-1",
            ],
        )
        assert result.exit_code != 0
        assert "not initialized" in result.output.lower()

    def test_dry_run_clean_catalog(
        self, runner: CliRunner, git_jig_project: Path
    ) -> None:
        """`jig validate` with no --ticket-id runs a catalog dry-run."""
        result = runner.invoke(cli, ["validate", "--path", str(git_jig_project)])
        assert result.exit_code == 0, result.output
        assert "catalog ok" in result.output.lower()

    def test_validate_ticket_reports_no_locks_when_none_active(
        self, runner: CliRunner, git_jig_project: Path
    ) -> None:
        """Task M pre-flight: a real feature ticket with no accepted
        handoff shows no locks active."""
        import asyncio

        from jig.store.tickets import TicketStore
        from jig.ticket import Size, Ticket, WorkType

        async def _seed() -> None:
            tickets = TicketStore(git_jig_project / ".jig" / "store" / "tickets.jsonl")
            await tickets.load()
            await tickets.create(
                Ticket(
                    id="t-locks",
                    work_type=WorkType.FEATURE,
                    size=Size.M,
                    title="widget",
                    created_by="alice",
                    description=TICKET_AC_PLACEHOLDER,
                )
            )

        asyncio.run(_seed())

        result = runner.invoke(
            cli,
            [
                "validate",
                "--path",
                str(git_jig_project),
                "--ticket-id",
                "t-locks",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "no section locks active" in result.output.lower()

    def test_validate_ticket_reports_locked_sections(
        self, runner: CliRunner, git_jig_project: Path
    ) -> None:
        """Task M pre-flight: an accepted ``test`` handoff on a
        feature ticket locks ``behaviors`` + ``acceptance_criteria``;
        those surface by name with their locking phase."""
        import asyncio

        from jig.store.threads import ThreadStore
        from jig.store.tickets import TicketStore
        from jig.thread import Handoff
        from jig.ticket import Size, Ticket, WorkType

        async def _seed() -> None:
            tickets = TicketStore(git_jig_project / ".jig" / "store" / "tickets.jsonl")
            await tickets.load()
            await tickets.create(
                Ticket(
                    id="t-locked",
                    work_type=WorkType.FEATURE,
                    size=Size.M,
                    title="widget",
                    created_by="alice",
                    description=TICKET_AC_PLACEHOLDER,
                )
            )
            threads = ThreadStore(git_jig_project / ".jig" / "store" / "comments.jsonl")
            await threads.load()
            await threads.post(
                Handoff(
                    ticket_id="t-locked",
                    author="dev",
                    phase="test",
                    summary="tests done",
                    acceptance_state="accepted",
                    accepted_by="reviewer",
                )
            )

        asyncio.run(_seed())

        result = runner.invoke(
            cli,
            [
                "validate",
                "--path",
                str(git_jig_project),
                "--ticket-id",
                "t-locked",
            ],
        )
        assert result.exit_code == 0, result.output
        out = result.output.lower()
        assert "locked sections" in out
        assert "behaviors" in out
        assert "acceptance_criteria" in out
        assert "test" in out  # locking phase

    def test_dry_run_reports_broken_catalog(
        self, runner: CliRunner, git_jig_project: Path
    ) -> None:
        """A workflow with an unknown role is caught at dry-run."""
        from jig.models import PhaseConfig, WorkflowConfig
        from jig.persistence import save_workflow

        save_workflow(
            git_jig_project,
            WorkflowConfig(
                name="broken",
                phases=[PhaseConfig(name="x", role="no-such-role")],
            ),
        )
        result = runner.invoke(cli, ["validate", "--path", str(git_jig_project)])
        assert result.exit_code != 0
        assert "no-such-role" in result.output


class TestCreate:
    def test_create_makes_dir_and_git_init(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ) -> None:
        """`jig create <name>` creates the dir, runs git init, then would
        execvp. We patch execvp + chdir to avoid replacing the test process."""
        execvp_calls: list[tuple] = []
        chdir_calls: list[str] = []
        monkeypatch.setattr("jig.cli._execvp", lambda f, a: execvp_calls.append((f, a)))
        monkeypatch.setattr("jig.cli._chdir", lambda p: chdir_calls.append(str(p)))
        monkeypatch.chdir(tmp_path)

        result = runner.invoke(cli, ["create", "newproj"])
        assert result.exit_code == 0, result.output
        target = tmp_path / "newproj"
        assert target.is_dir()
        assert (target / ".git").is_dir(), "git init didn't create .git/"
        # cli passes the relative target Path to chdir; we just verify it
        # would chdir into the new project (basename match is enough).
        assert len(chdir_calls) == 1
        assert Path(chdir_calls[0]).name == "newproj"
        assert len(execvp_calls) == 1

    def test_create_refuses_when_dir_exists(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        (tmp_path / "existing").mkdir()
        result = runner.invoke(cli, ["create", "existing"])
        assert result.exit_code != 0
        assert "already exists" in result.output

    def test_create_no_git_skips_git_init(
        self, runner: CliRunner, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr("jig.cli._execvp", lambda *a, **kw: None)
        monkeypatch.setattr("jig.cli._chdir", lambda *a, **kw: None)
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(cli, ["create", "skipgit", "--no-git"])
        assert result.exit_code == 0, result.output
        assert (tmp_path / "skipgit").is_dir()
        assert not (tmp_path / "skipgit" / ".git").exists()
