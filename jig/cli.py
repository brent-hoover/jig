"""Jig CLI."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import click

from jig.events import EventEmitter
from jig.project import Project, save_project
from jig.ws_server import WebSocketServer
from jig.orchestrator import Orchestrator
from jig.persistence import (
    init_project,
    save_default_agent_types,
    save_default_workflow,
)
from jig.worktree import remove_worktree


@click.group()
def cli() -> None:
    """Jig: Agent harness for Claude Code."""


def _detect_branch(path: Path) -> str:
    """Detect the current git branch, falling back to 'main'."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        try:
            ref = subprocess.check_output(
                ["git", "symbolic-ref", "HEAD"],
                cwd=path, text=True, stderr=subprocess.DEVNULL,
            ).strip()
            return ref.removeprefix("refs/heads/")
        except subprocess.CalledProcessError:
            return "main"


def _prompt_project_context(path: Path, existing: Project) -> Project:
    """Interactively gather project fields."""
    click.echo("Project context. (Press Enter to skip/keep current value)\n")

    name = click.prompt("Project name", default=existing.name or path.resolve().name)
    description = click.prompt("Short description", default=existing.description)
    language = click.prompt("Primary language", default=existing.language)
    framework = click.prompt("Framework", default=existing.framework)
    pkg_mgr = click.prompt("Package manager", default=existing.package_manager)
    build_cmd = click.prompt("Build command", default=existing.build_command)
    test_cmd = click.prompt("Test command", default=existing.test_command)

    return Project(
        id=existing.id,
        name=name,
        path=str(path.resolve()),
        default_branch=existing.default_branch,
        description=description,
        language=language,
        framework=framework,
        package_manager=pkg_mgr,
        build_command=build_cmd,
        test_command=test_cmd,
    )


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--branch", default=None, help="Default branch name (auto-detected from current branch).")
@click.option("--no-input", is_flag=True, help="Skip interactive prompts.")
def init(path: Path, branch: str | None, no_input: bool) -> None:
    """Initialize .jig/ in a project."""
    if not (path / ".git").is_dir():
        if no_input:
            raise click.ClickException(
                f"{path} is not a git repository. Run 'git init' first, or omit --no-input to be prompted."
            )
        if not click.confirm(
            f"{path} is not a git repository. Initialize one?", default=True
        ):
            raise click.ClickException("Aborted: jig requires a git repository.")
        init_branch = branch or "main"
        result = subprocess.run(
            ["git", "init", "-b", init_branch], cwd=path, capture_output=True, text=True
        )
        if result.returncode != 0:
            raise click.ClickException(f"git init failed: {result.stderr.strip()}")
        click.echo(f"Initialized empty git repository in {path} (branch: {init_branch})")

    if branch is None:
        branch = _detect_branch(path)

    try:
        init_project(path, default_branch=branch)
        save_default_agent_types(path)
        save_default_workflow(path)
    except FileExistsError:
        raise click.ClickException(f"Already initialized: {path / '.jig'}")
    except ValueError as e:
        raise click.ClickException(str(e))

    click.echo(f"Initialized Jig in {path / '.jig'} (branch: {branch})")

    project_name = path.resolve().name
    project_id = project_name

    if no_input:
        save_project(path, Project(
            id=project_id,
            name=project_name,
            path=str(path.resolve()),
            default_branch=branch,
        ))
        return

    click.echo()

    project = _prompt_project_context(
        path,
        Project(id=project_id, name=project_name, path=str(path.resolve()), default_branch=branch),
    )
    save_project(path, project)
    click.echo("\nProject saved to .jig/project.json")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--ws-port", default=9100, type=int, help="WebSocket server port.", show_default=True)
def start(path: Path, ws_port: int) -> None:
    """Start the Jig orchestrator daemon."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    async def run_daemon() -> None:
        emitter = EventEmitter()
        ws_server = WebSocketServer(emitter, port=ws_port)
        await ws_server.start()
        click.echo(f"WebSocket server listening on ws://127.0.0.1:{ws_server.port}")

        orchestrator = Orchestrator(project_path=path, emitter=emitter)
        try:
            await orchestrator.startup()
            click.echo("Orchestrator started. Press Ctrl-C to stop.")
            # Run until cancelled
            await asyncio.get_event_loop().create_future()
        except KeyboardInterrupt:
            pass
        finally:
            await orchestrator.shutdown()
            await ws_server.stop()

    try:
        asyncio.run(run_daemon())
        click.echo("Orchestrator stopped.")
    except KeyboardInterrupt:
        click.echo("\nOrchestrator stopped.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--ticket-id", required=True, help="Ticket to validate.")
def validate(path: Path, ticket_id: str) -> None:
    """Validate a ticket and clean up its worktree."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    worktree_path = jig_dir / "worktrees" / ticket_id
    if worktree_path.is_dir():
        try:
            asyncio.run(remove_worktree(path, ticket_id))
            click.echo(f"  Removed worktree: {ticket_id}")
        except RuntimeError:
            click.echo(f"  Warning: could not remove worktree {ticket_id}")

    click.echo(f"Ticket {ticket_id} validated.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.confirmation_option(prompt="This will delete .jig/ and reinitialize git. Continue?")
def reset(path: Path) -> None:
    """Reset project to a clean state for testing."""
    jig_dir = path / ".jig"

    # Remove git worktrees before deleting .git
    if (path / ".git").is_dir():
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=path, capture_output=True, text=True,
            )
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    wt = line.removeprefix("worktree ")
                    if wt == str(path.resolve()):
                        continue
                    click.echo(f"  Removing worktree: {wt}")
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", wt],
                        cwd=path, capture_output=True,
                    )
        except Exception:
            pass

    # Detect current branch before removing .git
    branch = "develop"
    try:
        ref = subprocess.check_output(
            ["git", "symbolic-ref", "HEAD"],
            cwd=path, text=True, stderr=subprocess.DEVNULL,
        ).strip()
        branch = ref.removeprefix("refs/heads/")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Remove all files and directories except .jig (removed next) and .git (removed after)
    click.echo("  Cleaning project files")
    for item in path.iterdir():
        if item.name in (".git", ".jig"):
            continue
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    if jig_dir.is_dir():
        click.echo("  Removing .jig/")
        shutil.rmtree(jig_dir)

    git_dir = path / ".git"
    if git_dir.is_dir():
        click.echo("  Removing .git/")
        shutil.rmtree(git_dir)

    click.echo(f"  Initializing fresh git repo (branch: {branch})")
    subprocess.run(["git", "init", "-b", branch], cwd=path, check=True)

    click.echo("Done. Run 'jig init' to set up the project.")
