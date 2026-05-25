from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

from jig.models import RoleConfig
from jig.safe_path import safe_resolve_within
from jig.store import Message, MessageBus, MessageType
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import (
    Answer,
    Decision,
    Note,
    Question,
    SystemEvent,
    ThreadEntry,
)
from jig.ticket import Size, Ticket, TicketStatus, WorkType
from jig.worktree import LintError, commit_worktree

if TYPE_CHECKING:
    from jig.store.audit import AuditStore
    from jig.store.canon_issues import CanonicalizationIssueStore
    from jig.store.checkpoints import CheckpointStore

_logger = logging.getLogger(__name__)

_WRITABLE_KINDS = frozenset({"comment", "decision", "question", "answer"})

_CAPABILITY_URI_PREFIX = "project://spec/capabilities/"


def _maybe_materialize_ticket_spec(
    *,
    project_path: Path,
    ticket: Ticket,
) -> None:
    """Look up the ticket's capability and write its AC as a TicketSpec.

    Best-effort: every failure path returns without raising so the
    caller's ``publish_ticket_created`` still fires. An exception
    leaking out of here would orphan an already-persisted ticket
    (created but never dispatched).

    Skip paths and log levels:

    * ``debug`` for expected non-paths — non-feature work types and
      tickets whose ``derived_from`` doesn't target a capability URI.
    * ``debug`` when no project spec exists (typical for bare
      projects that haven't run ``jig init`` yet).
    * ``warning`` for likely misconfigurations — empty cap id, unknown
      cap id, materialised spec the work-type schema rejects, or any
      unexpected error while reading / writing the spec.
    """
    from jig.spec_loader import load_structured_spec
    from jig.specs import (
        SpecValidationError,
        materialize_ticket_spec_from_capability,
        save_ticket_spec,
    )

    if ticket.work_type != WorkType.FEATURE:
        _logger.debug(
            "ticket %s: work_type %s is not feature; skipping capability "
            "spec materialisation",
            ticket.id,
            ticket.work_type.value,
        )
        return

    uri = ticket.derived_from or ""
    if not uri.startswith(_CAPABILITY_URI_PREFIX):
        _logger.debug(
            "ticket %s: derived_from %r does not point at a capability; skipping spec",
            ticket.id,
            uri,
        )
        return

    cap_id = uri[len(_CAPABILITY_URI_PREFIX) :].strip("/")
    if not cap_id:
        _logger.warning(
            "ticket %s: derived_from %r has empty capability id", ticket.id, uri
        )
        return

    try:
        spec, _ = load_structured_spec(project_path)
    except FileNotFoundError:
        _logger.debug(
            "ticket %s: no project.structured.yaml; skipping spec materialisation",
            ticket.id,
        )
        return
    except Exception:  # noqa: BLE001 — best-effort enrichment must not orphan tickets
        # Malformed YAML, pydantic ValidationError on the structured
        # spec, permission denied — anything else surfaces here. We
        # log and skip so ticket dispatch still fires.
        _logger.warning(
            "ticket %s: failed to load project spec for materialisation",
            ticket.id,
            exc_info=True,
        )
        return

    capability = spec.capability_by_id_or_alias(cap_id)
    if capability is None:
        _logger.warning(
            "ticket %s: capability %r not found in project spec; skipping",
            ticket.id,
            cap_id,
        )
        return

    ticket_spec = materialize_ticket_spec_from_capability(
        ticket_id=ticket.id,
        work_type=ticket.work_type,
        size=ticket.size,
        capability=capability,
    )
    if not ticket_spec.fields.get("acceptance_criteria"):
        # The capability has no AC anywhere — neither top-level nor
        # per-behaviour. The materialised spec exists but reviewer-
        # test-adequacy will have nothing to check coverage against.
        # Surface the configuration gap so it's visible in logs.
        _logger.warning(
            "ticket %s: capability %r materialised with no acceptance_criteria "
            "(reviewer-test-adequacy will have nothing to gate on)",
            ticket.id,
            cap_id,
        )
    # ``enforce_required_fields=False`` because the capability only
    # carries M-level fields (summary / behaviors / acceptance_criteria
    # / out_of_scope). L/XL tickets need ``design`` and
    # ``technical_risks`` added through a proposal before the spec is
    # canonically "complete" for its size. The unknown-fields check
    # still runs — a custom feature schema that drops one of our four
    # fields will surface as a validation error here.
    try:
        save_ticket_spec(project_path, ticket_spec, enforce_required_fields=False)
    except SpecValidationError as exc:
        _logger.warning(
            "ticket %s: materialised spec failed work-type validation: %s",
            ticket.id,
            exc,
        )
    except Exception:  # noqa: BLE001 — best-effort enrichment must not orphan tickets
        # OSError (disk full, permission denied), unexpected
        # serialisation errors. Same contract as the load path: log,
        # skip, let dispatch proceed.
        _logger.warning(
            "ticket %s: failed to write materialised spec",
            ticket.id,
            exc_info=True,
        )


async def handle_create_ticket(
    *,
    tickets: TicketStore,
    bus: MessageBus,
    sender: str,
    args: dict,
    project_path: Path | None = None,
) -> str:
    depends_on: list[str] = args.get("depends_on", [])

    # Validate that dependency ticket IDs exist
    for dep_id in depends_on:
        if await tickets.get(dep_id) is None:
            raise KeyError(f"dependency ticket {dep_id} not found")

    # Accept either the new "work_type" or the legacy "type" kwarg.
    # If "type" is passed, the Ticket model validator handles migration
    # of legacy values (bug→bugfix, etc.) and sets workflow="thread" for
    # the old task/question values.
    raw_size = args.get("size", "m")
    try:
        size = Size(raw_size)
    except ValueError:
        raise ValueError(
            f"Unknown size {raw_size!r}. Valid values: {[s.value for s in Size]}"
        ) from None

    ticket_kwargs: dict = {
        "title": args["title"],
        "description": args.get("description", ""),
        "assignee": args.get("assignee"),
        "parent_id": args.get("parent_id"),
        "blocked_by": depends_on,
        "labels": args.get("labels", []),
        "created_by": sender,
        "size": size,
        "derived_from": args.get("derived_from"),
    }
    if "workflow" in args:
        ticket_kwargs["workflow"] = args["workflow"]
    if "work_type" in args:
        raw_wt = args["work_type"]
        try:
            ticket_kwargs["work_type"] = WorkType(raw_wt)
        except ValueError:
            raise ValueError(
                f"Unknown work_type {raw_wt!r}. "
                f"Valid values: {[w.value for w in WorkType]}"
            ) from None
    elif "type" in args:
        # Legacy alias — the model validator migrates bug→bugfix etc.
        # Anything that doesn't map cleanly surfaces as a pydantic
        # ValidationError from WorkType(...), which is fine for
        # forensics but not friendly — catch and rewrite it.
        from jig.ticket import _LEGACY_TYPE_MIGRATION

        raw_legacy = args["type"]
        mapped = _LEGACY_TYPE_MIGRATION.get(raw_legacy, raw_legacy)
        if mapped not in {w.value for w in WorkType}:
            raise ValueError(
                f"Unknown work_type {raw_legacy!r}. "
                f"Valid values: {[w.value for w in WorkType]} "
                f"(legacy accepted: {sorted(_LEGACY_TYPE_MIGRATION)})"
            )
        ticket_kwargs["type"] = raw_legacy
    else:
        raise KeyError("work_type is required")

    ticket = Ticket(**ticket_kwargs)

    # Phase 2D: if the caller didn't pin a workflow and the model's
    # legacy-migration validator didn't override (e.g. type=task→thread),
    # consult .jig/config.yaml. Explicit per-ticket overrides and legacy
    # "thread" migration both win over config-driven resolution.
    explicit_workflow = args.get("workflow")
    if (
        explicit_workflow is None
        and ticket.workflow == "default"
        and project_path is not None
    ):
        from jig.config import (
            WorkflowResolutionError,
            load_config,
            resolve_workflow,
        )

        try:
            cfg = load_config(project_path)
        except FileNotFoundError:
            cfg = None
        if cfg is not None:
            try:
                resolved = resolve_workflow(
                    cfg,
                    work_type=ticket.work_type.value,
                    size=ticket.size.value,
                )
            except WorkflowResolutionError:
                # Should only fire with explicit=..., which we don't
                # pass here. Raised defensively in case future code does.
                raise
            if resolved != ticket.workflow:
                ticket = ticket.model_copy(update={"workflow": resolved})
    elif explicit_workflow is not None and project_path is not None:
        # Validate an explicit workflow against config.workflows.available.
        from jig.config import WorkflowResolutionError, load_config, resolve_workflow

        try:
            cfg = load_config(project_path)
        except FileNotFoundError:
            cfg = None
        if cfg is not None:
            try:
                resolve_workflow(
                    cfg,
                    work_type=ticket.work_type.value,
                    size=ticket.size.value,
                    explicit=explicit_workflow,
                )
            except WorkflowResolutionError as exc:
                raise ValueError(str(exc)) from exc

    # Skip the store's on-create callback for this create. The
    # callback (when wired by ``wire_create_publisher`` or
    # ``Orchestrator.startup``) publishes the broadcast event for
    # paths that bypass this handler. Here we publish both topics
    # explicitly below, so the broadcast must not also fire from the
    # callback. ``fire_create_callback=False`` keeps the suppression
    # task-local — no shared mutable state across the await.
    ticket_id = await tickets.create(ticket, fire_create_callback=False)

    # Update the reverse side: each dependency now blocks this ticket
    for dep_id in depends_on:
        dep = await tickets.get(dep_id)
        if dep is not None and ticket_id not in dep.blocks:
            await tickets.update(dep_id, blocks=dep.blocks + [ticket_id])

    # Materialise a TicketSpec inline when the ticket is derived from a
    # project-spec capability. Best-effort — a failure here logs and
    # continues; the ticket is still created.
    if ticket.derived_from and project_path is not None:
        _maybe_materialize_ticket_spec(
            project_path=project_path,
            ticket=ticket,
        )

    # Publish both orchestrator (for dispatch) and broadcast (for TUI
    # subscribers). PM-driven create needs dispatch immediately —
    # without it the ticket sits unscheduled until something else
    # nudges the orchestrator.
    from jig.ticket_events import publish_ticket_created

    await publish_ticket_created(
        bus,
        ticket,
        sender=sender,
        depends_on=depends_on,
        for_dispatch=True,
    )
    return ticket_id


async def handle_read_ticket(*, tickets: TicketStore, ticket_id: str) -> Ticket:
    loaded = await tickets.get(ticket_id)
    if loaded is None:
        raise KeyError(f"ticket {ticket_id} not found")
    return loaded


async def handle_list_tickets(*, tickets: TicketStore, args: dict) -> list[Ticket]:
    # Accept both "work_type" and legacy "type" in filter args. Legacy
    # values (bug, chore, task, question) are migrated through the same
    # mapping as the model validator so old callers keep working.
    raw_work_type = args.get("work_type", args.get("type"))
    if raw_work_type is None:
        wt = None
    else:
        from jig.ticket import _LEGACY_TYPE_MIGRATION

        mapped = _LEGACY_TYPE_MIGRATION.get(raw_work_type, raw_work_type)
        wt = WorkType(mapped)
    status = TicketStatus(args["status"]) if "status" in args else None
    assignee = args.get("assignee")
    parent_id = args.get("parent_id")

    if assignee is not None:
        pool = await tickets.find_by_assignee(assignee)
    elif parent_id is not None:
        pool = await tickets.find_by_parent(parent_id)
    else:
        pool = await tickets.list_all()

    def keep(t: Ticket) -> bool:
        if wt is not None and t.work_type != wt:
            return False
        if status is not None and t.status != status:
            return False
        return True

    return [t for t in pool if keep(t)]


async def handle_read_comments(
    *, threads: ThreadStore, ticket_id: str, kind: str | None = None
) -> list[ThreadEntry]:
    """Return thread entries for a ticket, oldest first.

    Legacy-API name kept for call-site compatibility during Phase 4.
    ``kind`` filters on the new ``ThreadEntry.kind`` discriminator;
    pre-Phase-4 callers who asked for ``commit`` / ``phase_run`` /
    ``status_change`` should now pass ``system_event`` (all three fold
    into that kind, distinguished by ``event_type``).
    """
    all_for = await threads.for_ticket(ticket_id)
    if kind is None:
        return all_for
    return [e for e in all_for if e.kind == kind]


async def handle_comment_on_ticket(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    sender_cfg: RoleConfig | None,
    args: dict,
) -> str:
    kind = args.get("kind", "comment")
    if kind not in _WRITABLE_KINDS:
        raise ValueError(
            f"kind {kind!r} is reserved for system primitives; "
            f"agents may only write {sorted(_WRITABLE_KINDS)}"
        )

    ticket_id = args["ticket_id"]
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {ticket_id} not found")

    content = args["content"]
    entry: ThreadEntry
    if kind == "comment":
        entry = Note(ticket_id=ticket_id, author=sender, text=content)
    elif kind == "decision":
        entry = Decision(
            ticket_id=ticket_id,
            author=sender,
            decision=content,
            rationale="",
        )
    elif kind == "question":
        entry = Question(
            ticket_id=ticket_id,
            author=sender,
            target=args.get("target", "any_human"),
            question=content,
            blocking=bool(args.get("blocking", False)),
        )
    elif kind == "answer":
        question_id = args.get("question_id") or await _latest_open_question_id(
            threads, ticket_id
        )
        if not question_id:
            raise ValueError(
                "answer kind requires question_id (or an open Question on the ticket)"
            )
        entry = Answer(
            ticket_id=ticket_id,
            author=sender,
            question_id=question_id,
            text=content,
        )
    else:  # pragma: no cover — guarded by _WRITABLE_KINDS
        raise ValueError(f"unhandled writable kind {kind!r}")

    cid = await threads.post(entry)

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "comment_posted",
                "ticket_id": ticket_id,
                "comment_id": cid,
                "author": sender,
                "content": content,
                "comment_kind": kind,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return cid


async def _latest_open_question_id(threads: ThreadStore, ticket_id: str) -> str | None:
    """Return the id of the most recent unresolved Question on the ticket."""
    questions = await threads.find_by_kind(ticket_id, "question")
    open_qs = [q for q in questions if not q.is_resolved()]
    if not open_qs:
        return None
    return open_qs[-1].id


async def handle_update_ticket(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    args: dict,
) -> Ticket:
    ticket_id = args.pop("ticket_id")
    before = await tickets.get(ticket_id)
    if before is None:
        raise KeyError(f"ticket {ticket_id} not found")

    update_fields: dict = {}
    for key, value in args.items():
        if key == "status":
            update_fields["status"] = TicketStatus(value)
        else:
            update_fields[key] = value

    updated = await tickets.update(ticket_id, **update_fields)

    # Auto-emit status_change audit record on status transitions
    if "status" in update_fields and update_fields["status"] != before.status:
        await threads.post(
            SystemEvent(
                ticket_id=ticket_id,
                author=sender,
                event_type="status_change",
                content=f"status {before.status.value} -> {updated.status.value}",
            )
        )

    # Agents set "resolved" to signal phase completion, but only the
    # orchestrator should broadcast resolved/failed to the TUI — otherwise the
    # UI flickers "resolved" between workflow phases.  We still publish to the
    # bus (so the agent runner detects the terminal status), but mark it
    # internal so the emitter relay skips it.
    internal = (
        sender not in ("orchestrator", "user")
        and "status" in update_fields
        and update_fields["status"] in (TicketStatus.RESOLVED, TicketStatus.FAILED)
    )
    update_payload = {
        "kind": "ticket_updated",
        "ticket_id": ticket_id,
        "status": updated.status.value,
        **({"_internal": True} if internal else {}),
    }
    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload=update_payload,
            topic=f"tickets.{ticket_id}",
        )
    )
    # Also notify the orchestrator so it can react to status changes
    # (e.g. re-enqueue a ticket reset to "open" for retry).
    await bus.publish(
        Message(
            sender=sender,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload=update_payload,
            topic="orchestrator",
        )
    )
    return updated


async def handle_commit_progress(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    sender: str,
    worktree_path: Path,
    args: dict,
    checkpoints: "CheckpointStore | None" = None,
    phase_name: str = "",
) -> dict:
    ticket_id = args["ticket_id"]
    agent_message = args["message"]
    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {ticket_id} not found")

    # Build a conventional commit: feat(role): agent's description
    # Fall back to ticket title if the agent just passed the ticket ID or empty text.
    subject = agent_message.strip()
    if not subject or subject == ticket_id:
        subject = ticket.title
    # Truncate subject to conventional commit length
    if len(subject) > 72:
        subject = subject[:69] + "..."
    commit_message = f"feat({sender}): {subject}"

    try:
        sha = await commit_worktree(worktree_path, commit_message)
    except LintError as exc:
        # Harness-triggered: lint/test hook fires regardless of outcome
        # per doc 09. The failing lint output surfaces as open_questions
        # on the resulting checkpoint so the next agent view sees what's
        # red.
        if checkpoints is not None:
            from jig.checkpoint_mcp import record_auto_test_checkpoint

            await record_auto_test_checkpoint(
                checkpoints=checkpoints,
                ticket_id=ticket_id,
                phase_name=phase_name,
                author=sender,
                passed=False,
                summary=f"{len(exc.errors)} unfixable lint errors",
                open_questions=exc.errors,
            )
        return {
            "success": False,
            "error": "lint_errors",
            "message": "Fix these lint errors before committing:",
            "errors": exc.errors,
        }

    if checkpoints is not None:
        # Lint passed (commit_worktree gets past the LintError check).
        from jig.checkpoint_mcp import record_auto_test_checkpoint

        await record_auto_test_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name=phase_name,
            author=sender,
            passed=True,
            summary="ruff clean",
        )

    if sha is None:
        return {"sha": None, "comment_id": None}

    cid = await threads.post(
        SystemEvent(
            ticket_id=ticket_id,
            author=sender,
            event_type="commit",
            content=commit_message,
            commit_sha=sha,
        )
    )

    if checkpoints is not None:
        from jig.checkpoint_mcp import record_auto_commit_checkpoint

        await record_auto_commit_checkpoint(
            checkpoints=checkpoints,
            ticket_id=ticket_id,
            phase_name=phase_name,
            author=sender,
            commit_sha=sha,
            message=commit_message,
        )

    await bus.publish(
        Message(
            sender=sender,
            to="broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "commit_recorded",
                "ticket_id": ticket_id,
                "sha": sha,
                "message": commit_message,
            },
            topic=f"tickets.{ticket_id}",
        )
    )
    return {"sha": sha, "comment_id": cid}


async def handle_record_learning(
    *,
    memory: MemoryStore,
    role: str,
    args: dict,
) -> str:
    roles: list[str] = args.get("roles") or [role]
    invalid = [r for r in roles if not isinstance(r, str) or not r.strip()]
    if invalid:
        raise ValueError(
            f"Invalid roles entries: {invalid!r}. Each role must be a non-empty string."
        )
    await memory.add_role_learning(roles=roles, content=args["content"])
    return f"learning recorded for {', '.join(roles)}"


async def handle_request_context(
    *,
    worktree_path: Path,
    args: dict,
) -> str:
    """Read a file under the agent's worktree.

    The agent supplies ``args["path"]`` over MCP. Without containment
    a malicious agent could read arbitrary host files via ``../``
    traversal, absolute paths, or symlinks pointing outside the
    worktree. ``safe_resolve_within`` enforces all three.
    """
    requested = args["path"]
    try:
        target = safe_resolve_within(worktree_path, requested)
    except ValueError as exc:
        return f"Invalid path {requested!r}: {exc}"
    if not target.is_file():
        return f"File not found: {requested}"
    try:
        return target.read_text()
    except Exception as exc:
        return f"Error reading {requested}: {exc}"


# Maps package_manager values to their add-dependency commands.
# JS package managers get ``--ignore-scripts`` so postinstall code
# can't execute under the orchestrator process. Python managers
# already use PEP 517/518 build isolation; supply-chain risk there
# is intrinsic to package install rather than fixable by a flag.
_PKG_COMMANDS: dict[str, list[str]] = {
    "uv": ["uv", "add"],
    "pip": ["pip", "install"],
    "poetry": ["poetry", "add"],
    "npm": ["npm", "install", "--ignore-scripts"],
    "yarn": ["yarn", "add", "--ignore-scripts"],
    "pnpm": ["pnpm", "add", "--ignore-scripts"],
    "bun": ["bun", "add", "--ignore-scripts"],
}

# Env vars passed through to the package-manager subprocess.
# Anything else (notably CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY,
# JIG_*, GIT_*, GH_TOKEN) is dropped so a malicious package can't
# exfiltrate orchestrator secrets via postinstall or build hooks.
_PKG_ENV_ALLOWLIST: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TERM",
        "TMPDIR",
        "SHELL",
    }
)


def _scrubbed_env(worktree_path: Path) -> dict[str, str]:
    """Allowlist environment for package-manager subprocesses."""

    env = {k: v for k, v in os.environ.items() if k in _PKG_ENV_ALLOWLIST}
    env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    env.setdefault("LANG", "C.UTF-8")
    env["PWD"] = str(worktree_path)
    return env


async def handle_add_dependency(
    *,
    worktree_path: Path,
    package_manager: str,
    args: dict,
) -> dict:
    """Install one or more packages using the project's package manager.

    args:
        packages: list[str] — package specifiers (e.g. ["requests", "pydantic>=2"])
        dev: bool (default False) — install as dev dependency
    """
    packages: list[str] = args.get("packages", [])
    if not packages:
        raise ValueError("at least one package name is required")

    base = _PKG_COMMANDS.get(package_manager)
    if base is None:
        raise ValueError(
            f"unknown package_manager {package_manager!r}; "
            f"supported: {sorted(_PKG_COMMANDS)}"
        )

    cmd = list(base)
    dev = args.get("dev", False)
    if dev:
        dev_flags: dict[str, list[str]] = {
            "uv": ["--dev"],
            "pip": [],  # pip has no dev concept
            "poetry": ["--group", "dev"],
            "npm": ["--save-dev"],
            "yarn": ["--dev"],
            "pnpm": ["--save-dev"],
            "bun": ["--dev"],
        }
        cmd.extend(dev_flags.get(package_manager, []))
    cmd.extend(packages)

    _logger.info("add_dependency: running %s in %s", cmd, worktree_path)
    # Args are passed directly (no shell), and env is allowlisted so
    # install-time hooks can't exfiltrate orchestrator secrets.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=_scrubbed_env(worktree_path),
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").strip()

    if proc.returncode != 0:
        _logger.warning("add_dependency failed (rc=%d): %s", proc.returncode, output)
        return {"success": False, "output": output}

    _logger.info("add_dependency succeeded: %s", packages)
    return {"success": True, "packages": packages, "output": output}


async def handle_log_audit_entry(
    *,
    store: "AuditStore",
    ticket_id: str,
    run_id: str,
    rule_id: str,
    rule_source: str,
    file_path: str,
    before_hash: str,
    after_hash: str,
) -> str:
    """Persist an AuditEntry recording a rule applying a fix to a file."""
    from jig.store.audit import AuditEntry

    entry = AuditEntry(
        run_id=run_id,
        rule_id=rule_id,
        rule_source=rule_source,  # type: ignore[arg-type]
        file_path=file_path,
        before_hash=before_hash,
        after_hash=after_hash,
        ticket_id=ticket_id,
    )
    return await store.append(entry)


async def handle_create_canonicalization_issue(
    *,
    store: "CanonicalizationIssueStore",
    project_path: Path,
    ticket_id: str,
    issue_type: str,
    rule_id: str,
    rule_message: str,
    diff_hunk: str,
    file_path: str,
    suggested_fixes: list[str],
) -> str:
    """Persist a CanonicalizationIssue with routing resolved from config."""
    from jig.canonicalize import load_escalation_config, resolve_route

    esc = load_escalation_config(project_path)
    route = resolve_route(esc, rule_id, issue_type)
    from jig.store.canon_issues import CanonicalizationIssue

    issue = CanonicalizationIssue(
        type=issue_type,  # type: ignore[arg-type]
        rule_id=rule_id,
        rule_message=rule_message,
        diff_hunk=diff_hunk,
        file_path=file_path,
        ticket_id=ticket_id,
        suggested_fixes=suggested_fixes,
        routing=route,  # type: ignore[arg-type]
    )
    return await store.append(issue)
