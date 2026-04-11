"""Singleton orchestrator — tracks tickets, runs agents, dispatches bus events."""

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jig.events import EventEmitter

from jig.agent import run_agent
from jig.project import Project, load_project
from jig.store import MessageBus
from jig.store.comments import CommentStore
from jig.store.memory import MemoryStore
from jig.store.tickets import TicketStore
from jig.ticket import TicketStatus, TicketType

_logger = logging.getLogger(__name__)


class Orchestrator:
    """Singleton orchestrator per project process.

    Runs three concurrent asyncio loops over the shared bus and stores:
      - service loop: external wake-ups on the "orchestrator" topic
      - per-ticket loops: one ``_run_ticket`` task per in-flight top-level ticket
      - dispatch loop: spawns fresh agents for unaddressed bus events
    """

    def __init__(self, project_path: Path, emitter: "EventEmitter | None" = None) -> None:
        self._project_path = project_path
        self._emitter = emitter
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
        """Walk the workflow phases for a top-level ticket.

        For each phase, creates a child TASK ticket, calls ``run_agent``,
        writes a ``phase_run`` comment on the task, and advances on
        success. Fails fast on any non-success result. Crash recovery
        is deferred post-MVP.
        """
        from jig.persistence import load_agent_type, load_workflow
        from jig.runtime import AgentSpawnContext, SpawnReason
        from jig.ticket import Ticket

        if (
            self.tickets is None
            or self.comments is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            raise RuntimeError("Orchestrator not started — call startup() first")

        ticket = await self.tickets.get(ticket_id)
        if ticket is None:
            return

        workflow = load_workflow(self._project_path, "default")
        worktree = await self._ensure_worktree(ticket)
        phase_idx = await self._current_phase_index(ticket_id, workflow)

        while phase_idx < len(workflow.phases):
            phase = workflow.phases[phase_idx]
            role_cfg = load_agent_type(self._project_path, phase.role)

            task_ticket = Ticket(
                type=TicketType.TASK,
                title=f"{phase.name}: {ticket.title}",
                description=phase.task_template or ticket.description,
                parent_id=ticket_id,
                assignee=phase.role,
                created_by="orchestrator",
                status=TicketStatus.IN_PROGRESS,
            )
            task_id = await self.tickets.create(task_ticket)
            loaded_task = await self.tickets.get(task_id)
            if loaded_task is None:
                raise RuntimeError(f"failed to create task ticket for {phase.name}")

            ctx = AgentSpawnContext(
                role=phase.role,
                role_cfg=role_cfg,
                spawn_reason=SpawnReason.PHASE_PRIMARY,
                ticket=loaded_task,
                parent=ticket,
                worktree_path=worktree,
                project=self._project,
                tickets=self.tickets,
                comments=self.comments,
                memory=self.memory,
                bus=self.bus,
            )
            try:
                result = await run_agent(ctx)
                await self._write_phase_run_comment(task_id, phase, result)
            except Exception:
                _logger.exception(
                    "agent or comment-write failed for phase %s ticket %s",
                    phase.name,
                    ticket_id,
                )
                try:
                    await self.tickets.update_status(task_id, TicketStatus.FAILED)
                except Exception:
                    _logger.exception(
                        "failed to mark child task %s FAILED (best-effort)", task_id
                    )
                await self.tickets.update_status(ticket_id, TicketStatus.FAILED)
                return

            if result.status == "success":
                phase_idx += 1
                continue

            # Fail fast. Recovery deferred post-MVP.
            await self.tickets.update_status(ticket_id, TicketStatus.FAILED)
            return

        await self.tickets.update_status(ticket_id, TicketStatus.RESOLVED)

    async def _ensure_worktree(self, ticket) -> Path:
        """Ensure a worktree exists for ``ticket`` and return its path."""
        from jig.worktree import create_worktree

        if self._project is None:
            raise RuntimeError("Orchestrator not started")
        worktree_path = self._project_path / ".jig" / "worktrees" / ticket.id
        if worktree_path.exists():
            return worktree_path
        return await create_worktree(
            project_path=self._project_path,
            ticket_id=ticket.id,
            base_branch=self._project.default_branch,
        )

    async def _current_phase_index(self, ticket_id: str, workflow) -> int:
        """Return the index of the first phase that has not yet succeeded.

        Walks ``workflow.phases`` in order and checks whether ANY child TASK
        ticket whose title starts with ``"{phase.name}: "`` has a successful
        phase_run comment.  We match on title-prefix (which the creation code
        stamps as ``f"{phase.name}: {ticket.title}"``) rather than on
        assignee/role so that retried task tickets for the same phase are also
        recognised.  We stop at the first phase without a success — duplicate
        task tickets for the same phase therefore still count as *one* phase
        completed, not two.
        """
        if self.tickets is None or self.comments is None:
            raise RuntimeError("Orchestrator not started")
        task_tickets = await self.tickets.find_by_parent(ticket_id)
        for phase_idx, phase in enumerate(workflow.phases):
            prefix = f"{phase.name}: "
            phase_tasks = [t for t in task_tickets if t.title.startswith(prefix)]
            phase_succeeded = False
            for task_ticket in phase_tasks:
                runs = await self.comments.phase_runs_for(task_ticket.id)
                if any(r.phase_result == "success" for r in runs):
                    phase_succeeded = True
                    break
            if not phase_succeeded:
                return phase_idx
        return len(workflow.phases)

    async def _write_phase_run_comment(self, task_id: str, phase, result) -> None:
        from jig.ticket import Comment

        if self.comments is None:
            raise RuntimeError("Orchestrator not started")

        phase_result: str = result.status if result.status in {
            "success", "failed", "blocked", "needs_info"
        } else "failed"

        await self.comments.post(
            Comment(
                ticket_id=task_id,
                author="orchestrator",
                content=f"phase {phase.name}: {result.status}",
                kind="phase_run",
                phase_result=phase_result,  # type: ignore[arg-type]
                phase_branch=f"jig/{task_id}",
            )
        )

    async def _run_dispatch_loop(self) -> None:
        """Fan-out listener: spawn QA responders for unaddressed bus events.

        Subscribes to ALL bus publishes via ``add_websocket_listener``,
        queues them locally, then filters for ticket-topic events
        addressed to a non-user role that does not already have a live
        subscriber. A filtered event triggers ``_spawn_qa_responder``.
        """
        if self.bus is None:
            raise RuntimeError("Orchestrator not started — call startup() first")

        queue: asyncio.Queue = asyncio.Queue()

        async def _forward(message) -> None:
            await queue.put(message)

        await self.bus.add_websocket_listener(_forward)

        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue
            # Mirror to emitter for TUI consumption (before target filter so all messages reach it).
            # Isolate emit failures so a bad subscriber cannot kill the dispatch loop.
            if self._emitter is not None:
                from jig.events import JigEvent
                payload = msg.payload or {}
                kind = payload.get("kind", "event")
                try:
                    await self._emitter.emit(JigEvent(type=kind, data=payload))
                except Exception:
                    _logger.warning("emitter.emit raised; continuing", exc_info=True)

            target = self._resolve_target(msg)
            if target is None:
                continue
            ticket_id, role = target
            if role == "user":
                continue
            if (ticket_id, role) in self._live_subscribers:
                continue
            await self._spawn_qa_responder(ticket_id, role, msg)

    def _resolve_target(self, msg) -> tuple[str, str] | None:
        """Extract (ticket_id, role) from a ticket-topic directed message."""
        if not msg.topic.startswith("tickets."):
            return None
        ticket_id = msg.topic.removeprefix("tickets.")
        if not msg.to or msg.to == "broadcast":
            return None
        return (ticket_id, msg.to)

    async def _spawn_qa_responder(
        self, ticket_id: str, role: str, initial_event
    ) -> None:
        from jig.persistence import load_agent_type
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self.tickets is None
            or self.comments is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            raise RuntimeError("Orchestrator not started — call startup() first")

        # Reserve the slot synchronously before any await so that the
        # dispatch loop's duplicate-spawn check sees it immediately.  Use the
        # currently-running task as a cheap placeholder; it is replaced with
        # the real run_agent task once setup succeeds.
        self._live_subscribers[(ticket_id, role)] = asyncio.current_task()  # type: ignore[assignment]

        try:
            ticket = await self.tickets.get(ticket_id)
            if ticket is None:
                self._live_subscribers.pop((ticket_id, role), None)
                return
            parent = None
            if ticket.parent_id:
                parent = await self.tickets.get(ticket.parent_id)
            role_cfg = load_agent_type(self._project_path, role)
            worktree = await self._ensure_worktree(parent or ticket)
        except Exception:
            # Swallow setup failures so the dispatch loop keeps running.
            _logger.exception(
                "failed to spawn qa responder for %s/%s", ticket_id, role
            )
            self._live_subscribers.pop((ticket_id, role), None)
            return

        ctx = AgentSpawnContext(
            role=role,
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.QA_RESPONDER,
            ticket=ticket,
            parent=parent,
            worktree_path=worktree,
            project=self._project,
            tickets=self.tickets,
            comments=self.comments,
            memory=self.memory,
            bus=self.bus,
            initial_bus_message=initial_event.payload if initial_event else None,
        )
        task = asyncio.create_task(run_agent(ctx))
        self._live_subscribers[(ticket_id, role)] = task

        # Log any exception raised inside the run_agent task.
        def _cleanup(t: asyncio.Task) -> None:
            self._live_subscribers.pop((ticket_id, role), None)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                _logger.warning(
                    "qa responder for %s/%s raised",
                    ticket_id,
                    role,
                    exc_info=exc,
                )

        task.add_done_callback(_cleanup)
