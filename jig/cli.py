"""Jig CLI."""

import asyncio
import shutil
import subprocess
from pathlib import Path

import click

from jig.events import EventEmitter
from jig.ws_server import WebSocketServer
from jig.orchestrator import Orchestrator
from jig.worktree import remove_worktree


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
def init(name: str, force: bool) -> None:
    """Initialize a new jig project: brief → spec → architecture → scaffold."""
    import asyncio

    from jig.init_workflow import run_init

    asyncio.run(run_init(name=name, force=force))


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--ws-port",
    default=9100,
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

    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(
            f"Jig not initialized in {path}. Run 'jig init' first."
        )

    # Fail-loud catalog validation (Phase 2F). Unknown role / workflow /
    # check references, malformed YAML, and missing required context
    # artifacts all surface here before any loop starts.
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


@cli.command()
@click.option("--path", default=".", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--ticket-id",
    default=None,
    help="Clean up a specific ticket's worktree. Without this flag, runs a catalog dry-run.",
)
def validate(path: Path, ticket_id: str | None) -> None:
    """Validate the project catalog, or clean up a ticket's worktree.

    Without ``--ticket-id``: walks roles, workflows, config, and the
    check catalog. Reports every inconsistency and exits non-zero if
    anything is wrong. Same checks ``jig start`` runs at boot, but
    safe to run on a stopped service.

    With ``--ticket-id``: the legacy per-ticket cleanup (removes the
    worktree directory for that ticket).
    """
    jig_dir = path / ".jig"
    if not jig_dir.is_dir():
        raise click.ClickException(
            f"Jig not initialized in {path}. Run 'jig init' first."
        )

    if ticket_id is not None:
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
                    subprocess.run(
                        ["git", "worktree", "remove", "--force", wt],
                        cwd=path,
                        capture_output=True,
                    )
        except Exception:
            pass

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

_DEFAULT_WS_URL = "ws://127.0.0.1:9100"
_WORK_TYPES = ["feature", "bugfix", "refactor", "spike", "perf", "migration", "docs"]


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
            e
            for e in events
            if e.source != StorySource.log or e.level != "DEBUG"
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
        src_tag = "T" if ev.source == StorySource.thread else "L"
        click.echo(
            f"{ev.ts.strftime('%H:%M:%S.%f')[:12]} "
            f"(+{elapsed:7.2f}s) [{src_tag}] "
            f"{ev.level:5s} {ev.message}"
        )
