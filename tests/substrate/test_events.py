"""Substrate bones — TypedEvent schema (Epic 2, task 3).

Typed events replace magic-string bus topics + payload ``kind`` discriminators
with pydantic types. Each maps to an existing ``kind`` string so the down-
conversion to the legacy ``Message`` is faithful.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.store.bus import MessageType
from jig.substrate.events import (
    TicketCompleted,
    TicketCreated,
    TicketFailed,
    TicketUpdated,
    TypedEvent,
    ticket_topic,
)


def test_ticket_events_carry_their_existing_kind() -> None:
    assert TicketCreated(ticket_id="jig-1").kind == "ticket_created"
    assert TicketUpdated(ticket_id="jig-1").kind == "ticket_updated"
    assert TicketCompleted(ticket_id="jig-1").kind == "ticket_completed"
    assert TicketFailed(ticket_id="jig-1").kind == "ticket_failed"


def test_ticket_events_default_to_the_orchestrator_topic() -> None:
    assert TicketCompleted(ticket_id="jig-1").topic == "orchestrator"


def test_ticket_topic_helper_builds_per_ticket_topic() -> None:
    assert ticket_topic("jig-1") == "tickets.jig-1"


def test_to_message_carries_kind_and_domain_fields_in_payload() -> None:
    msg = TicketCompleted(ticket_id="jig-1").to_message()
    assert msg.topic == "orchestrator"
    assert msg.type == MessageType.STATUS
    assert msg.payload["kind"] == "ticket_completed"
    assert msg.payload["ticket_id"] == "jig-1"


def test_typed_event_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError, match="extra"):
        TicketCompleted(ticket_id="jig-1", bogus="x")


def test_typed_event_base_is_abstract_without_a_kind() -> None:
    # The base class has no kind — only concrete events do.
    assert not hasattr(TypedEvent, "kind") or TypedEvent.__dict__.get("kind") is None
