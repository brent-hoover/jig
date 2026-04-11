"""Singleton orchestrator — tracks tickets, runs agents, dispatches bus events."""

import asyncio
import logging
from pathlib import Path

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
            result = await run_agent(ctx)
            await self._write_phase_run_comment(task_id, phase, result)

            if result.status == "success":
                phase_idx += 1
                continue

            # Fail fast. Recovery deferred post-MVP.
            await self.tickets.update_status(ticket_id, TicketStatus.FAILED)
            return

        await self.tickets.update_status(ticket_id, TicketStatus.RESOLVED)

    async def _ensure_worktree(self, ticket) -> Path:
        """Ensure a worktree exists for ``ticket`` and return its path.

        NOTE: This calls ``create_worktree`` with the new per-ticket
        signature that Task 6.1 will introduce. Until Task 6.1 lands,
        callers must monkeypatch this method (tests already do).
        """
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
        """Count successful phase_run comments on task tickets under this ticket."""
        if self.tickets is None or self.comments is None:
            raise RuntimeError("Orchestrator not started")
        task_tickets = await self.tickets.find_by_parent(ticket_id)
        completed = 0
        for task_ticket in task_tickets:
            runs = await self.comments.phase_runs_for(task_ticket.id)
            if any(r.phase_result == "success" for r in runs):
                completed += 1
        return completed

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
        """Register a live subscriber for (ticket_id, role).

        Task 5.4 replaces this stub with a real ``run_agent`` invocation.
        For now we register a no-op task so the dispatch loop's
        ``already-subscribed`` check functions.
        """
        async def _noop() -> None:
            return

        task = asyncio.create_task(_noop())
        self._live_subscribers[(ticket_id, role)] = task

        def _cleanup(_task: asyncio.Task) -> None:
            self._live_subscribers.pop((ticket_id, role), None)

        task.add_done_callback(_cleanup)
