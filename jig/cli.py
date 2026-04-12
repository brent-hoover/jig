"""Jig CLI."""

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

import click

from jig.events import EventEmitter
from jig.models import MergeStrategy
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


_MERGE_STRATEGIES = [s.value for s in MergeStrategy]


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
    merge = click.prompt(
        f"Merge strategy ({', '.join(_MERGE_STRATEGIES)})",
        default=existing.merge_strategy.value,
        type=click.Choice(_MERGE_STRATEGIES, case_sensitive=False),
        show_choices=False,
    )

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
        merge_strategy=MergeStrategy(merge),
    )


_TEMPLATE_DEFAULTS: dict[str, dict[str, str]] = {
    "python": {
        "language": "python",
        "framework": "",
        "package_manager": "uv",
        "test_command": "uv run pytest",
        "build_command": "uv build",
    },
    "fastapi": {
        "language": "python",
        "framework": "fastapi",
        "package_manager": "uv",
        "test_command": "uv run pytest",
        "build_command": "uv build",
    },
}


def _available_templates() -> list[str]:
    """Return names of bundled project templates."""
    tpl_root = Path(__file__).resolve().parent.parent / "templates"
    if not tpl_root.is_dir():
        return []
    return sorted(d.name for d in tpl_root.iterdir() if d.is_dir())


def _apply_template(template_name: str, dest: Path, project_name: str) -> None:
    """Copy a project template into dest, replacing 'myproject' with project_name."""
    tpl_root = Path(__file__).resolve().parent.parent / "templates"
    tpl_dir = tpl_root / template_name
    if not tpl_dir.is_dir():
        available = _available_templates()
        raise click.ClickException(
            f"Unknown template {template_name!r}. Available: {', '.join(available) or 'none'}"
        )

    # Sanitize project name for use as a Python package name
    pkg_name = project_name.replace("-", "_").replace(" ", "_").lower()
    skip_dirs = {"__pycache__", ".ruff_cache", ".mypy_cache", ".pytest_cache", ".venv", "node_modules"}

    for src_file in tpl_dir.rglob("*"):
        if not src_file.is_file():
            continue
        if skip_dirs & set(src_file.relative_to(tpl_dir).parts):
            continue
        rel = src_file.relative_to(tpl_dir)
        # Rename paths containing "myproject" to the actual package name
        dest_rel = Path(str(rel).replace("myproject", pkg_name))
        dest_file = dest / dest_rel
        dest_file.parent.mkdir(parents=True, exist_ok=True)
        content = src_file.read_bytes()
        # Replace placeholder in text files
        try:
            text = content.decode()
            text = text.replace("myproject", pkg_name)
            dest_file.write_text(text)
        except UnicodeDecodeError:
            dest_file.write_bytes(content)


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--branch", default=None, help="Default branch name (auto-detected from current branch).")
@click.option("--template", "template_name", default=None, help="Project template (python, fastapi).")
@click.option("--no-input", is_flag=True, help="Skip interactive prompts.")
def init(path: Path, branch: str | None, template_name: str | None, no_input: bool) -> None:
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

    # Apply project template if specified (or prompt for one)
    if template_name is None and not no_input:
        available = _available_templates()
        if available:
            choice = click.prompt(
                f"Project template ({', '.join(available)}, or blank to skip)",
                default="",
            )
            if choice.strip():
                template_name = choice.strip()

    if template_name:
        _apply_template(template_name, path, project_name)
        click.echo(f"Applied template: {template_name}")
    project_id = project_name
    tpl_defaults = _TEMPLATE_DEFAULTS.get(template_name or "", {})

    base_project = Project(
        id=project_id,
        name=project_name,
        path=str(path.resolve()),
        default_branch=branch,
        **tpl_defaults,
    )

    if no_input:
        save_project(path, base_project)
    else:
        click.echo()
        project = _prompt_project_context(path, base_project)
        save_project(path, project)
        click.echo("\nProject saved to .jig/project.json")

    # Commit everything so worktrees branch from a working state
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initialize jig project"],
        cwd=path, capture_output=True,
    )
    click.echo("Initial commit created.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--ws-port", default=9100, type=int, help="WebSocket server port.", show_default=True)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose (DEBUG) logging.")
def start(path: Path, ws_port: int, verbose: bool) -> None:
    """Start the Jig orchestrator daemon."""
    level = logging.DEBUG if verbose else logging.INFO

    console_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(console_fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console)

    # Quiet noisy third-party loggers — frame-level WS debug is never useful
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)

    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    from datetime import datetime
    log_dir = jig_dir / "logs"
    log_dir.mkdir(exist_ok=True)
    log_file = log_dir / f"jig-{datetime.now():%Y%m%d-%H%M%S}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    root.addHandler(file_handler)
    click.echo(f"Logging to {log_file}")

    async def run_daemon() -> None:
        emitter = EventEmitter()
        orchestrator = Orchestrator(project_path=path, emitter=emitter)
        ws_server = WebSocketServer(emitter, port=ws_port, orchestrator=orchestrator)
        await ws_server.start()
        click.echo(f"WebSocket server listening on ws://127.0.0.1:{ws_server.port}")
        try:
            await orchestrator.startup()
            click.echo("Orchestrator started. Press Ctrl-C to stop.")
            # Run until cancelled (Ctrl-C triggers CancelledError via asyncio.run).
            await asyncio.get_running_loop().create_future()
        finally:
            await orchestrator.shutdown()
            await ws_server.stop()

    try:
        asyncio.run(run_daemon())
    except KeyboardInterrupt:
        pass
    click.echo("Orchestrator stopped.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def sync(path: Path) -> None:
    """Sync default agent types and workflows from the installed jig version.

    Copies any new defaults without overwriting existing customizations.
    """
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    from jig.persistence import _defaults_dir

    added: list[str] = []

    # Sync agent types
    source_agents = _defaults_dir() / "agent_types"
    dest_agents = jig_dir / "agent_types"
    for src in sorted(source_agents.glob("*.yaml")):
        dest = dest_agents / src.name
        if not dest.exists():
            dest.write_text(src.read_text())
            added.append(f"agent_types/{src.name}")

    # Sync workflows
    source_wf = _defaults_dir() / "workflows"
    dest_wf = jig_dir / "workflows"
    for src in sorted(source_wf.glob("*.yaml")):
        dest = dest_wf / src.name
        if not dest.exists():
            dest.write_text(src.read_text())
            added.append(f"workflows/{src.name}")

    if added:
        for name in added:
            click.echo(f"  Added {name}")
        click.echo(f"Synced {len(added)} new defaults.")
    else:
        click.echo("Already up to date.")


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
        except RuntimeError as e:
            raise click.ClickException(
                f"Could not remove worktree {ticket_id}: {e}"
            )
        click.echo(f"  Removed worktree: {ticket_id}")

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
