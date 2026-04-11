from jig.store import Message, MessageBus, MessageType
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, TicketType


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
) -> list:
    all_for = await comments.for_ticket(ticket_id)
    if kind is None:
        return all_for
    return [c for c in all_for if c.kind == kind]
