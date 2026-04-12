from pathlib import Path

from jig.models import AgentTypeConfig
from jig.store import Message, MessageBus, MessageType
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import Comment, Ticket, TicketStatus, TicketType
from jig.worktree import commit_worktree

_WRITABLE_KINDS = frozenset({"comment", "decision", "question", "answer"})


async def handle_create_ticket(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    bus: MessageBus,
    sender: str,
    args: dict,
) -> str:
    ticket = Ticket(
        type=TicketType(args["type"]),
        title=args["title"],
        description=args.get("description", ""),
        assignee=args.get("assignee"),
        parent_id=args.get("parent_id"),
        labels=args.get("labels", []),
        created_by=sender,
    )
    ticket_id = await tickets.create(ticket)
    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "ticket_created",
            "ticket_id": ticket_id,
            "title": ticket.title,
            "description": ticket.description,
            "type": ticket.type.value,
            "assignee": ticket.assignee,
            "parent_id": ticket.parent_id,
        },
        topic="orchestrator",
    ))
    # Also publish to the ticket-specific topic so subscribers see it:
    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "ticket_created",
            "ticket_id": ticket_id,
            "title": ticket.title,
            "description": ticket.description,
            "type": ticket.type.value,
            "assignee": ticket.assignee,
            "parent_id": ticket.parent_id,
        },
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
    ttype = TicketType(args["type"]) if "type" in args else None
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
        if ttype is not None and t.type != ttype:
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
    sender_cfg: AgentTypeConfig | None,
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
    # observations, not messaging the assignee.  The can_message restriction
    # applies to create_ticket (which directs work to another role), not to
    # comments which are read-only context.

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
    await bus.publish(Message(
        sender=sender,
        to="broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "ticket_updated",
            "ticket_id": ticket_id,
            "status": updated.status.value,
            **({"_internal": True} if internal else {}),
        },
        topic=f"tickets.{ticket_id}",
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
    message = args["message"]
    if await tickets.get(ticket_id) is None:
        raise KeyError(f"ticket {ticket_id} not found")

    sha = await commit_worktree(worktree_path, message)
    if sha is None:
        return {"sha": None, "comment_id": None}

    cid = await comments.post(Comment(
        ticket_id=ticket_id,
        author=sender,
        content=message,
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
            "message": message,
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
