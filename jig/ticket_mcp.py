from jig.models import AgentTypeConfig
from jig.store import Message, MessageBus, MessageType
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import Comment, Ticket, TicketStatus, TicketType

_WRITABLE_KINDS = frozenset({"comment", "decision"})


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
        payload={"kind": "ticket_created", "ticket_id": ticket_id},
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

    # Allowlist check — only applies when sender_cfg is provided (agents).
    # The orchestrator and user pass sender_cfg=None to bypass.
    if sender_cfg is not None and ticket.assignee:
        target = ticket.assignee
        allowed = set(sender_cfg.can_message) | {"orchestrator", sender_cfg.role}
        if target not in allowed:
            raise PermissionError(
                f"role {sender_cfg.role!r} is not allowed to message "
                f"role {target!r} (can_message={sender_cfg.can_message})"
            )

    comment = Comment(
        ticket_id=ticket_id,
        author=sender,
        content=args["content"],
        kind=kind,
    )
    cid = await comments.post(comment)

    await bus.publish(Message(
        sender=sender,
        to=ticket.assignee or "broadcast",
        type=MessageType.CONTEXT_UPDATE,
        payload={
            "kind": "comment_posted",
            "ticket_id": ticket_id,
            "comment_id": cid,
            "author": sender,
            "content": args["content"],
        },
        topic=f"tickets.{ticket_id}",
    ))
    return cid


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
    return updated
