from jig.store import Message, MessageBus, MessageType
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketType


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
