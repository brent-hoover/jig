"""Jig CLI."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import click

from jig.models import Issue, MergeStrategy, ProjectContext
from jig.orchestrator import Orchestrator, OrchestratorPaused, OrchestratorFailed
from jig.events import EventEmitter
from jig.ws_server import WebSocketServer
from jig.persistence import (
    init_project, list_issues, load_issue, load_project, load_project_context,
    save_default_agent_types, save_default_workflow, save_issue, save_project_context,
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


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--branch", default=None, help="Default branch name (auto-detected from current branch).")
@click.option("--no-input", is_flag=True, help="Skip interactive prompts.")
def init(path: Path, branch: str | None, no_input: bool) -> None:
    """Initialize .jig/ in a project."""
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

    if no_input:
        save_project_context(path, ProjectContext(name=path.resolve().name))
        return

    click.echo()

    # Gather project context interactively
    project_name = path.resolve().name
    ctx = _prompt_project_context(path, ProjectContext(name=project_name))
    save_project_context(path, ctx)
    click.echo(f"\nProject context saved to .jig/project_context.yaml")
    _apply_template(path, ctx.template_path)


def _apply_template(path: Path, template_path: str) -> None:
    """Copy template files into the project and commit them."""
    if not template_path:
        return

    template_dir = Path(template_path).expanduser()
    if not template_dir.is_absolute():
        template_dir = path / template_dir

    if not template_dir.is_dir():
        click.echo(f"  Warning: template path not found: {template_dir}")
        return

    click.echo(f"  Copying template from {template_dir}")
    copied = 0
    for item in template_dir.iterdir():
        if item.name == ".git":
            continue
        dest = path / item.name
        if dest.exists():
            continue
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)
        copied += 1

    if copied == 0:
        click.echo("  No new files to copy from template")
        return

    click.echo(f"  Copied {copied} item(s) from template")

    # Commit to base branch so worktrees inherit the files
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True)
    result = subprocess.run(
        ["git", "commit", "-m", "chore: apply project template"],
        cwd=path, capture_output=True,
    )
    if result.returncode == 0:
        click.echo("  Template committed to base branch")


MERGE_STRATEGIES = {s.value: s for s in MergeStrategy}


def _get_builtin_templates_dir() -> Path:
    """Return the path to the built-in templates directory."""
    return Path(__file__).resolve().parent.parent / "templates"


def _list_templates() -> dict[str, Path]:
    """List available built-in templates. Returns {name: path}."""
    templates_dir = _get_builtin_templates_dir()
    if not templates_dir.is_dir():
        return {}
    return {
        d.name: d
        for d in sorted(templates_dir.iterdir())
        if d.is_dir() and not d.name.startswith(".")
    }


def _prompt_template(existing_path: str) -> str:
    """Prompt user to pick a template."""
    templates = _list_templates()

    if not templates:
        return click.prompt("Project template directory (or empty to skip)", default=existing_path)

    choices = ["none"] + list(templates.keys()) + ["custom"]

    # Determine default
    default_idx = 1  # "none"
    for i, name in enumerate(choices):
        if name != "none" and name != "custom" and str(templates.get(name, "")) == existing_path:
            default_idx = i + 1
            break

    click.echo("\nAvailable project templates:")
    for i, name in enumerate(choices):
        marker = " (current)" if i + 1 == default_idx and default_idx > 1 else ""
        click.echo(f"  {i + 1}. {name}{marker}")

    raw = click.prompt(f"Choose a template [1-{len(choices)}]", default=str(default_idx))

    # Accept number or name
    try:
        idx = int(raw)
        if 1 <= idx <= len(choices):
            selection = choices[idx - 1]
        else:
            selection = raw
    except ValueError:
        selection = raw

    if selection not in choices:
        click.echo(f"Unknown template '{selection}', skipping.")
        return existing_path

    if selection == "none":
        return ""
    if selection == "custom":
        return click.prompt("Template directory path", default=existing_path)
    return str(templates[selection])


# Defaults inferred from template name
TEMPLATE_DEFAULTS: dict[str, dict[str, str]] = {
    "python": {
        "language": "python",
        "framework": "",
        "package_manager": "uv",
        "setup_commands": "uv sync",
        "build_command": "",
        "test_command": "uv run pytest",
    },
    "fastapi": {
        "language": "python",
        "framework": "fastapi",
        "package_manager": "uv",
        "setup_commands": "uv sync",
        "build_command": "",
        "test_command": "uv run pytest",
    },
}


def _prompt_project_context(path: Path, existing: ProjectContext) -> ProjectContext:
    """Interactively gather project context."""
    click.echo("Project context. (Press Enter to skip/keep current value)\n")

    name = click.prompt("Project name", default=existing.name or path.resolve().name)
    description = click.prompt("Short description", default=existing.description)
    template = _prompt_template(existing.template_path)

    # Infer defaults from template selection
    tpl_name = Path(template).name if template else ""
    defaults = TEMPLATE_DEFAULTS.get(tpl_name, {})

    language = click.prompt(
        "Primary language",
        default=existing.language or defaults.get("language", ""),
    )
    framework = click.prompt(
        "Framework",
        default=existing.framework or defaults.get("framework", ""),
    )
    pkg_mgr = click.prompt(
        "Package manager",
        default=existing.package_manager or defaults.get("package_manager", ""),
    )

    setup_input = click.prompt(
        "Setup commands (semicolon-separated)",
        default="; ".join(existing.setup_commands) or defaults.get("setup_commands", ""),
    )
    setup_cmds = [c.strip() for c in setup_input.split(";") if c.strip()]

    build_cmd = click.prompt(
        "Build command",
        default=existing.build_command or defaults.get("build_command", ""),
    )
    test_cmd = click.prompt(
        "Test command",
        default=existing.test_command or defaults.get("test_command", ""),
    )

    merge = click.prompt(
        "Merge strategy (direct, squash, pr, feature_branch)",
        default=existing.merge_strategy.value,
        type=click.Choice(list(MERGE_STRATEGIES.keys())),
    )

    docs_input = click.prompt(
        "Key docs to read (comma-separated paths)",
        default=",".join(existing.docs),
    )
    docs = [d.strip() for d in docs_input.split(",") if d.strip()]

    notes = click.prompt("Anything else agents should know?", default=existing.notes)

    return ProjectContext(
        name=name,
        description=description,
        language=language,
        framework=framework,
        package_manager=pkg_mgr,
        template_path=template,
        setup_commands=setup_cmds,
        build_command=build_cmd,
        test_command=test_cmd,
        merge_strategy=MERGE_STRATEGIES[merge],
        docs=docs,
        notes=notes,
    )


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def context(path: Path) -> None:
    """Update project context interactively."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    existing = load_project_context(path)
    ctx = _prompt_project_context(path, existing)
    save_project_context(path, ctx)
    click.echo(f"\nProject context saved to .jig/project_context.yaml")
    _apply_template(path, ctx.template_path)


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def status(path: Path) -> None:
    """Show current workflow state."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    config = load_project(path)
    issues = list_issues(path)

    click.echo(f"Project: {config.repo_path}")
    click.echo(f"Branch:  {config.default_branch}")
    click.echo()

    if not issues:
        click.echo("No issues.")
        return

    click.echo(f"{'ID':<15} {'Title':<30} {'Status':<15} {'Phase':<15}")
    click.echo("-" * 75)
    for issue in issues:
        phase = issue.current_phase or "-"
        click.echo(f"{issue.id:<15} {issue.title:<30} {issue.status.value:<15} {phase:<15}")


def _generate_issue_id(project_path: Path) -> str:
    """Generate a sequential issue ID like issue-1, issue-2, etc."""
    issues = list_issues(project_path)
    max_num = 0
    for iss in issues:
        if iss.id.startswith("issue-"):
            try:
                num = int(iss.id.removeprefix("issue-"))
                max_num = max(max_num, num)
            except ValueError:
                pass
    return f"issue-{max_num + 1}"


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--issue-id", default=None, help="Issue ID (auto-generated if omitted).")
@click.option("--title", default=None, help="Issue title.")
@click.option("--ws-port", default=9100, type=int, help="WebSocket server port.", show_default=True)
@click.option("--no-input", is_flag=True, help="Skip interactive prompts.")
@click.option("--description", "desc", default=None, help="Issue description.")
def start(path: Path, issue_id: str | None, title: str | None, ws_port: int, no_input: bool, desc: str | None) -> None:
    """Start a workflow for an issue."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    # Create or load issue
    config = load_project(path)

    issue = None
    if issue_id:
        try:
            issue = load_issue(path, issue_id)
            click.echo(f"Resuming issue: {issue.id} - {issue.title}")
        except FileNotFoundError:
            pass  # Will create below

    if issue is None:
        # New issue — prompt for details if needed
        if not title and not no_input:
            title = click.prompt("What needs to be done?")
        if not title:
            raise click.ClickException("--title is required for new issues.")

        if not issue_id:
            issue_id = _generate_issue_id(path)

        description = desc or ""
        if not no_input and not description:
            description = click.prompt(
                "Any more detail? (press Enter to skip)",
                default="",
            )

        issue = Issue(
            id=issue_id,
            title=title,
            description=description,
            base_branch=config.default_branch,
        )
        save_issue(path, issue)
        click.echo(f"Created issue: {issue.id} - {issue.title}")

    click.echo(f"Starting workflow for {issue_id}...")

    async def run_with_ws():
        emitter = EventEmitter()
        ws_server = WebSocketServer(emitter, port=ws_port)
        await ws_server.start()
        click.echo(f"WebSocket server listening on ws://127.0.0.1:{ws_server.port}")

        orchestrator = Orchestrator(path, issue_id, emitter=emitter)
        try:
            await orchestrator.run()
        finally:
            await ws_server.stop()

    try:
        asyncio.run(run_with_ws())
        click.echo(f"Workflow completed for {issue_id}.")
    except OrchestratorPaused as e:
        click.echo(f"Workflow paused: {e}")
        click.echo("Resolve the issue and run 'jig start' again to resume.")
    except OrchestratorFailed as e:
        raise click.ClickException(f"Workflow failed: {e}")
    except KeyboardInterrupt:
        click.echo("\nWorkflow interrupted. Run 'jig start' again to resume.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--issue-id", required=True, help="Issue to validate.")
def validate(path: Path, issue_id: str) -> None:
    """Validate an issue and clean up worktrees."""
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(f"Jig not initialized in {path}. Run 'jig init' first.")

    try:
        issue = load_issue(path, issue_id)
    except FileNotFoundError:
        raise click.ClickException(f"Issue '{issue_id}' not found.")

    # Clean up worktrees for this issue
    worktrees_dir = jig_dir / "worktrees" / issue_id
    if worktrees_dir.is_dir():
        for phase_dir in worktrees_dir.iterdir():
            if phase_dir.is_dir():
                try:
                    asyncio.run(remove_worktree(path, issue_id, phase_dir.name))
                    click.echo(f"  Removed worktree: {phase_dir.name}")
                except RuntimeError:
                    click.echo(f"  Warning: could not remove worktree {phase_dir.name}")

    click.echo(f"Issue {issue_id} validated.")


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
