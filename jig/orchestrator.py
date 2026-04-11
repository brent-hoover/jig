"""Singleton orchestrator — tracks tickets, runs agents, dispatches bus events."""

import asyncio
import logging
from pathlib import Path

from jig.project import Project, load_project
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus

_logger = logging.getLogger(__name__)


class Orchestrator:
    """Singleton orchestrator per project process.

    Runs three concurrent asyncio loops over the shared bus and stores:
      - service loop: external wake-ups on the "orchestrator" topic
      - per-ticket loops: one ``_run_ticket`` task per in-flight top-level ticket
      - dispatch loop: spawns fresh agents for unaddressed bus events

    Task 5.1 provides the scaffolding, service loop, startup/shutdown, and
    in-progress resume scan. Tasks 5.2-5.4 fill in the dispatch loop,
    per-ticket loop, and QA responder spawning.
    """

    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path
        self._project: Project | None = None
        self.tickets: TicketStore | None = None
        self.comments: CommentStore | None = None
        self.memory: MemoryStore | None = None
        self.bus: MessageBus | None = None

        self._running_tickets: dict[str, asyncio.Task] = {}
        self._live_subscribers: dict[tuple[str, str], asyncio.Task] = {}
        self._dispatch_task: asyncio.Task | None = None
        self._service_task: asyncio.Task | None = None
        self._running = False

    async def startup(self) -> None:
        try:
            self._project = load_project(self._project_path)
            store_dir = self._project_path / ".jig" / "store"
            store_dir.mkdir(parents=True, exist_ok=True)
            self.tickets = TicketStore(store_dir / "tickets.jsonl")
            self.comments = CommentStore(store_dir / "comments.jsonl")
            self.memory = MemoryStore(store_dir)
            self.bus = MessageBus(store_dir / "messages.jsonl")
            await asyncio.gather(
                self.tickets.load(),
                self.comments.load(),
                self.memory.load(),
                self.bus.load(),
            )
            self._running = True
            await self._resume_in_progress()
            self._dispatch_task = asyncio.create_task(self._run_dispatch_loop())
            self._service_task = asyncio.create_task(self._run_service_loop())
        except Exception:
            await self._emergency_reset()
            raise

    async def _emergency_reset(self) -> None:
        self._running = False
        for task in (self._dispatch_task, self._service_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        for task in self._running_tickets.values():
            task.cancel()
        self._running_tickets.clear()
        self._live_subscribers.clear()
        self._dispatch_task = None
        self._service_task = None
        self._project = None
        self.tickets = None
        self.comments = None
        self.memory = None
        self.bus = None

    async def shutdown(self) -> None:
        self._running = False
        tasks_to_cancel: list[asyncio.Task] = []
        if self._dispatch_task is not None:
            tasks_to_cancel.append(self._dispatch_task)
        if self._service_task is not None:
            tasks_to_cancel.append(self._service_task)
        tasks_to_cancel.extend(self._running_tickets.values())
        tasks_to_cancel.extend(self._live_subscribers.values())
        for task in tasks_to_cancel:
            task.cancel()
        for task in tasks_to_cancel:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                _logger.warning("task raised during shutdown", exc_info=True)
        self._running_tickets.clear()
        self._live_subscribers.clear()
        self._dispatch_task = None
        self._service_task = None

    async def _resume_in_progress(self) -> None:
        if self.tickets is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        in_progress = await self.tickets.find_in_progress_top_level()
        for ticket in in_progress:
            task = asyncio.create_task(self._run_ticket(ticket.id))
            self._running_tickets[ticket.id] = task

    async def _run_service_loop(self) -> None:
        if self.bus is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        queue = await self.bus.subscribe("orchestrator")
        try:
            while self._running:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                payload = msg.payload or {}
                kind = payload.get("kind")
                if kind == "ticket_created":
                    ticket_id = payload.get("ticket_id")
                    if ticket_id:
                        await self._handle_schedule(ticket_id)
                elif kind == "shutdown_request":
                    self._running = False
        finally:
            await self.bus.unsubscribe("orchestrator", queue)

    async def _handle_schedule(self, ticket_id: str) -> None:
        """For MVP: immediately start the ticket. Scheduling policy TBD."""
        if self.tickets is None:
            raise RuntimeError("Orchestrator not started — call startup() first")
        if ticket_id in self._running_tickets:
            return
        await self.tickets.update_status(ticket_id, TicketStatus.IN_PROGRESS)
        task = asyncio.create_task(self._run_ticket(ticket_id))
        self._running_tickets[ticket_id] = task

    async def _run_ticket(self, ticket_id: str) -> None:
        """Per-ticket loop. Task 5.3 fills this in."""
        _logger.info("run_ticket stub: %s", ticket_id)

    async def _run_dispatch_loop(self) -> None:
        """Dispatch loop. Task 5.2 fills this in."""
        while self._running:
            await asyncio.sleep(0.1)
