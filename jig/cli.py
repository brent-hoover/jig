"""Jig CLI."""

import asyncio
import os
import readline  # noqa: F401  # side-effect: line editing for click.prompt / input()
import shutil
import subprocess
import sys
from pathlib import Path

import click

from jig.events import EventEmitter
from jig.safe_path import validate_safe_path_segment
from jig.ws_server import WebSocketServer
from jig.orchestrator import Orchestrator
from jig.worktree import remove_worktree

# Module-level aliases so tests can monkeypatch jig.cli._chdir / _execvp
# without mutating the global ``os`` module (which would break the test
# harness's own os.chdir calls).
_chdir = os.chdir
_execvp = os.execvp


@click.group()
def cli() -> None:
    """Jig: Agent harness for Claude Code."""


async def _report_section_locks(project_path: Path, ticket_id: str) -> None:
    """Surface section-lock status for a single ticket during ``jig validate``.

    Task M: before touching a ticket's spec, operators want to see
    which fields are locked and by which phase. This runs as part
    of ``jig validate --ticket-id``. Missing ticket / missing stores
    are treated as "nothing to report" rather than errors — the
    flag predates this pre-flight and legacy callers may invoke it
    against ids that never booked a ticket record (e.g. pure
    worktree cleanup).
    """
    from jig.section_locks import locked_sections_for_ticket
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    store_dir = project_path / ".jig" / "store"
    tickets_path = store_dir / "tickets.jsonl"
    threads_path = store_dir / "comments.jsonl"
    if not tickets_path.is_file():
        return

    tickets = TicketStore(tickets_path)
    await tickets.load()
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        return

    threads = ThreadStore(threads_path)
    await threads.load()
    locked = await locked_sections_for_ticket(
        project_path, threads, ticket_id, ticket.work_type
    )
    if not locked:
        click.echo("  No section locks active on this ticket.")
        return

    click.echo("  Locked sections:")
    for field in sorted(locked):
        click.echo(f"    - {field} (locked after phase '{locked[field]}')")


@cli.command()
@click.argument("name")
@click.option("--force", is_flag=True, help="Wipe .jig/ state and restart.")
@click.option(
    "--brief",
    "brief_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Pre-baked brief.md to use instead of running the PO conversation.",
)
@click.option(
    "--auto",
    is_flag=True,
    help="Non-interactive mode: pick defaults for every prompt (for eval harnesses).",
)
def init(name: str, force: bool, brief_file: Path | None, auto: bool) -> None:
    """Initialize a new jig project: brief → spec → architecture → scaffold."""
    import asyncio

    from jig.init_prompts import AutoPromptHandler
    from jig.init_workflow import run_init

    prompts = AutoPromptHandler() if auto else None
    asyncio.run(
        run_init(
            name=name,
            force=force,
            brief_file=brief_file,
            prompts=prompts,
        )
    )


def _run_orchestrator_loop(path: Path, ws_port: int, verbose: bool = False) -> None:
    """Run the orchestrator + WebSocket server in the foreground until interrupted.

    Called by both ``jig start`` (after the Docker check) and
    ``jig daemon serve`` (the body the daemon-start fork executes).
    """
    # Ensure .jig/ exists so logging and daemon files have a place to live
    # even when the project hasn't been initialized yet (unconfigured mode).
    (path / ".jig").mkdir(parents=True, exist_ok=True)

    # Fail-loud catalog validation (Phase 2F). Only when a project config
    # exists — unconfigured mode skips this entirely.
    config_file = path / ".jig" / "config.yaml"
    if config_file.is_file():
        from jig.catalog import CatalogError, validate_catalog

        try:
            validate_catalog(path)
        except CatalogError as exc:
            raise click.ClickException(f"Catalog validation failed: {exc}")

    from jig.logging_setup import configure_logging

    log_file = configure_logging(path, verbose=verbose)
    click.echo(f"Logging to {log_file}")

    async def run_daemon() -> None:
        emitter = EventEmitter()
        orchestrator = Orchestrator(project_path=path, emitter=emitter)
        ws_server = WebSocketServer(
            emitter, port=ws_port, orchestrator=orchestrator, project_path=path
        )
        orchestrator._prompt_registry = ws_server.prompt_registry
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
@click.option(
    "--ws-port",
    default=19100,
    type=int,
    help="WebSocket server port.",
    show_default=True,
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose (DEBUG) logging.")
@click.option(
    "--no-docker", is_flag=True, help="Run without Docker container (no sandbox)."
)
def start(path: Path, ws_port: int, verbose: bool, no_docker: bool) -> None:
    """Start the Jig orchestrator daemon."""
    from jig.container import (
        is_in_container,
        docker_available,
        image_exists,
        build_image,
        exec_in_docker,
    )

    if not is_in_container() and not no_docker:
        if docker_available():
            if not image_exists():
                click.echo("Jig Docker image not found. Building...")
                try:
                    build_image()
                except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                    raise click.ClickException(
                        f"Failed to build Docker image: {exc}\n"
                        "Build manually: docker build -t jig /path/to/jig"
                    )
            click.echo("Launching jig inside Docker container...")
            exec_in_docker(path, ws_port, verbose)
            # exec_in_docker replaces the process — this line is unreachable
        else:
            click.echo(
                "Warning: Docker not available. Running without sandbox.",
                err=True,
            )

    _run_orchestrator_loop(path, ws_port, verbose=verbose)


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def plan(path: Path) -> None:
    """Create a planning ticket so the PM agent breaks the spec into tickets.

    Safe to run multiple times — a no-op if a planning ticket already exists.
    """
    jig_dir = path / ".jig"
    if not (jig_dir / "spec" / "architecture.yaml").is_file():
        raise click.ClickException("Project not initialized. Run 'jig init' first.")

    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket, WorkType

    store_dir = jig_dir / "store"
    tickets = TicketStore(store_dir / "tickets.jsonl")

    async def _run() -> bool:
        await tickets.load()
        existing = await tickets.get("planning")
        if existing is not None:
            return False
        spec_path = jig_dir / "spec" / "project.structured.yaml"
        await tickets.create(
            Ticket(
                id="planning",
                work_type=WorkType.PLANNING,
                title="Project planning",
                description=(
                    "Break down the project spec into implementation tickets.\n\n"
                    f"Spec: {spec_path}"
                ),
                workflow="project",
                created_by="cli",
            )
        )
        return True

    created = asyncio.run(_run())
    if created:
        click.echo("Planning ticket created. Start the orchestrator to begin.")
    else:
        click.echo("Planning ticket already exists.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def sync(path: Path) -> None:
    """Sync default agent types and workflows from the installed jig version.

    Copies any new defaults without overwriting existing customizations.
    """
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(
            f"Jig not initialized in {path}. Run 'jig init' first."
        )

    from jig.persistence import _defaults_dir

    added: list[str] = []

    # Sync agent types
    source_agents = _defaults_dir() / "roles"
    dest_agents = jig_dir / "roles"
    for src in sorted(source_agents.glob("*.yaml")):
        dest = dest_agents / src.name
        if not dest.exists():
            dest.write_text(src.read_text())
            added.append(f"roles/{src.name}")

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


def _validate_impl(path: Path, ticket_id: str | None) -> None:
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(
            f"Jig not initialized in {path}. Run 'jig init' first."
        )

    if ticket_id is not None:
        # Operator can pass any string here; validate before it
        # touches the filesystem so a stray ".." can't probe outside
        # .jig/worktrees.
        validate_safe_path_segment(ticket_id, "ticket_id")
        worktree_path = jig_dir / "worktrees" / ticket_id
        if worktree_path.is_dir():
            try:
                asyncio.run(remove_worktree(path, ticket_id))
            except RuntimeError as e:
                raise click.ClickException(
                    f"Could not remove worktree {ticket_id}: {e}"
                )
            click.echo(f"  Removed worktree: {ticket_id}")
        asyncio.run(_report_section_locks(path, ticket_id))
        click.echo(f"Ticket {ticket_id} validated.")
        return

    # Catalog dry-run. Collect every error so the operator sees the
    # whole picture in one pass.
    from jig.catalog import collect_policy_warnings, validate_catalog

    errors = validate_catalog(path, collect=True) or []
    if errors:
        for msg in errors:
            click.echo(f"  {msg}", err=True)
        raise click.ClickException(
            f"Catalog validation failed ({len(errors)} error(s))."
        )
    # Task F — shadow-pattern advisories. Non-fatal: a permit fully
    # subsumed by a deny compiles to a deterministic ruleset, just one
    # where the permit never fires. Surface as [WARN] so the operator
    # can decide whether to fix or suppress.
    warnings = collect_policy_warnings(path)
    for msg in warnings:
        click.echo(f"  [WARN] {msg}", err=True)
    if warnings:
        click.echo(f"Catalog OK with {len(warnings)} advisory warning(s).")
    else:
        click.echo("Catalog OK.")


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.confirmation_option(
    prompt="This will delete .jig/ and reinitialize git. Continue?"
)
def reset(path: Path) -> None:
    """Reset project to a clean state for testing."""
    jig_dir = path / ".jig"

    # Stop the daemon if it's running so it releases the port before we wipe .jig/
    try:
        from jig.daemon import daemon_status, daemon_stop

        status = daemon_status(path)
        if status.running:
            click.echo("  Stopping daemon")
            daemon_stop(path)
    except Exception as exc:
        click.echo(f"  Warning: could not stop daemon: {exc}", err=True)

    # Remove git worktrees before deleting .git
    if (path / ".git").is_dir():
        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=path,
                capture_output=True,
                text=True,
            )
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    wt = line.removeprefix("worktree ")
                    if wt == str(path.resolve()):
                        continue
                    click.echo(f"  Removing worktree: {wt}")
                    r = subprocess.run(
                        ["git", "worktree", "remove", "--force", wt],
                        cwd=path,
                        capture_output=True,
                        text=True,
                    )
                    if r.returncode != 0:
                        click.echo(
                            f"  Warning: worktree remove failed for {wt}: {r.stderr.strip()}",
                            err=True,
                        )
        except Exception as exc:
            click.echo(f"  Warning: could not remove worktrees: {exc}", err=True)

    # Detect current branch before removing .git
    branch = "develop"
    try:
        ref = subprocess.check_output(
            ["git", "symbolic-ref", "HEAD"],
            cwd=path,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        branch = ref.removeprefix("refs/heads/")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # Remove scaffold-generated files/dirs; preserve docs/ (brief, specs) and
    # hidden dirs other than .jig/.git which are handled separately.
    _SCAFFOLD_NAMES = {"src", "tests", "pyproject.toml", "README.md"}
    for item in path.iterdir():
        if item.name in (".git", ".jig", "docs") or item.name.startswith("."):
            continue
        if item.name in _SCAFFOLD_NAMES:
            click.echo(f"  Removing {item.name}")
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


@cli.command()
def build() -> None:
    """Build (or rebuild) the jig Docker image."""
    from jig.container import docker_available, build_image

    if not docker_available():
        raise click.ClickException("Docker is not installed or not on PATH.")

    click.echo("Building jig Docker image...")
    try:
        build_image()
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc))
    except subprocess.CalledProcessError:
        raise click.ClickException("Docker build failed. Check output above.")
    click.echo("Done.")


@cli.command()
@click.argument("name")
@click.option("--no-git", is_flag=True, help="Skip `git init`.")
def create(name: str, no_git: bool) -> None:
    """Create a new project directory and launch the TUI inside it.

    Equivalent to: ``mkdir <name> && git init <name> && cd <name> && jig``.
    Inside the TUI, run ``/init <name>`` to bootstrap the project (PO
    conversation, brief, spec-gen, scaffold).

    The shell's cwd is unchanged when the TUI exits (subcommands can't
    mutate the parent shell). To re-attach to an existing project later:
    ``cd <name> && jig``.
    """
    target = Path(name)
    if target.exists():
        raise click.ClickException(
            f"{target} already exists. Pick a different name or `cd` into it "
            "and run `jig` directly."
        )
    target.mkdir(parents=True)
    abs_target = target.resolve()
    click.echo(f"Created {abs_target}")
    if not no_git:
        try:
            subprocess.run(
                ["git", "init", "-q", str(abs_target)],
                check=True,
                capture_output=True,
            )
            click.echo("Initialized empty Git repository")
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            click.echo(f"Warning: git init failed (continuing): {exc}", err=True)
    # Chdir + replace this process with `jig` (no args) inside the new dir.
    # __main__:main will see no args, auto-start the daemon, launch the TUI.
    # We ALSO set JIG_PROJECT_PATH so the new process can recover even if
    # chdir somehow didn't take. _chdir / _execvp are module-level so
    # tests can monkeypatch them without mutating the global os module.
    os.environ["JIG_PROJECT_PATH"] = str(abs_target)
    _chdir(abs_target)
    click.echo(f"Launching TUI in {os.getcwd()}")
    _execvp(sys.argv[0], [sys.argv[0]])


# ---------------------------------------------------------------------------
# `jig render ...` — translation renderers (Track I MVP §6).
#
# One source (a typed contract) → many derived views. MVP ships
# Pydantic-from-data-contract; OpenAPI / SQL DDL / etc. land
# opportunistically. The rendered output goes to stdout so the operator
# can pipe it to a file or paste it into an editor — we deliberately
# don't write into the source tree, since the operator owns where the
# generated code lives in their codebase.
# ---------------------------------------------------------------------------


@cli.group("render")
def render_group() -> None:
    """Render typed contracts into derived views (Track I MVP)."""


@render_group.command("pydantic")
@click.argument("module_id")
@click.argument("contract_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def render_pydantic(module_id: str, contract_id: str, path: Path) -> None:
    """Render a DataContract as a Pydantic class to stdout.

    Looks up ``contract_id`` inside the module's contracts.yaml and
    emits a self-contained Pydantic class source. The DataContract must
    have an inline ``fields`` payload (URI-based schema resolution
    lands later).
    """
    from jig.renderers.pydantic_from_data_contract import (
        render_pydantic_from_data_contract,
    )
    from jig.spec_loader import load_module_contracts

    try:
        contracts = load_module_contracts(path, module_id)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc))
    contract = next(
        (c for c in contracts.data_contracts if c.id == contract_id),
        None,
    )
    if contract is None:
        raise click.ClickException(
            f"DataContract {contract_id!r} not found in module {module_id!r} "
            f"(known: {[c.id for c in contracts.data_contracts]})"
        )
    try:
        click.echo(render_pydantic_from_data_contract(contract))
    except ValueError as exc:
        raise click.ClickException(str(exc))


@cli.group("quartermaster")
def quartermaster_group() -> None:
    """Quartermaster briefing + feedback loop (Track I)."""


@quartermaster_group.command("feedback")
@click.argument("briefing_id")
@click.option(
    "--useful/--not-useful",
    default=None,
    required=True,
    help="Mark the briefing useful or not-useful.",
)
@click.option(
    "--noisy-pattern",
    "noisy_patterns",
    multiple=True,
    help=(
        "One or more pattern ids to flag as noisy. Repeat the flag "
        "to tag multiple patterns. Only applies to --not-useful."
    ),
)
@click.option(
    "--note",
    default=None,
    help="Free-form prose attached to the feedback row.",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def quartermaster_feedback(
    briefing_id: str,
    useful: bool,
    noisy_patterns: tuple[str, ...],
    note: str | None,
    path: Path,
) -> None:
    """Record operator feedback on a quartermaster briefing.

    Each --not-useful invocation that names a pattern raises that
    pattern's threshold by 1 (capped at 2x default). After 30 days
    of no feedback the calibration drifts back toward defaults.
    """
    from jig.quartermaster import record_feedback

    try:
        row_id = asyncio.run(
            record_feedback(
                path,
                briefing_id=briefing_id,
                useful=useful,
                not_useful_pattern_ids=list(noisy_patterns),
                note=note,
            )
        )
    except ValueError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"recorded feedback {row_id} on briefing {briefing_id}")


@cli.group("hooks")
def hooks_group() -> None:
    """Manage human-side git hooks (pre-commit, pre-push, commit-msg)."""


@hooks_group.command("install")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--force", is_flag=True, help="Overwrite an existing .jig-backup.")
def hooks_install(path: Path, force: bool) -> None:
    """Install jig-managed git hooks under .git/hooks/."""
    from jig.hooks import HookInstallError, install_hooks

    try:
        report = install_hooks(path, force=force)
    except HookInstallError as exc:
        raise click.ClickException(str(exc))
    except RuntimeError as exc:
        raise click.ClickException(str(exc))
    for line in report:
        click.echo(line)


@hooks_group.command("uninstall")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def hooks_uninstall(path: Path) -> None:
    """Remove jig-managed git hooks; restore any backups."""
    from jig.hooks import uninstall_hooks

    try:
        report = uninstall_hooks(path)
    except RuntimeError as exc:
        raise click.ClickException(str(exc))
    for line in report:
        click.echo(line)


@hooks_group.command("status")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def hooks_status_cmd(path: Path) -> None:
    """Report which jig hooks are installed."""
    from jig.hooks import hook_status

    try:
        lines = hook_status(path)
    except RuntimeError as exc:
        raise click.ClickException(str(exc))
    for line in lines:
        click.echo(line)


@hooks_group.command("run")
@click.argument("stage", type=click.Choice(["pre-commit", "pre-push", "commit-msg"]))
@click.argument("args", nargs=-1)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def hooks_run(stage: str, args: tuple[str, ...], path: Path) -> None:
    """Run the check subset for a hook stage (invoked by hook scripts)."""
    import asyncio

    from jig.hooks import (
        run_commit_msg,
        run_pre_commit,
        run_pre_push,
    )

    if stage == "pre-commit":
        rc = asyncio.run(run_pre_commit(path))
        raise SystemExit(rc)
    if stage == "pre-push":
        rc = asyncio.run(run_pre_push(path))
        raise SystemExit(rc)
    if stage == "commit-msg":
        if not args:
            raise click.ClickException("commit-msg requires a message file path")
        rc = run_commit_msg(Path(args[0]))
        raise SystemExit(rc)
    raise click.ClickException(f"stage {stage!r} not yet implemented")


# ---------------------------------------------------------------------------
# `jig ticket ...` — human-driven ticket management against a running
# orchestrator. The orchestrator already owns the business logic (via
# `ws_server`'s `create_ticket` command); this is a thin WebSocket client.
# ---------------------------------------------------------------------------

_DEFAULT_WS_URL = "ws://127.0.0.1:19100"
_WORK_TYPES = ["feature", "bugfix", "refactor", "spike", "perf", "migration", "docs"]


# Track G MVP follow-on: per-commit reviewer hook entry point.
# Registered as a top-level command (not under ``hooks``) because the
# installed post-commit hook calls it by short name; nesting under
# ``hooks`` would force the hook script to use ``jig hooks <name>``,
# breaking the hook's documented invocation in design.md.
from jig.hooks.per_commit_runner import main as _per_commit_main  # noqa: E402

cli.add_command(_per_commit_main, name="per-commit-review")


@cli.group("ticket")
def ticket_group() -> None:
    """Manage tickets against a running orchestrator."""


async def _send_create_ticket(ws_url: str, args: dict) -> dict:
    """Open ``ws_url``, send a create_ticket command, return the reply.

    The ws_server emits mirrored events ({"type", "data"}) alongside the
    command reply ({"ok": ..., ...}); we skip events and return the first
    reply. Connection errors raise ``OSError`` — the caller turns them
    into a friendly CLI message.
    """
    import json

    from websockets.asyncio.client import connect

    async with connect(ws_url, open_timeout=3, close_timeout=2) as ws:
        await ws.send(json.dumps({"command": "create_ticket", "args": args}))
        # Drain events until the command reply lands. A misbehaving server
        # could starve us forever; a generous timeout per-recv keeps the
        # CLI from hanging indefinitely.
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            parsed = json.loads(raw)
            if "ok" in parsed:
                return parsed


@ticket_group.command("create")
@click.option("--title", required=True, help="One-line ticket title.")
@click.option(
    "--work-type",
    type=click.Choice(_WORK_TYPES),
    default="feature",
    show_default=True,
    help="Classification axis (see docs/03-specs-and-work-types.md).",
)
@click.option(
    "--size",
    default="m",
    show_default=True,
    help="T-shirt size (xs|s|m|l|xl). Unknown values are rejected by the server.",
)
@click.option(
    "--description",
    "description",
    default=None,
    help="Ticket body. Mutually exclusive with --description-file.",
)
@click.option(
    "--description-file",
    "description_file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read the ticket body from a file (for multi-line briefs).",
)
@click.option("--workflow", default=None, help="Override the resolved workflow.")
@click.option("--assignee", default=None, help="Pre-assign to a role.")
@click.option("--parent-id", default=None, help="Parent ticket ID (for sub-tickets).")
@click.option(
    "--depends-on",
    "depends_on",
    multiple=True,
    help="Block this ticket until the given ticket resolves. Repeatable.",
)
@click.option(
    "--ws-url",
    default=_DEFAULT_WS_URL,
    show_default=True,
    help="Orchestrator WebSocket URL.",
)
def ticket_create(
    title: str,
    work_type: str,
    size: str,
    description: str | None,
    description_file: Path | None,
    workflow: str | None,
    assignee: str | None,
    parent_id: str | None,
    depends_on: tuple[str, ...],
    ws_url: str,
) -> None:
    """Create a ticket against a running orchestrator.

    Prints the new ticket ID to stdout on success so callers can pipe it.
    Requires `jig start` to be running at ``--ws-url``.
    """
    if description is not None and description_file is not None:
        raise click.UsageError(
            "--description and --description-file are mutually exclusive"
        )
    body = description
    if description_file is not None:
        body = description_file.read_text()

    args: dict = {
        "work_type": work_type,
        "size": size,
        "title": title,
    }
    if body is not None:
        args["description"] = body
    if workflow is not None:
        args["workflow"] = workflow
    if assignee is not None:
        args["assignee"] = assignee
    if parent_id is not None:
        args["parent_id"] = parent_id
    if depends_on:
        args["depends_on"] = list(depends_on)

    try:
        reply = asyncio.run(_send_create_ticket(ws_url, args))
    except OSError as exc:
        raise click.ClickException(
            f"could not connect to orchestrator at {ws_url}: {exc}"
        ) from exc
    except asyncio.TimeoutError as exc:
        raise click.ClickException(
            f"timed out waiting for orchestrator reply at {ws_url}"
        ) from exc

    if not reply.get("ok"):
        raise click.ClickException(
            f"orchestrator rejected ticket: {reply.get('error', 'unknown error')}"
        )
    ticket_id = reply.get("ticket_id")
    if not ticket_id:
        raise click.ClickException("orchestrator did not return a ticket_id")
    click.echo(ticket_id)


# ---------------------------------------------------------------------------
# `jig story <ticket-id>` — print the combined thread + log narrative for a
# single ticket. Backed by `jig.story.build_story` (Task 13). Pretty-print by
# default; `--json` emits one JSON object per line for machine consumers.
# ---------------------------------------------------------------------------


@cli.command()
@click.argument("ticket_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
@click.option("--json", "json_out", is_flag=True, help="Output JSON per line.")
@click.option(
    "--include-children",
    is_flag=True,
    help="Include events from child tickets.",
)
@click.option(
    "--since",
    type=str,
    default=None,
    help="ISO8601 timestamp — only show events at or after this time.",
)
@click.option(
    "--level",
    type=click.Choice(["DEBUG", "INFO"]),
    default="INFO",
    help="Minimum log level (thread entries are always shown).",
)
def story(
    ticket_id: str,
    path: Path,
    json_out: bool,
    include_children: bool,
    since: str | None,
    level: str,
) -> None:
    """Print the full story of a ticket."""
    import json
    from datetime import datetime, timezone

    from jig.story import StorySource, build_story
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            raise click.ClickException(f"Invalid --since: {exc}")
        # Coerce naive timestamps to UTC so comparison with the aware
        # event timestamps inside build_story() doesn't raise TypeError.
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=timezone.utc)

    async def run() -> list:
        threads_path = path / ".jig" / "store" / "comments.jsonl"
        tickets_path = path / ".jig" / "store" / "tickets.jsonl"
        threads = ThreadStore(threads_path)
        await threads.load()
        tickets = TicketStore(tickets_path)
        await tickets.load()
        # Verify ticket exists up front so unknown ids fail loud rather
        # than returning an empty story.
        t = await tickets.get(ticket_id)
        if t is None:
            raise click.ClickException(f"Ticket {ticket_id!r} not found")
        return await build_story(
            ticket_id,
            project_path=path,
            threads=threads,
            tickets=tickets,
            include_children=include_children,
            since=since_dt,
        )

    events = asyncio.run(run())

    # Filter by level for log events only (thread entries are always shown).
    if level == "INFO":
        events = [
            e for e in events if e.source != StorySource.log or e.level != "DEBUG"
        ]

    if not events:
        click.echo("(no events)", err=True)
        return

    if json_out:
        for ev in events:
            click.echo(
                json.dumps(
                    {
                        "ts": ev.ts.isoformat(),
                        "source": ev.source.value,
                        "kind": ev.kind,
                        "level": ev.level,
                        "message": ev.message,
                        "ticket_id": ev.ticket_id,
                        "phase": ev.phase,
                        "role": ev.role,
                    }
                )
            )
        return

    # Pretty print. Show elapsed since first event.
    first_ts = events[0].ts
    for ev in events:
        elapsed = (ev.ts - first_ts).total_seconds()
        src_tag = {
            StorySource.thread: "T",
            StorySource.log: "L",
            StorySource.finding: "F",
        }.get(ev.source, "?")
        click.echo(
            f"{ev.ts.strftime('%H:%M:%S.%f')[:12]} "
            f"(+{elapsed:7.2f}s) [{src_tag}] "
            f"{ev.level:5s} {ev.message}"
        )


# ---------------------------------------------------------------------------
# `jig dev ...` — Track E MVP: dev-environment manifest + orphan tooling
# ---------------------------------------------------------------------------


@cli.group("dev")
def dev_group() -> None:
    """Inspect and manage the per-project dev environment (Track E MVP)."""


@dev_group.command("manifest")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
@click.option(
    "--derive/--no-derive",
    default=True,
    show_default=True,
    help="Re-derive from architecture.yaml before printing.",
)
def dev_manifest_cmd(path: Path, derive: bool) -> None:
    """Print the derived dev-environment manifest as YAML.

    By default re-runs the derivation against the current
    ``architecture.yaml`` so the printed manifest is always fresh; pass
    ``--no-derive`` to print the on-disk file as-is.
    """
    import yaml

    from jig.dev_env.manifest import derive_manifest
    from jig.spec_loader import (
        load_architecture,
        load_dev_manifest,
        save_dev_manifest,
    )

    if derive:
        try:
            arch = load_architecture(path)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
        manifest = derive_manifest(arch)
        save_dev_manifest(path, manifest)
    else:
        try:
            manifest = load_dev_manifest(path)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
    click.echo(
        yaml.safe_dump(manifest.model_dump(mode="json"), sort_keys=False).rstrip()
    )


@dev_group.group("orphans")
def dev_orphans_group() -> None:
    """List, drop, or purge orphan namespaces."""


async def _load_tracker(path: Path):
    from jig.dev_env.orphans import OrphanTracker
    from jig.spec_loader import load_dev_manifest
    from jig.store.tickets import TicketStore

    manifest = load_dev_manifest(path)
    tickets = TicketStore(path / ".jig" / "store" / "tickets.jsonl")
    await tickets.load()
    return OrphanTracker(path, manifest, tickets)


@dev_orphans_group.command("list")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_orphans_list_cmd(path: Path) -> None:
    """List orphan namespaces (tickets that are no longer OPEN/IN_PROGRESS)."""

    async def _run() -> None:
        try:
            tracker = await _load_tracker(path)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
        rows = await tracker.list_orphans()
        if not rows:
            click.echo("(no orphans)")
            return
        for o in rows:
            click.echo(
                f"{o.id}\tservice={o.service_id}\tkind={o.service_kind}\t"
                f"namespace={o.namespace}\tstatus={o.ticket_status}"
            )

    asyncio.run(_run())


@dev_orphans_group.command("drop")
@click.argument("orphan_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_orphans_drop_cmd(orphan_id: str, path: Path) -> None:
    """Drop one orphan namespace identified by its composite id."""

    async def _run() -> None:
        try:
            tracker = await _load_tracker(path)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
        ok = await tracker.drop(orphan_id)
        if not ok:
            raise click.ClickException(f"orphan id {orphan_id!r} not found")
        click.echo(f"dropped {orphan_id}")

    asyncio.run(_run())


@dev_group.group("fixtures")
def dev_fixtures_group() -> None:
    """Inspect / clear vcr-style external-API fixture cassettes (Track E Final)."""


@dev_fixtures_group.command("list")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_fixtures_list_cmd(path: Path) -> None:
    """List service ids with at least one recorded cassette."""
    from jig.dev_env.fixtures import FixtureStore

    async def _run() -> None:
        store = FixtureStore(path)
        services = await store.list_services()
        if not services:
            click.echo("(no fixtures recorded)")
            return
        for sid in services:
            rows = await store.list_for_service(sid)
            click.echo(f"{sid}\t{len(rows)} cassette(s)")

    asyncio.run(_run())


@dev_fixtures_group.command("show")
@click.argument("service_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_fixtures_show_cmd(service_id: str, path: Path) -> None:
    """Print every cassette recorded for ``service_id``."""
    from jig.dev_env.fixtures import FixtureStore

    async def _run() -> None:
        store = FixtureStore(path)
        rows = await store.list_for_service(service_id)
        if not rows:
            click.echo(f"(no cassettes for {service_id})")
            return
        for c in rows:
            method = c.request.get("method", "?")
            url = c.request.get("url", "?")
            click.echo(
                f"{c.recorded_at.isoformat()}\t{method}\t{url}\t"
                f"sig={c.request_signature[:12]}…"
            )

    asyncio.run(_run())


@dev_fixtures_group.command("clear")
@click.argument("service_id")
@click.option(
    "--confirm",
    is_flag=True,
    help="Required — destroys every cassette for the named service.",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_fixtures_clear_cmd(service_id: str, confirm: bool, path: Path) -> None:
    """Drop every cassette for ``service_id``. Requires ``--confirm``."""
    if not confirm:
        raise click.ClickException(
            "refusing to clear without --confirm "
            f"(destroys every cassette for {service_id!r})"
        )
    from jig.dev_env.fixtures import FixtureStore

    async def _run() -> None:
        store = FixtureStore(path)
        await store.clear_service(service_id)

    asyncio.run(_run())
    click.echo(f"cleared {service_id}")


@dev_group.group("sweeper")
def dev_sweeper_group() -> None:
    """Periodic orphan sweeper with operator-confirmation (Track E Final)."""


async def _build_sweeper(path: Path, threshold_days: int):
    from jig.dev_env.sweeper import OrphanSweeper
    from jig.spec_loader import load_dev_manifest
    from jig.store.tickets import TicketStore

    manifest = load_dev_manifest(path)
    tickets = TicketStore(path / ".jig" / "store" / "tickets.jsonl")
    await tickets.load()
    return OrphanSweeper(path, manifest, tickets, threshold_days=threshold_days)


@dev_sweeper_group.command("run")
@click.option(
    "--threshold-days",
    type=int,
    default=7,
    show_default=True,
    help="Age threshold (days) — older terminal tickets bucket as auto_safe.",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_sweeper_run_cmd(threshold_days: int, path: Path) -> None:
    """Categorize orphan namespaces into auto_safe / needs_confirm / keep buckets."""

    async def _run() -> None:
        try:
            sweeper = await _build_sweeper(path, threshold_days)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
        report = await sweeper.run()
        click.echo(
            f"sweep complete (threshold_days={report.threshold_days}): "
            f"auto_safe={len(report.auto_safe)} "
            f"needs_confirm={len(report.needs_confirm)} "
            f"keep={len(report.keep)}"
        )
        if report.auto_safe:
            click.echo("auto_safe:")
            for o in report.auto_safe:
                click.echo(
                    f"  {o.id}\tstatus={o.ticket_status}\tnamespace={o.namespace}"
                )
        if report.needs_confirm:
            click.echo("needs_confirm:")
            for o in report.needs_confirm:
                click.echo(
                    f"  {o.id}\tstatus={o.ticket_status}\tnamespace={o.namespace}"
                )
        if report.keep:
            click.echo("keep:")
            for o in report.keep:
                click.echo(
                    f"  {o.id}\tstatus={o.ticket_status}\tnamespace={o.namespace}"
                )

    asyncio.run(_run())


@dev_sweeper_group.command("apply")
@click.option(
    "--bucket",
    type=click.Choice(["auto_safe", "needs_confirm", "all"]),
    required=True,
    help="Which bucket to drop.",
)
@click.option(
    "--confirm",
    is_flag=True,
    help="Required for needs_confirm + all buckets (auto_safe is exempt).",
)
@click.option(
    "--threshold-days",
    type=int,
    default=7,
    show_default=True,
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_sweeper_apply_cmd(
    bucket: str, confirm: bool, threshold_days: int, path: Path
) -> None:
    """Drop the named bucket. needs_confirm + all require --confirm."""
    from jig.dev_env.sweeper import SweepBucket

    if bucket in {"needs_confirm", "all"} and not confirm:
        raise click.ClickException(
            f"refusing to apply --bucket {bucket} without --confirm"
        )

    async def _run() -> None:
        try:
            sweeper = await _build_sweeper(path, threshold_days)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
        report = await sweeper.run()
        n = await sweeper.apply(report, bucket=SweepBucket(bucket))
        click.echo(f"applied: dropped {n} orphan(s) from bucket={bucket}")

    asyncio.run(_run())


@dev_group.group("ephemeral")
def dev_ephemeral_group() -> None:
    """Inspect / drop per-agent ephemeral instances (Track E Final)."""


@dev_ephemeral_group.command("list")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_ephemeral_list_cmd(path: Path) -> None:
    """List active ephemeral SQLite instances under ``.jig/dev/ephemeral/``."""
    from jig.dev_env.ephemeral import list_ephemeral_instances

    rows = list_ephemeral_instances(path)
    if not rows:
        click.echo("(no ephemeral instances)")
        return
    for inst in rows:
        click.echo(
            f"{inst.id}\tkind={inst.kind}\tpath={inst.path}\t{inst.size_bytes} bytes"
        )


@dev_ephemeral_group.command("inspect")
@click.argument("instance_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_ephemeral_inspect_cmd(instance_id: str, path: Path) -> None:
    """Print per-table row counts for one SQLite ephemeral instance."""
    from jig.dev_env.ephemeral import inspect_ephemeral_instance

    try:
        rows = inspect_ephemeral_instance(path, instance_id)
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc))
    if not rows:
        click.echo("(no tables)")
        return
    click.echo(f"{instance_id}:")
    for r in rows:
        click.echo(f"  {r.table}\t{r.rows} rows")


@dev_ephemeral_group.command("drop")
@click.argument("instance_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_ephemeral_drop_cmd(instance_id: str, path: Path) -> None:
    """Drop one SQLite ephemeral instance (operator override)."""
    from jig.dev_env.ephemeral import drop_ephemeral_instance

    if not drop_ephemeral_instance(path, instance_id):
        raise click.ClickException(f"ephemeral instance {instance_id!r} not found")
    click.echo(f"dropped {instance_id}")


@dev_orphans_group.command("purge")
@click.option(
    "--confirm",
    is_flag=True,
    help="Required — destroys every orphan namespace. Use with care.",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def dev_orphans_purge_cmd(confirm: bool, path: Path) -> None:
    """Drop every orphan namespace. Requires ``--confirm`` for safety."""
    if not confirm:
        raise click.ClickException(
            "refusing to purge without --confirm (destroys every orphan namespace)"
        )

    async def _run() -> None:
        try:
            tracker = await _load_tracker(path)
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc))
        n = await tracker.purge()
        click.echo(f"purged {n} orphan(s)")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# `jig serve` — local HTTP server for the wireframes index (Track D MVP)
# ---------------------------------------------------------------------------


@cli.command(name="serve")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
@click.option(
    "--port",
    default=8765,
    type=int,
    show_default=True,
    help="HTTP port to listen on.",
)
@click.option(
    "--regenerate/--no-regenerate",
    default=True,
    show_default=True,
    help="Regenerate index.html before serving.",
)
def serve_cmd(path: Path, port: int, regenerate: bool) -> None:
    """Serve the wireframes dir on localhost so the operator can review.

    Walks ``.jig/spec/wireframes/``, regenerates ``index.html`` (so the
    operator picks up wireframes added since the last serve), then
    starts ``python -m http.server`` rooted at the wireframes dir.
    The operator opens ``http://localhost:<port>/index.html`` in their
    browser.

    The default port (8765) is the design's suggested value;
    ``--port`` overrides for the rare case the operator already has
    something on it. ``--no-regenerate`` skips the index re-write —
    useful when an operator wants to inspect a previously-generated
    index without the regeneration noise.
    """
    import http.server
    import socketserver

    from jig.spec_loader import wireframes_dir
    from jig.wireframes.index_generator import generate_index

    target = wireframes_dir(path)
    if not target.is_dir():
        raise click.ClickException(
            f"wireframes dir not found at {target}; run VD discovery first"
        )

    if regenerate:
        index_html = generate_index(target)
        (target / "index.html").write_text(index_html)
        click.echo(f"Regenerated {target / 'index.html'}")

    handler = http.server.SimpleHTTPRequestHandler

    # ``directory=`` keyword on SimpleHTTPRequestHandler is the cleanest
    # way to root the server at the wireframes dir without chdir'ing
    # the whole process.
    class WireframesHandler(handler):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(target), **kwargs)

    click.echo(
        f"Serving {target} at http://localhost:{port}/index.html (Ctrl-C to stop)"
    )
    with socketserver.TCPServer(("127.0.0.1", port), WireframesHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            click.echo("\nshutting down")


# ---------------------------------------------------------------------------
# `jig daemon ...` — manage the background daemon (orchestrator + WebSocket)
# ---------------------------------------------------------------------------


@cli.command(name="tui", hidden=True)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def tui_cmd(path: Path) -> None:
    """Launch the Textual TUI (default action when `jig` is invoked
    with no args via __main__.py)."""
    from jig.tui.app import JigApp

    JigApp(project_path=path).run()


@cli.group(name="daemon")
def daemon_group() -> None:
    """Manage the background daemon (orchestrator + WebSocket server)."""


@daemon_group.command(name="start")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--ws-port",
    default=None,
    type=int,
    help="WebSocket port (default: 19100 if free, else auto-pick ephemeral).",
)
@click.option(
    "--docker/--no-docker",
    default=None,
    help=(
        "Run inside a Docker container (host isolation + bwrap per agent). "
        "Defaults to auto-detect: docker if available + image built."
    ),
)
def daemon_start_cmd(path: Path, ws_port: int | None, docker: bool | None) -> None:
    """Start the daemon in the background."""
    from jig.container import docker_available, image_exists
    from jig.daemon import DaemonAlreadyRunning, daemon_start

    if docker is None:
        # Auto-detect: prefer docker when available + image built.
        docker = docker_available() and image_exists()

    try:
        result = daemon_start(path, ws_port=ws_port, docker=docker)
    except DaemonAlreadyRunning as exc:
        raise click.ClickException(str(exc))
    except RuntimeError as exc:
        raise click.ClickException(str(exc))

    if result.orphans_removed:
        click.echo(
            "warning: removed orphan jig container(s) from prior run: "
            + ", ".join(result.orphans_removed),
            err=True,
        )

    if result.container_id:
        click.echo(
            f"daemon started (docker): container={result.container_id[:12]} "
            f"addr={result.addr}"
        )
    else:
        click.echo(f"daemon started: pid={result.pid} addr={result.addr}")


@daemon_group.command(name="stop")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def daemon_stop_cmd(path: Path) -> None:
    """Stop the daemon if running."""
    from jig.daemon import daemon_stop

    if daemon_stop(path):
        click.echo("daemon stopped")
    else:
        click.echo("no daemon was running")


@daemon_group.command(name="status")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def daemon_status_cmd(path: Path) -> None:
    """Print daemon status (running? pid/container? addr?)."""
    from jig.daemon import daemon_paths, daemon_status

    status = daemon_status(path)
    if not status.running:
        if status.stale:
            msg = "daemon: not running (stale state present)"
            if status.last_error:
                msg += f"\n  last error: {status.last_error}"
            err_path = daemon_paths(path).stderr_log
            if err_path.is_file():
                msg += f"\n  full log: {err_path}"
            click.echo(msg)
        else:
            click.echo("daemon: not running")
        return
    addr_file = daemon_paths(path).socket_addr_file
    addr = addr_file.read_text().strip() if addr_file.is_file() else "?"
    if status.kind == "docker":
        click.echo(
            f"daemon: running (docker) container={status.container_id[:12]} addr={addr}"
        )
    else:
        click.echo(f"daemon: running pid={status.pid} addr={addr}")


@daemon_group.command(name="serve", hidden=True)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--ws-port", default=19100, type=int)
def daemon_serve_cmd(path: Path, ws_port: int) -> None:
    """Internal: actually host the orchestrator. Called by daemon_start
    via the forked subprocess; not for direct user invocation."""
    _run_orchestrator_loop(path, ws_port)


# Attach the synthetic operator simulator (Track H5, bones). Bones
# ships ``jig sim run <scenario.yaml>`` only — see ``jig.sim.cli`` for
# the full surface roadmap (run-tier / coverage / realism land in MVP).
from jig.sim.cli import sim as _sim_group  # noqa: E402

cli.add_command(_sim_group)


# ---- jig sa cascade subgroup (Track C Final, Deliverable 2) ----------------


@cli.group("sa")
def sa_group() -> None:
    """SA-related operator commands (cascades, future SA tools)."""


@sa_group.group("cascade")
def sa_cascade_group() -> None:
    """Inspect cascade proposals + their audit trail."""


@sa_cascade_group.command("list")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def sa_cascade_list_cmd(path: Path) -> None:
    """List every cascade on disk (resolved + pending + rejected + holding)."""
    from jig.cascade_viewer import format_list, list_cascades

    rows = list_cascades(path)
    click.echo(format_list(rows))


@sa_cascade_group.command("show")
@click.argument("cascade_id")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def sa_cascade_show_cmd(cascade_id: str, path: Path) -> None:
    """Show one cascade — proposal + audit log + current state."""
    from jig.cascade_viewer import format_show, show_cascade

    try:
        result = show_cascade(path, cascade_id)
    except KeyError as e:
        raise click.ClickException(str(e))
    click.echo(format_show(result))


@sa_cascade_group.command("audit")
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"]),
    default=None,
    help="Only show entries on or after this UTC date/time.",
)
@click.option(
    "--operator",
    "actor",
    type=str,
    default=None,
    help="Filter by actor name (exact match).",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def sa_cascade_audit_cmd(since, actor: str | None, path: Path) -> None:
    """Filtered audit log view (markdown table)."""
    # Click's DateTime parser returns naive datetimes; the viewer
    # compares against tz-aware timestamps in the audit log so we
    # promote to UTC at the boundary rather than scattering tz logic
    # through the filter.
    from datetime import timezone as _tz

    from jig.cascade_viewer import filter_audit, format_audit_markdown

    since_aware = since.replace(tzinfo=_tz.utc) if since is not None else None
    entries = filter_audit(path, since=since_aware, actor=actor)
    click.echo(format_audit_markdown(entries))


# ---- jig pm subgroup (Track F Final) -------------------------------------


@cli.group("pm")
def pm_group() -> None:
    """PM-side operator commands (calibration, overrides, cycle view)."""


@pm_group.group("calibration")
def pm_calibration_group() -> None:
    """Estimation calibration loop inspection."""


@pm_calibration_group.command("show")
@click.option(
    "--size",
    type=click.Choice(["xs", "s", "m", "l", "xl"]),
    default=None,
    help="Optional size filter; default prints all sizes.",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def pm_calibration_show(size: str | None, path: Path) -> None:
    """Print current per-size envelopes (median + p90 over turns/cost/duration)."""
    import json

    from jig.pm.calibration import (
        CalibrationStore,
        current_envelopes,
        serialize_envelopes_for_cli,
    )

    store = CalibrationStore(path)
    asyncio.run(store.load())
    envelopes = current_envelopes(store)
    if size is not None:
        env = envelopes.get(size)
        if env is None:
            click.echo(f"(no envelope for size={size!r})")
            return
        click.echo(json.dumps(env.model_dump(mode="json"), indent=2))
        return
    click.echo(json.dumps(serialize_envelopes_for_cli(envelopes), indent=2))


# ---- jig pm plan + overrides (Track F Final) ----------------------------


@pm_group.group("plan")
def pm_plan_group() -> None:
    """Build-plan operator commands (override gates, etc.)."""


@pm_plan_group.command("unblock")
@click.argument("epic_id")
@click.option(
    "--rationale",
    default="",
    help="Operator rationale for the override (recorded in audit log).",
)
@click.option(
    "--cascade-risk-low",
    is_flag=True,
    default=False,
    help=(
        "Acknowledge an SA cascade_risk_low hint as the trigger. "
        "Recorded so analytics can correlate the override with the "
        "SA suggestion."
    ),
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def pm_plan_unblock(
    epic_id: str,
    rationale: str,
    cascade_risk_low: bool,
    path: Path,
) -> None:
    """Manually override the bones-first gate for one epic.

    Records an entry in ``.jig/plan/overrides.jsonl`` and emits a
    ``BonesPromotedIncomplete`` analytics event so the consequences
    are visible later if the still-running bones forces a contract
    change. Per docs/v2.0/pm-workflow/design.md §"Bones-first ordering".
    """
    from jig.analytics.emitter import EventEmitter
    from jig.analytics.store import AnalyticsStore
    from jig.pm.overrides import record_unblock_override
    from jig.schemas.plan import LayerStatusEnum
    from jig.spec_loader import load_build_plan

    async def _run() -> None:
        try:
            plan = load_build_plan(path)
        except FileNotFoundError:
            raise click.ClickException(
                f"no build plan found at {path}/.jig/plan/build-plan.yaml"
            )

        # Compute still-running bones context for the audit + event.
        still_running: list[str] = []
        for epic in plan.epics:
            layer = epic.layers.bones
            if not layer.tickets:
                continue
            if layer.status != LayerStatusEnum.DONE:
                still_running.append(epic.id)

        # Best-effort SA cascade_risk_low correlation.
        sa_low: list[str] = []
        try:
            from jig.spec_loader import load_architecture

            arch = load_architecture(path)
            low_modules = {m.id for m in arch.modules if m.cascade_risk_low}
            for epic in plan.epics:
                if epic.id not in still_running:
                    continue
                if epic.modules and all(mid in low_modules for mid in epic.modules):
                    sa_low.append(epic.id)
        except FileNotFoundError:
            pass

        # Optional analytics emit; non-blocking on missing store dir.
        emitter: EventEmitter | None = None
        store_dir = path / ".jig" / "store"
        if store_dir.is_dir():
            analytics = AnalyticsStore(store_dir / "analytics.jsonl")
            await analytics.load()
            emitter = EventEmitter(analytics)

        await record_unblock_override(
            project_root=path,
            epic_id=epic_id,
            rationale=rationale,
            cascade_risk_low_acknowledged=cascade_risk_low,
            still_running_bones_epic_ids=still_running,
            sa_marked_cascade_risk_low_epic_ids=sa_low,
            emitter=emitter,
        )
        if emitter is not None:
            await emitter.drain()

        click.echo(f"recorded override for epic {epic_id!r}")
        click.echo(f"audit: {path}/.jig/plan/overrides.jsonl")

    asyncio.run(_run())


@pm_group.group("overrides")
def pm_overrides_group() -> None:
    """Inspect manual bones-first overrides."""


@pm_overrides_group.command("list")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def pm_overrides_list(path: Path) -> None:
    """List every recorded override (chronological)."""
    from jig.pm.overrides import list_overrides

    rows = list_overrides(path)
    if not rows:
        click.echo("(no overrides recorded)")
        return
    for row in rows:
        cri = " (cascade_risk_low)" if row.cascade_risk_low_acknowledged else ""
        click.echo(
            f"[{row.recorded_at.isoformat()}] {row.epic_id} by {row.actor}{cri}: "
            f"{row.rationale or '(no rationale)'}"
        )


@pm_group.command("view")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def pm_view_cmd(path: Path) -> None:
    """Print the cycle view (epics × layers + Coordinator state + envelopes)."""
    from jig.coordinator import Coordinator
    from jig.pm.cycle_view import build_cycle_view, format_cycle_view
    from jig.store.tickets import TicketStore

    async def _run() -> None:
        store_dir = path / ".jig" / "store"
        tickets = TicketStore(store_dir / "tickets.jsonl")
        if (store_dir / "tickets.jsonl").is_file():
            await tickets.load()
        coord = Coordinator(tickets=tickets, project_root=path)
        view = await build_cycle_view(coord, path)
        click.echo(format_cycle_view(view))

    asyncio.run(_run())


# ---- ontology CLI (Track B Final operator-edit affordances) -------------


@cli.group("ontology")
def ontology_group() -> None:
    """Inspect and edit the project ontology (operator-edit affordances)."""


@ontology_group.command("list")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def ontology_list_cmd(path: Path) -> None:
    """List every term currently in ``.jig/spec/ontology.md``."""
    from jig.po_ontology_mcp import handle_ontology_get_terms

    out = asyncio.run(handle_ontology_get_terms(project_path=path))
    terms = out.get("terms", [])
    if not terms:
        click.echo("(no terms)")
        return
    for t in terms:
        click.echo(f"- {t['term']}")


@ontology_group.command("show")
@click.argument("term")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def ontology_show_cmd(term: str, path: Path) -> None:
    """Print the full entry for one term."""
    from jig.po_ontology_mcp import handle_ontology_lookup

    out = asyncio.run(handle_ontology_lookup(project_path=path, term=term))
    if out is None:
        raise click.ClickException(f"term {term!r} not in ontology")
    click.echo(f"### {out['term']}")
    click.echo(out["definition"])
    if out.get("examples"):
        click.echo("")
        click.echo("**Examples:**")
        for ex in out["examples"]:
            click.echo(f"- {ex}")


@ontology_group.command("edit")
@click.argument("term")
@click.option("--definition", required=True, help="New definition (one paragraph).")
@click.option(
    "--example",
    "examples",
    multiple=True,
    help="An example bullet. Repeat to add multiple.",
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def ontology_edit_cmd(
    term: str, definition: str, examples: tuple[str, ...], path: Path
) -> None:
    """Replace an existing term's definition + examples."""
    from jig.po_ontology_mcp import handle_ontology_edit_term

    try:
        result = asyncio.run(
            handle_ontology_edit_term(
                project_path=path,
                term=term,
                definition=definition,
                examples=list(examples),
            )
        )
    except KeyError as exc:
        raise click.ClickException(str(exc).strip("'"))
    click.echo(f"updated term {result.term!r}")


@ontology_group.command("remove")
@click.argument("term")
@click.option(
    "--replace-with",
    "replacement_term",
    default=None,
    help=(
        "Replacement term — references in artifacts get rewritten in place. "
        "Without this flag references become orphaned."
    ),
)
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def ontology_remove_cmd(term: str, replacement_term: str | None, path: Path) -> None:
    """Remove a term from the ontology (with optional reference rewrite)."""
    from jig.po_ontology_mcp import handle_ontology_remove_term

    try:
        result = asyncio.run(
            handle_ontology_remove_term(
                project_path=path,
                term=term,
                replacement_term=replacement_term,
            )
        )
    except KeyError as exc:
        raise click.ClickException(str(exc).strip("'"))
    click.echo(f"removed term {result.term!r}")
    if result.replacement_term:
        click.echo(f"redirected references to {result.replacement_term!r}")
        for p in result.rewritten:
            click.echo(f"  rewrote {p}")
    elif result.orphaned:
        click.echo(f"orphaned {len(result.orphaned)} references:")
        for ref in result.orphaned:
            click.echo(f"  {ref.path}:{ref.line}: {ref.snippet}")


@ontology_group.command("find-references")
@click.argument("term")
@click.option(
    "--path",
    default=".",
    type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
def ontology_find_references_cmd(term: str, path: Path) -> None:
    """List every artifact line referencing ``term``."""
    from jig.po_ontology_mcp import handle_ontology_find_references

    refs = asyncio.run(handle_ontology_find_references(project_path=path, term=term))
    if not refs:
        click.echo(f"no references to {term!r}")
        return
    for ref in refs:
        click.echo(f"{ref.path}:{ref.line}: {ref.snippet}")


# ---- eval harness --------------------------------------------------------


@cli.group("eval")
def eval_group() -> None:
    """Eval harness — collect and compare metrics across jig runs."""


@eval_group.command("collect")
@click.argument("project_path", type=click.Path(exists=True, path_type=Path))
@click.option("--project-id", required=True, help="Eval project id (e.g. hn-cli).")
@click.option("--label", default=None, help="Human-readable label for this run.")
@click.option(
    "--tracer-cmd",
    "tracer_cmd",
    default=None,
    help="Shell command to run the tracer (e.g. 'bash tracer.sh'). "
    "Runs inside project_path.",
)
@click.option(
    "--runs-root",
    "runs_root",
    default=None,
    type=click.Path(path_type=Path),
    help="Directory where manifests are stored. Defaults to evals/runs/ "
    "relative to the jig source root.",
)
def eval_collect(
    project_path: Path,
    project_id: str,
    label: str | None,
    tracer_cmd: str | None,
    runs_root: Path | None,
) -> None:
    """Collect metrics from a completed project run and save a manifest.

    PROJECT_PATH is the root of the jig-managed project (the directory
    that contains .jig/).
    """
    import shlex
    import uuid

    import yaml

    from jig.eval.collector import collect

    run_id = str(uuid.uuid4())[:8]

    parsed_tracer: list[str] | None = None
    if tracer_cmd:
        parsed_tracer = shlex.split(tracer_cmd)

    manifest = asyncio.run(
        collect(
            project_path,
            run_id=run_id,
            project_id=project_id,
            label=label,
            tracer_cmd=parsed_tracer,
        )
    )

    # Determine runs root — prefer explicit flag, then walk up from here
    # to find the jig source tree's evals/runs/, otherwise use cwd.
    if runs_root is None:
        src_root = Path(__file__).resolve().parent.parent
        candidate = src_root / "evals" / "runs"
        runs_root = candidate if candidate.parent.exists() else Path("evals/runs")

    out_dir = runs_root / project_id / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "manifest.yaml"
    out_path.write_text(
        yaml.dump(manifest.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )

    click.echo(f"run_id:   {run_id}")
    click.echo(f"label:    {label or '(none)'}")
    click.echo(f"manifest: {out_path}")
    click.echo(f"tickets:  {manifest.ticket_status_counts}")
    click.echo(f"cost_usd: {manifest.total_cost_usd:.4f}")
    click.echo(
        f"spawns:   {manifest.agent_spawn_count}  fix_cycles: {manifest.fix_cycle_count}"
    )
    if manifest.tracer:
        status = "PASS" if manifest.tracer.passed else "FAIL"
        click.echo(f"tracer:   {status}")
    error_keys = [k for k, v in manifest.system_event_counts.items() if v]
    if error_keys:
        click.echo(f"errors:   {manifest.system_event_counts}")


@eval_group.command("compare")
@click.argument("project_id")
@click.option(
    "--before", "before_label", required=True, help="Label or run-id of baseline run."
)
@click.option(
    "--after", "after_label", required=True, help="Label or run-id of new run."
)
@click.option(
    "--runs-root",
    "runs_root",
    default=None,
    type=click.Path(path_type=Path),
    help="Directory where manifests are stored.",
)
def eval_compare(
    project_id: str,
    before_label: str,
    after_label: str,
    runs_root: Path | None,
) -> None:
    """Compare two eval runs and print a markdown table.

    PROJECT_ID is the eval project name (e.g. hn-cli).
    """

    from jig.eval.compare import compare_manifests, _find_manifest, _load_manifest

    if runs_root is None:
        src_root = Path(__file__).resolve().parent.parent
        candidate = src_root / "evals" / "runs"
        runs_root = candidate if candidate.parent.exists() else Path("evals/runs")

    before_path = _find_manifest(runs_root, project_id, before_label)
    after_path = _find_manifest(runs_root, project_id, after_label)

    before = _load_manifest(before_path)
    after = _load_manifest(after_path)

    click.echo(compare_manifests(before, after))


@eval_group.command("list")
@click.argument("project_id")
@click.option(
    "--runs-root",
    "runs_root",
    default=None,
    type=click.Path(path_type=Path),
    help="Directory where manifests are stored.",
)
def eval_list(project_id: str, runs_root: Path | None) -> None:
    """List all collected runs for a project."""
    import yaml

    if runs_root is None:
        src_root = Path(__file__).resolve().parent.parent
        candidate = src_root / "evals" / "runs"
        runs_root = candidate if candidate.parent.exists() else Path("evals/runs")

    base = runs_root / project_id
    if not base.exists():
        click.echo(f"no runs found for '{project_id}'")
        return

    rows = []
    for run_dir in sorted(base.iterdir()):
        m = run_dir / "manifest.yaml"
        if not m.exists():
            continue
        data = yaml.safe_load(m.read_text()) or {}
        rows.append(
            (
                data.get("run_id", run_dir.name),
                data.get("label") or "",
                data.get("collected_at", "")[:10],
                str(data.get("ticket_status_counts", {})),
                "PASS"
                if (data.get("tracer") or {}).get("passed")
                else ("FAIL" if data.get("tracer") else "n/a"),
            )
        )

    if not rows:
        click.echo(f"no manifests under {base}")
        return

    header = f"{'run_id':<10} {'label':<20} {'date':<12} {'tickets':<40} tracer"
    click.echo(header)
    click.echo("-" * len(header))
    for run_id, label, date, tickets, tracer in rows:
        click.echo(f"{run_id:<10} {label:<20} {date:<12} {tickets:<40} {tracer}")


# ---------------------------------------------------------------------------
# jig graph — dependency graph queries (Phase 3.8)
# ---------------------------------------------------------------------------


@cli.group("graph")
def graph_group() -> None:
    """Dependency graph queries — build, impact, neighbors, consumers, tracers."""


@graph_group.command("build")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def graph_build(path: Path) -> None:
    """Rebuild the dependency graph from spec artifacts."""
    from jig.graph.derive import write_graph

    out = write_graph(path)
    click.echo(f"graph written to {out}")


@graph_group.command("impact")
@click.argument("ticket_id")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--depth", default=1, show_default=True, help="Neighborhood depth.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def graph_impact(ticket_id: str, path: Path, depth: int, as_json: bool) -> None:
    """Show what a ticket touches and who consumes those nodes."""
    import json as _json

    from jig.graph.derive import build_graph, ticket_impact

    graph = build_graph(path)
    impact = ticket_impact(graph, ticket_id, depth=depth)
    if as_json:
        click.echo(_json.dumps(impact.model_dump(mode="json"), indent=2))
        return
    click.echo(f"ticket:       {impact.ticket_id}")
    click.echo(f"boundaries:   {impact.crossed_boundaries}")
    if impact.touched:
        click.echo("touched:")
        for n in impact.touched:
            click.echo(f"  {n.id}  ({n.kind})")
    if impact.consumers:
        click.echo("consumers:")
        for node_id, consumers in impact.consumers.items():
            for c in consumers:
                click.echo(f"  {c.id} → {node_id}")
    if impact.exercised_tracers:
        click.echo("exercised tracers:")
        for t in impact.exercised_tracers:
            click.echo(f"  {t.id}")


@graph_group.command("neighbors")
@click.argument("node_id")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option("--depth", default=1, show_default=True, help="Hops to walk.")
@click.option("--kind", default=None, help="Filter by node kind.")
def graph_neighbors(node_id: str, path: Path, depth: int, kind: str | None) -> None:
    """List outgoing neighbors of a node within DEPTH hops."""
    from jig.graph.derive import build_graph

    graph = build_graph(path)
    neighbor_ids = graph.neighbors(node_id, depth=depth, kind=kind)
    node_map = {n.id: n for n in graph.nodes}
    results = sorted(
        (node_map[nid] for nid in neighbor_ids if nid in node_map),
        key=lambda n: (n.kind, n.id),
    )
    if not results:
        click.echo("(no neighbors)")
        return
    for n in results:
        line = f"{n.id}  [{n.kind}]"
        if n.title:
            line += f"  — {n.title}"
        click.echo(line)


@graph_group.command("consumers")
@click.argument("node_id")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def graph_consumers(node_id: str, path: Path) -> None:
    """List all nodes with an edge pointing TO NODE_ID."""
    from jig.graph.derive import build_graph

    graph = build_graph(path)
    consumer_ids = graph.consumers_of(node_id)
    node_map = {n.id: n for n in graph.nodes}
    results = sorted(
        (node_map[nid] for nid in consumer_ids if nid in node_map),
        key=lambda n: (n.kind, n.id),
    )
    if not results:
        click.echo("(no consumers)")
        return
    for n in results:
        line = f"{n.id}  [{n.kind}]"
        if n.title:
            line += f"  — {n.title}"
        click.echo(line)


@graph_group.command("tracers")
@click.argument("node_id")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def graph_tracers(node_id: str, path: Path) -> None:
    """List tracer nodes reachable from NODE_ID."""
    from jig.graph.derive import build_graph

    graph = build_graph(path)
    reachable = graph.reachable_from(node_id)
    node_map = {n.id: n for n in graph.nodes}
    tracers = sorted(
        (
            node_map[nid]
            for nid in reachable
            if nid in node_map and node_map[nid].kind == "tracer"
        ),
        key=lambda n: n.id,
    )
    if not tracers:
        click.echo("(no tracers)")
        return
    for t in tracers:
        line = f"{t.id}"
        if t.title:
            line += f"  — {t.title}"
        click.echo(line)


# ---- tracer group (Phase 5.12) ------------------------------------------


@cli.group("tracer")
def tracer_group() -> None:
    """Manage tracer-bullet specs (.jig/spec/tracers/)."""


@tracer_group.command("list")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def tracer_list(path: Path) -> None:
    """List all authored tracers."""
    from jig.spec_loader import load_all_tracers

    tracers = load_all_tracers(path)
    if not tracers:
        click.echo("(no tracers authored)")
        return
    for tr in tracers:
        click.echo(f"{tr.id}  — {tr.description}")


@tracer_group.command("show")
@click.argument("tracer_id")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def tracer_show(tracer_id: str, path: Path) -> None:
    """Show a tracer spec in detail."""
    import yaml as _yaml
    from jig.spec_loader import load_tracer

    try:
        tr = load_tracer(path, tracer_id)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc
    click.echo(_yaml.safe_dump(tr.model_dump(mode="json"), sort_keys=False))


@tracer_group.command("run")
@click.argument("tracer_id")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def tracer_run(tracer_id: str, path: Path) -> None:
    """Run a tracer's smoke command and report pass/fail."""
    import subprocess
    from jig.spec_loader import load_tracer

    try:
        tr = load_tracer(path, tracer_id)
    except FileNotFoundError as exc:
        click.echo(str(exc), err=True)
        raise SystemExit(1) from exc

    click.echo(f"Running tracer {tr.id!r}: {' '.join(tr.command)}")
    try:
        result = subprocess.run(
            tr.command,
            cwd=str(path),
            timeout=tr.timeout_seconds,
            capture_output=False,
        )
    except subprocess.TimeoutExpired:
        click.echo(f"TIMEOUT after {tr.timeout_seconds}s", err=True)
        raise SystemExit(1)
    except FileNotFoundError as exc:
        click.echo(f"Command not found: {exc}", err=True)
        raise SystemExit(1)

    if result.returncode == 0:
        click.echo(f"PASS (exit {result.returncode})")
    else:
        click.echo(f"FAIL (exit {result.returncode})")
        raise SystemExit(result.returncode)


# ---- canonicalize --------------------------------------------------------


@cli.command("canonicalize")
@click.option(
    "--ticket-id",
    "ticket_id",
    default=None,
    help="Attach the canonicalize ticket as a child of this ticket.",
)
@click.option(
    "--sweep",
    is_flag=True,
    help="Sweep the whole repo instead of a specific change set.",
)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def canonicalize_cmd(ticket_id: str | None, sweep: bool, path: Path) -> None:
    """Create a ticket that runs the canonicalizer agent."""
    from jig.store.tickets import TicketStore
    from jig.ticket import Size, Ticket, TicketStatus, WorkType

    async def _run() -> str:
        store = TicketStore(path / ".jig" / "store" / "tickets.jsonl")
        await store.load()

        if sweep:
            title = "Canonicalize sweep (whole repo)"
            labels = ["sweep"]
        else:
            try:
                proc = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=str(path),
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                branch = proc.stdout.strip() or "HEAD"
            except Exception:
                branch = "HEAD"
            title = f"Canonicalize: {branch}"
            labels = []

        ticket = Ticket(
            title=title,
            description="",
            work_type=WorkType.CANONICALIZE,
            workflow="canonicalize",
            size=Size.S,
            status=TicketStatus.OPEN,
            parent_id=ticket_id,
            labels=labels,
            created_by="cli",
        )
        return await store.create(ticket)

    new_id = asyncio.run(_run())
    click.echo(new_id)


# ---- audit report --------------------------------------------------------


@cli.group("audit")
def audit_group() -> None:
    """Canonicalization audit reporting."""


@audit_group.command("report")
@click.option("--run-id", "run_id", default=None, help="Filter to a single run id.")
@click.option(
    "--ticket-id",
    "ticket_id",
    default=None,
    help="Filter to entries from a single ticket.",
)
@click.option(
    "--days",
    default=None,
    type=int,
    help="Filter to entries applied in the last N days.",
)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def audit_report(
    run_id: str | None,
    ticket_id: str | None,
    days: int | None,
    path: Path,
) -> None:
    """Print a summary of canonicalization audit entries."""
    from datetime import datetime, timedelta, timezone

    from jig.store.audit import AuditStore

    audit_path = path / ".jig" / "store" / "audit.jsonl"
    if not audit_path.is_file():
        click.echo("no audit entries found (.jig/store/audit.jsonl missing)")
        return

    async def _load() -> list:
        store = AuditStore(audit_path)
        await store.load()
        if run_id is not None:
            return await store.for_run(run_id)
        if ticket_id is not None:
            return await store.for_ticket(ticket_id)
        return await store.all()

    entries = asyncio.run(_load())

    if days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        entries = [e for e in entries if e.applied_at >= cutoff]

    if not entries:
        click.echo("no audit entries match the given filters")
        return

    by_rule: dict[str, dict[str, int]] = {}
    for e in entries:
        bucket = by_rule.setdefault(e.rule_id, {"files": set(), "count": 0})
        bucket["files"].add(e.file_path)  # type: ignore[union-attr]
        bucket["count"] += 1

    rule_w = max(8, max(len(r) for r in by_rule))
    click.echo(f"{'rule_id':<{rule_w}}  {'files':>5}  {'count':>5}")
    click.echo(f"{'-' * rule_w}  {'-----':>5}  {'-----':>5}")
    for rule, info in sorted(by_rule.items()):
        files = info["files"]
        count = info["count"]
        n_files = len(files) if isinstance(files, set) else int(files)
        click.echo(f"{rule:<{rule_w}}  {n_files:>5}  {count:>5}")


@audit_group.command("rules")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
def audit_rules(path: Path) -> None:
    """List active canonicalization rule sources for this project.

    Includes .jig/rules/semgrep/*.yml (semgrep-format rules) and
    .jig/rules/deprecations.yml (deprecations manifest, converted to
    semgrep rules at runtime via to_semgrep_rules()).
    """
    from jig.canonicalize import list_semgrep_rule_paths

    rule_paths = list_semgrep_rule_paths(path)
    if not rule_paths:
        click.echo(
            "no rule sources found (.jig/rules/semgrep/ is absent or empty, and .jig/rules/deprecations.yml is missing)"
        )
        return
    for p in rule_paths:
        click.echo(str(p.relative_to(path)))


@audit_group.command("coverage")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--fail/--no-fail",
    default=True,
    help="Exit 1 when undocumented rules are found (default: on).",
)
def audit_coverage(path: Path, fail: bool) -> None:
    """Check that every rule ID is mentioned in .jig/conventions.md.

    Rules not mentioned in conventions.md won't be seen by agents at task
    start — convention injection only injects the prose file, not the rule
    files themselves.  Exit code 1 when undocumented rules are found (use
    --no-fail to suppress for informational runs).
    """
    from jig.canonicalize import check_rule_coverage

    result = check_rule_coverage(path)
    if result["missing_conventions"]:
        for msg in result["missing_conventions"]:
            click.echo(f"warning: {msg}")
        return
    for msg in result.get("parse_errors", []):
        click.echo(f"warning: {msg}")
    undocumented = result["undocumented"]
    if not undocumented:
        click.echo("ok — all rule IDs are mentioned in .jig/conventions.md")
        return
    click.echo(f"{len(undocumented)} rule(s) not mentioned in .jig/conventions.md:")
    for rid in undocumented:
        click.echo(f"  {rid}")
    if fail:
        raise SystemExit(1)


@cli.group("validate", invoke_without_command=True)
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--ticket-id",
    default=None,
    help="Clean up a specific ticket's worktree. Without this flag, runs a catalog dry-run.",
)
@click.pass_context
def validate_group(ctx: click.Context, path: Path, ticket_id: str | None) -> None:
    """Validate the project catalog, or clean up a ticket's worktree.

    Without a subcommand: runs a catalog dry-run, or (with ``--ticket-id``) cleans
    up a specific ticket's worktree. Subcommands validate specific aspects.
    """
    if ctx.invoked_subcommand is None:
        _validate_impl(path, ticket_id)


@validate_group.command("conventions")
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--fail/--no-fail", default=True, help="Exit 1 on validation failure (default: on)."
)
def validate_conventions(path: Path, fail: bool) -> None:
    """Validate .jig/conventions.md is present, non-empty, and within the recommended size.

    Recommended maximum is 500 lines — longer files degrade agent context
    quality.  Missing or empty files prevent convention injection entirely.
    Exit code 1 on any error (use --no-fail for informational runs).
    """
    from jig.canonicalize import check_conventions

    errors = check_conventions(path)
    if not errors:
        click.echo("ok — .jig/conventions.md is valid")
        return
    for err in errors:
        click.echo(f"error: {err}")
    if fail:
        raise SystemExit(1)
