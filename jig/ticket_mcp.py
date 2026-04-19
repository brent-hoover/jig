import asyncio
import logging
from pathlib import Path

from jig.models import RoleConfig
from jig.store import Message, MessageBus, MessageType
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import Comment, Size, Ticket, TicketStatus, WorkType
from jig.worktree import LintError, commit_worktree

_logger = logging.getLogger(__name__)

_WRITABLE_KINDS = frozenset({"comment", "decision", "question", "answer"})


async def handle_create_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    args: dict,
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
    ticket_kwargs: dict = {
        "title": args["title"],
        "description": args.get("description", ""),
        "assignee": args.get("assignee"),
        "parent_id": args.get("parent_id"),
        "blocked_by": depends_on,
        "labels": args.get("labels", []),
        "created_by": sender,
        "size": Size(args.get("size", "m")),
    }
    if "workflow" in args:
        ticket_kwargs["workflow"] = args["workflow"]
    if "work_type" in args:
        ticket_kwargs["work_type"] = WorkType(args["work_type"])
    elif "type" in args:
        ticket_kwargs["type"] = args["type"]
    else:
        raise KeyError("work_type is required")

    ticket = Ticket(**ticket_kwargs)
    ticket_id = await tickets.create(ticket)

    # Update the reverse side: each dependency now blocks this ticket
    for dep_id in depends_on:
        dep = await tickets.get(dep_id)
        if dep is not None and ticket_id not in dep.blocks:
            await tickets.update(dep_id, blocks=dep.blocks + [ticket_id])

    payload = {
        "kind": "ticket_created",
        "ticket_id": ticket_id,
        "title": ticket.title,
        "description": ticket.description,
        "work_type": ticket.work_type.value,
        # Legacy alias for subscribers not yet updated to the Phase 1
        # schema. Remove once TUI + any other consumers land on work_type.
        "type": ticket.work_type.value,
        "size": ticket.size.value,
        "assignee": ticket.assignee,
        "parent_id": ticket.parent_id,
        "depends_on": depends_on,
        "workflow": ticket.workflow,
    }
    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload=payload,
        topic="orchestrator",
    ))
    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload=payload,
        topic=f"tickets.{ticket_id}",
    ))
    return ticket_id


async def handle_read_ticket(*, tickets: TicketStore, ticket_id: str) -> Ticket:
    loaded = await tickets.get(ticket_id)
    if loaded is None:
        raise KeyError(f"ticket {ticket_id} not found")
    return loaded


async def handle_list_tickets(
    *, tickets: TicketStore, args: dict
) -> list[Ticket]:
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
    *, comments: CommentStore, ticket_id: str, kind: str | None = None
) -> list[Comment]:
    all_for = await comments.for_ticket(ticket_id)
    if kind is None:
        return all_for
    return [c for c in all_for if c.kind == kind]


async def handle_comment_on_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
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

    # Commenting on a ticket is always allowed — the agent is posting its own
    # observations, not messaging the assignee. Cross-role messaging policy
    # (if any) will land with the capability-policy layer in Phase 5.

    comment = Comment(
        ticket_id=ticket_id,
        author=sender,
        content=args["content"],
        kind=kind,
    )
    cid = await comments.post(comment)

    await bus.publish(Message(
        sender=sender,
        to="broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "comment_posted",
            "ticket_id": ticket_id,
            "comment_id": cid,
            "author": sender,
            "content": args["content"],
            "comment_kind": kind,
        },
        topic=f"tickets.{ticket_id}",
    ))
    return cid


async def handle_ask_question(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    args: dict,
) -> dict:
    """Post one or more questions on a ticket and set it to needs_info.

    Returns {"comment_ids": [...], "status": "needs_info"}.
    """
    ticket_id = args["ticket_id"]
    questions: list[str] = args.get("questions", [])
    # Also accept a single "question" string for convenience
    if "question" in args and isinstance(args["question"], str):
        questions.append(args["question"])
    if not questions:
        raise ValueError("at least one question is required")

    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {ticket_id} not found")

    comment_ids: list[str] = []
    for q in questions:
        comment = Comment(
            ticket_id=ticket_id,
            author=sender,
            content=q,
            kind="question",
        )
        cid = await comments.post(comment)
        comment_ids.append(cid)
        await bus.publish(Message(
            sender=sender,
            to=ticket.assignee or "broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "comment_posted",
                "ticket_id": ticket_id,
                "comment_id": cid,
                "author": sender,
                "content": q,
                "comment_kind": "question",
            },
            topic=f"tickets.{ticket_id}",
        ))

    # Transition to needs_info
    before_status = ticket.status
    updated = await tickets.update(ticket_id, status=TicketStatus.NEEDS_INFO)
    if before_status != TicketStatus.NEEDS_INFO:
        await comments.post(Comment(
            ticket_id=ticket_id,
            author=sender,
            content=f"status {before_status.value} -> needs_info",
            kind="status_change",
        ))
    await bus.publish(Message(
        sender=sender,
        to=updated.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "ticket_updated",
            "ticket_id": ticket_id,
            "status": "needs_info",
        },
        topic=f"tickets.{ticket_id}",
    ))

    return {"comment_ids": comment_ids, "status": "needs_info"}


async def handle_answer_questions(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    args: dict,
) -> dict:
    """Post answers to pending questions and optionally resume the ticket.

    args:
        ticket_id: str
        answers: list[str]         — one answer per pending question, in order
        resume: bool (default True) — set ticket back to in_progress
    """
    ticket_id = args["ticket_id"]
    answers: list[str] = args.get("answers", [])
    resume: bool = args.get("resume", True)

    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {ticket_id} not found")

    comment_ids: list[str] = []
    for a in answers:
        comment = Comment(
            ticket_id=ticket_id,
            author=sender,
            content=a,
            kind="answer",
        )
        cid = await comments.post(comment)
        comment_ids.append(cid)
        await bus.publish(Message(
            sender=sender,
            to=ticket.assignee or "broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "comment_posted",
                "ticket_id": ticket_id,
                "comment_id": cid,
                "author": sender,
                "content": a,
                "comment_kind": "answer",
            },
            topic=f"tickets.{ticket_id}",
        ))

    result: dict = {"comment_ids": comment_ids}

    if resume and ticket.status == TicketStatus.NEEDS_INFO:
        updated = await tickets.update(ticket_id, status=TicketStatus.IN_PROGRESS)
        await comments.post(Comment(
            ticket_id=ticket_id,
            author=sender,
            content=f"status needs_info -> {updated.status.value}",
            kind="status_change",
        ))
        await bus.publish(Message(
            sender=sender,
            to=updated.assignee or "broadcast",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "ticket_updated",
                "ticket_id": ticket_id,
                "status": updated.status.value,
            },
            topic=f"tickets.{ticket_id}",
        ))
        result["status"] = updated.status.value
    else:
        result["status"] = ticket.status.value

    return result


async def handle_update_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
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

    # Auto-emit status_change comment on status transitions
    if "status" in update_fields and update_fields["status"] != before.status:
        await comments.post(Comment(
            ticket_id=ticket_id,
            author=sender,
            content=f"status {before.status.value} -> {updated.status.value}",
            kind="status_change",
        ))

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
    await bus.publish(Message(
        sender=sender,
        to="broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload=update_payload,
        topic=f"tickets.{ticket_id}",
    ))
    # Also notify the orchestrator so it can react to status changes
    # (e.g. re-enqueue a ticket reset to "open" for retry).
    await bus.publish(Message(
        sender=sender,
        to="orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload=update_payload,
        topic="orchestrator",
    ))
    return updated


async def handle_commit_progress(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    worktree_path: Path,
    args: dict,
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
        return {
            "success": False,
            "error": "lint_errors",
            "message": "Fix these lint errors before committing:",
            "errors": exc.errors,
        }
    if sha is None:
        return {"sha": None, "comment_id": None}

    cid = await comments.post(Comment(
        ticket_id=ticket_id,
        author=sender,
        content=commit_message,
        kind="commit",
        commit_sha=sha,
    ))

    await bus.publish(Message(
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
    ))
    return {"sha": sha, "comment_id": cid}


async def handle_record_learning(
    *,
    memory: MemoryStore,
    role: str,
    args: dict,
) -> str:
    await memory.add_role_learning(role=role, content=args["content"])
    return f"learning recorded for {role}"


async def handle_request_context(
    *,
    worktree_path: Path,
    args: dict,
) -> str:
    target = worktree_path / args["path"]
    if not target.is_file():
        return f"File not found: {args['path']}"
    try:
        return target.read_text()
    except Exception as exc:
        return f"Error reading {args['path']}: {exc}"


# Maps package_manager values to their add-dependency commands.
# The package names are appended as extra args.
_PKG_COMMANDS: dict[str, list[str]] = {
    "uv": ["uv", "add"],
    "pip": ["pip", "install"],
    "poetry": ["poetry", "add"],
    "npm": ["npm", "install"],
    "yarn": ["yarn", "add"],
    "pnpm": ["pnpm", "add"],
    "bun": ["bun", "add"],
}


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
    # Using create_subprocess_exec (not shell) to avoid injection —
    # each arg is passed directly to the process.
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(worktree_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    stdout, _ = await proc.communicate()
    output = stdout.decode(errors="replace").strip()

    if proc.returncode != 0:
        _logger.warning("add_dependency failed (rc=%d): %s", proc.returncode, output)
        return {"success": False, "output": output}

    _logger.info("add_dependency succeeded: %s", packages)
    return {"success": True, "packages": packages, "output": output}
