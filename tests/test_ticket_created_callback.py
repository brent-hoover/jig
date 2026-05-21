"""Tests for ``TicketStore.set_create_callback`` and ``publish_ticket_created``.

Background: every code path that mints a ``Ticket`` (PM MCP,
orchestrator, init flow, CLI, TUI commands, Coordinator materialize,
spike proposals) must announce the new ticket on the bus so
subscribers — primarily the TUI — can maintain complete in-memory
state. Previously only ``ticket_mcp.handle_create_ticket`` published;
every other path landed silently and the TUI saw a half-empty dict
once the first status-change event arrived. This module pins down the
callback shape that fixes that.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jig.store.bus import MessageBus
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType
from jig.ticket_events import publish_ticket_created
from tests._test_ticket import TICKET_AC_PLACEHOLDER


@pytest.fixture
async def store_and_bus(tmp_path: Path):
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    await tickets.load()
    await bus.load()
    return tickets, bus


class TestStoreCreateCallback:
    async def test_callback_fires_on_create(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        tickets, _ = store_and_bus
        seen: list[str] = []

        def _cb(t: Ticket) -> None:
            seen.append(t.id)

        tickets.set_create_callback(_cb)
        await tickets.create(
            Ticket(
                id="t-1",
                work_type=WorkType.FEATURE,
                title="t",
                created_by="test",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        assert seen == ["t-1"]

    async def test_async_callback_is_awaited(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        tickets, _ = store_and_bus
        seen: list[str] = []

        async def _cb(t: Ticket) -> None:
            await asyncio.sleep(0)
            seen.append(t.id)

        tickets.set_create_callback(_cb)
        await tickets.create(
            Ticket(
                id="t-2",
                work_type=WorkType.BUGFIX,
                title="b",
                created_by="test",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        # Async callback runs in the background — give it a tick to land.
        for _ in range(10):
            if seen:
                break
            await asyncio.sleep(0.01)
        assert seen == ["t-2"]

    async def test_callback_can_be_cleared(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        tickets, _ = store_and_bus
        seen: list[str] = []
        tickets.set_create_callback(lambda t: seen.append(t.id))
        tickets.set_create_callback(None)
        await tickets.create(
            Ticket(
                id="t-3",
                work_type=WorkType.FEATURE,
                title="t",
                created_by="test",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        assert seen == []

    async def test_callback_failure_does_not_break_create(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        """Fail-soft: a broken sync callback is swallowed (logged as
        warning) and ``create()`` returns normally. The async path
        already behaves this way via ``_on_done``; the sync path
        matches so the documented "fail-soft" contract holds
        regardless of callback flavor."""
        tickets, _ = store_and_bus

        def _broken(t: Ticket) -> None:
            raise RuntimeError("boom")

        tickets.set_create_callback(_broken)
        ticket_id = await tickets.create(
            Ticket(
                id="t-4",
                work_type=WorkType.FEATURE,
                title="t",
                created_by="test",
                description=TICKET_AC_PLACEHOLDER,
            )
        )
        assert ticket_id == "t-4"
        loaded = await tickets.get("t-4")
        assert loaded is not None


class TestPublishTicketCreated:
    async def test_default_publishes_broadcast_only(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        """Default ``for_dispatch=False``: only the broadcast topic
        fires. Prevents direct-create paths (system tickets,
        Coordinator materialize) from double-triggering dispatch via
        the orchestrator topic."""
        _, bus = store_and_bus
        orch_queue = await bus.subscribe("orchestrator")
        broadcast_queue = await bus.subscribe("tickets.planning")
        ticket = Ticket(
            id="planning",
            work_type=WorkType.PLANNING,
            title="Project planning",
            created_by="test",
            description="",
        )
        await publish_ticket_created(bus, ticket, sender="orchestrator")
        # Broadcast topic gets the message.
        msg = await broadcast_queue.get()
        assert msg.payload["title"] == "Project planning"
        # Orchestrator topic stays empty.
        assert orch_queue.empty()

    async def test_for_dispatch_publishes_to_orchestrator_topic(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        """``for_dispatch=True`` triggers the dispatch loop by also
        publishing to the orchestrator topic. Used by
        ``handle_create_ticket`` so MCP-created tickets get scheduled
        immediately."""
        _, bus = store_and_bus
        queue = await bus.subscribe("orchestrator")
        ticket = Ticket(
            id="feature-x",
            work_type=WorkType.FEATURE,
            title="X",
            created_by="test",
            description=TICKET_AC_PLACEHOLDER,
        )
        await publish_ticket_created(bus, ticket, sender="test", for_dispatch=True)
        msg = await queue.get()
        assert msg.topic == "orchestrator"
        assert msg.payload["kind"] == "ticket_created"
        assert msg.payload["title"] == "X"
        # Legacy alias for older subscribers.
        assert msg.payload["type"] == "feature"

    async def test_publishes_to_ticket_topic(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        _, bus = store_and_bus
        queue = await bus.subscribe("tickets.feature-x")
        ticket = Ticket(
            id="feature-x",
            work_type=WorkType.FEATURE,
            title="X",
            created_by="test",
            description=TICKET_AC_PLACEHOLDER,
        )
        await publish_ticket_created(bus, ticket, sender="test")
        msg = await queue.get()
        assert msg.topic == "tickets.feature-x"
        assert msg.payload["title"] == "X"

    async def test_depends_on_falls_back_to_blocked_by(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        _, bus = store_and_bus
        queue = await bus.subscribe("tickets.t-deps")
        ticket = Ticket(
            id="t-deps",
            work_type=WorkType.FEATURE,
            title="deps",
            created_by="test",
            description=TICKET_AC_PLACEHOLDER,
            blocked_by=["dep-a", "dep-b"],
        )
        await publish_ticket_created(bus, ticket, sender="test")
        msg = await queue.get()
        assert msg.payload["depends_on"] == ["dep-a", "dep-b"]

    async def test_explicit_depends_on_overrides_blocked_by(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        _, bus = store_and_bus
        queue = await bus.subscribe("tickets.t-deps2")
        ticket = Ticket(
            id="t-deps2",
            work_type=WorkType.FEATURE,
            title="deps",
            created_by="test",
            description=TICKET_AC_PLACEHOLDER,
            blocked_by=["from-model"],
        )
        await publish_ticket_created(
            bus, ticket, sender="test", depends_on=["from-caller"]
        )
        msg = await queue.get()
        assert msg.payload["depends_on"] == ["from-caller"]


class TestEndToEndStoreCallback:
    """End-to-end: register publish_ticket_created as the callback,
    then verify a direct ``tickets.create(Ticket(...))`` publishes
    on the broadcast topic (and stays off the orchestrator topic so
    the dispatch loop isn't double-triggered)."""

    async def test_direct_create_publishes_broadcast_event(
        self, store_and_bus: tuple[TicketStore, MessageBus]
    ) -> None:
        tickets, bus = store_and_bus

        async def _publish(t: Ticket) -> None:
            await publish_ticket_created(bus, t, sender="orchestrator")

        tickets.set_create_callback(_publish)
        broadcast_queue = await bus.subscribe("tickets.planning")
        orch_queue = await bus.subscribe("orchestrator")
        await tickets.create(
            Ticket(
                id="planning",
                work_type=WorkType.PLANNING,
                title="Project planning",
                created_by="orchestrator",
                description="",
            )
        )
        # Async callback runs in the background.
        for _ in range(10):
            if not broadcast_queue.empty():
                break
            await asyncio.sleep(0.01)
        msg = await broadcast_queue.get()
        assert msg.payload["ticket_id"] == "planning"
        assert msg.payload["title"] == "Project planning"
        # Direct creates must NOT trigger the dispatch loop —
        # ``for_dispatch=False`` is the default for the callback.
        assert orch_queue.empty()
