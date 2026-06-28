"""Substrate bones — TypedEvent schema (Epic 2, task 3).

Typed events replace magic-string bus topics + payload ``kind`` discriminators
with pydantic types. Each renders a legacy ``Message`` whose payload matches
what the current publishers emit (``jig/ticket_events.py``,
``Orchestrator._update_ticket_status``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.store.bus import Message, MessageType
from jig.substrate.events import (
    TicketCreated,
    TicketUpdated,
    TypedEvent,
    decode_event,
    ticket_topic,
)


def test_ticket_events_carry_their_existing_kind() -> None:
    assert (
        TicketCreated(
            ticket_id="jig-1", title="T", work_type="feature", size="s", status="open"
        ).kind
        == "ticket_created"
    )
    assert TicketUpdated(ticket_id="jig-1", status="open").kind == "ticket_updated"


def test_lifecycle_events_use_context_update_not_status() -> None:
    msg = TicketUpdated(ticket_id="jig-1", status="open").to_message()
    assert msg.type == MessageType.CONTEXT_UPDATE


def test_ticket_topic_helper_builds_per_ticket_topic() -> None:
    assert ticket_topic("jig-1") == "tickets.jig-1"


def test_ticket_events_default_to_the_per_ticket_topic() -> None:
    # A bare event routes to the broad audience (TUI + agent subscribers),
    # matching the real publishers — NOT the orchestrator topic.
    assert TicketUpdated(ticket_id="jig-1", status="open").topic == "tickets.jig-1"
    assert (
        TicketCreated(
            ticket_id="jig-1", title="T", work_type="feature", size="s", status="open"
        ).topic
        == "tickets.jig-1"
    )


def test_orchestrator_dispatch_copy_is_explicit() -> None:
    msg = TicketUpdated(
        ticket_id="jig-1", status="open", topic="orchestrator"
    ).to_message()
    assert msg.topic == "orchestrator"


def test_ticket_updated_payload_is_faithful() -> None:
    msg = TicketUpdated(ticket_id="jig-1", status="in_progress").to_message()
    assert msg.payload == {
        "kind": "ticket_updated",
        "ticket_id": "jig-1",
        "status": "in_progress",
    }


def test_ticket_updated_omits_internal_marker_by_default() -> None:
    payload = TicketUpdated(ticket_id="jig-1", status="resolved").to_message().payload
    assert "_internal" not in payload


def test_ticket_updated_emits_internal_marker_when_set() -> None:
    """Agent-side terminal updates carry ``_internal`` so the TUI relay skips
    them (matches ``ticket_mcp``'s conditional marker)."""
    msg = TicketUpdated(
        ticket_id="jig-1", status="resolved", internal=True
    ).to_message()
    assert msg.payload == {
        "kind": "ticket_updated",
        "ticket_id": "jig-1",
        "status": "resolved",
        "_internal": True,
    }


def test_ticket_created_payload_matches_build_payload_shape() -> None:
    """Payload must match ``jig.ticket_events._build_payload`` field-for-field,
    including the legacy ``type`` alias of ``work_type``."""
    msg = TicketCreated(
        ticket_id="jig-1",
        title="Add login",
        description="desc",
        work_type="feature",
        size="m",
        assignee="dev",
        parent_id="jig-0",
        depends_on=["jig-2"],
        workflow="code",
        status="open",
    ).to_message()
    assert msg.payload == {
        "kind": "ticket_created",
        "ticket_id": "jig-1",
        "title": "Add login",
        "description": "desc",
        "work_type": "feature",
        "type": "feature",
        "size": "m",
        "assignee": "dev",
        "parent_id": "jig-0",
        "depends_on": ["jig-2"],
        "workflow": "code",
        "status": "open",
    }


def test_typed_event_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TicketUpdated(ticket_id="jig-1", status="open", bogus="x")


def test_base_typed_event_raises_on_to_message() -> None:
    # The base declares ``kind`` as an un-valued ClassVar; only concrete events
    # are renderable. Test the observable contract, not pydantic internals.
    with pytest.raises(AttributeError):
        TypedEvent().to_message()


# --- decode_event: the subscribe-side inverse of to_message ------------------


def test_decode_round_trips_ticket_created() -> None:
    event = TicketCreated(
        sender="cli",
        recipient="broadcast",
        ticket_id="jig-1",
        title="Add login",
        description="desc",
        work_type="feature",
        size="m",
        assignee="dev",
        parent_id="jig-0",
        depends_on=["jig-2"],
        workflow="code",
        status="open",
    )
    decoded = decode_event(event.to_message())
    assert isinstance(decoded, TicketCreated)
    # The decoded event renders the identical wire message (full round-trip).
    assert decoded.to_message().payload == event.to_message().payload
    assert decoded.ticket_id == "jig-1"
    assert decoded.topic == "tickets.jig-1"
    assert decoded.sender == "cli"


def test_decode_preserves_an_explicit_dispatch_topic() -> None:
    # The orchestrator-dispatch copy sets topic="orchestrator" explicitly; the
    # envelope (not just the default per-ticket topic) must survive the decode.
    event = TicketCreated(
        sender="cli",
        recipient="orchestrator",
        topic="orchestrator",
        ticket_id="jig-1",
        title="T",
        work_type="feature",
        size="m",
        status="open",
    )
    decoded = decode_event(event.to_message())
    assert isinstance(decoded, TicketCreated)
    assert decoded.topic == "orchestrator"
    assert decoded.recipient == "orchestrator"


@pytest.mark.parametrize("internal", [False, True])
def test_decode_round_trips_ticket_updated_including_internal(internal: bool) -> None:
    event = TicketUpdated(
        sender="dev",
        recipient="orchestrator",
        topic="orchestrator",
        ticket_id="jig-1",
        status="resolved",
        internal=internal,
    )
    decoded = decode_event(event.to_message())
    assert isinstance(decoded, TicketUpdated)
    assert decoded.internal is internal
    assert decoded.status == "resolved"
    assert decoded.topic == "orchestrator"
    assert decoded.to_message().payload == event.to_message().payload


def test_decode_returns_none_for_an_untyped_kind() -> None:
    # shutdown_request / comment_posted / … are outside the typed set; the
    # subscriber falls back to raw-payload handling for those.
    msg = Message(
        sender="orchestrator",
        to="orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload={"kind": "shutdown_request"},
        topic="orchestrator",
    )
    assert decode_event(msg) is None


def test_decode_returns_none_when_payload_has_no_kind() -> None:
    msg = Message(
        sender="x",
        to="y",
        type=MessageType.CONTEXT_UPDATE,
        payload={"ticket_id": "jig-1"},
        topic="orchestrator",
    )
    assert decode_event(msg) is None


def test_decode_is_tolerant_of_a_partial_typed_payload() -> None:
    # A legacy/partial ``ticket_created`` (only kind + ticket_id, missing the
    # required title/work_type/…) must decode to None, NOT raise — the
    # subscriber falls back to raw handling and a stray message can't kill the
    # dispatch loop.
    msg = Message(
        sender="x",
        to="orchestrator",
        type=MessageType.CONTEXT_UPDATE,
        payload={"kind": "ticket_created", "ticket_id": "jig-1"},
        topic="orchestrator",
    )
    assert decode_event(msg) is None


def test_base_from_message_is_not_implemented() -> None:
    msg = TicketUpdated(ticket_id="jig-1", status="open").to_message()
    with pytest.raises(NotImplementedError):
        TypedEvent.from_message(msg)
