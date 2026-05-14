"""WebSocket server for broadcasting events to TUI clients."""

import asyncio
import collections
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import websockets
from websockets.asyncio.server import serve, ServerConnection

from jig.agent import build_agent_prompt
from jig.events import EventEmitter
from jig.persistence import list_roles, load_role, load_workflow
from jig.prompt_registry import PromptRegistry
from jig.store import Message, MessageType
from jig.thread import Answer, Note, SystemEvent, ThreadEntry
from jig.ticket import TicketStatus
from jig.ticket_mcp import (
    handle_create_ticket,
    handle_comment_on_ticket,
    handle_read_comments,
    handle_update_ticket,
    handle_list_tickets,
)

if TYPE_CHECKING:
    from jig.orchestrator import Orchestrator

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Typed message protocol — Task 1.2
# ---------------------------------------------------------------------------

_VALID_TOPICS = frozenset({"tickets", "threads", "agents", "spec", "events", "prompts"})


def snapshot_envelope(topic: str, data: Any) -> dict:
    """Build a snapshot message for a topic (sent once per subscribe)."""
    if topic not in _VALID_TOPICS:
        raise ValueError(
            f"unknown topic {topic!r}; expected one of {sorted(_VALID_TOPICS)}"
        )
    return {"type": "snapshot", "topic": topic, "data": data}


def event_envelope(topic: str, kind: str, data: Any) -> dict:
    """Build a typed event message (sent per state change)."""
    if topic not in _VALID_TOPICS:
        raise ValueError(
            f"unknown topic {topic!r}; expected one of {sorted(_VALID_TOPICS)}"
        )
    return {"type": "event", "topic": topic, "kind": kind, "data": data}


class WebSocketServer:
    def __init__(
        self,
        emitter: EventEmitter,
        host: str = "0.0.0.0" if os.environ.get("JIG_IN_CONTAINER") else "127.0.0.1",
        port: int = 19100,
        orchestrator: "Orchestrator | None" = None,
        project_path: Path | None = None,
    ) -> None:
        self._emitter = emitter
        self._host = host
        self._port = port
        self._orch = orchestrator
        self._project_path = project_path
        self._server = None
        self._relay_task = None
        self._clients: set[ServerConnection] = set()
        self._queue = emitter.subscribe()
        self._history: collections.deque[str] = collections.deque(maxlen=1000)
        self._history_replayed: set[ServerConnection] = set()
        self._subscriptions: dict[ServerConnection, set[str]] = {}
        self.prompt_registry = PromptRegistry()
        self._background_tasks: set[asyncio.Task] = set()
        # prompt_id → full data payload for prompts not yet replied to.
        # Replayed as live events to clients that connect after the emit.
        self._pending_prompts: dict[str, dict] = {}

    @property
    def port(self) -> int:
        if self._server is not None:
            return self._server.sockets[0].getsockname()[1]
        return self._port

    async def start(self) -> None:
        self._server = await serve(
            self._handle_client,
            self._host,
            self._port,
        )
        self._relay_task = asyncio.create_task(self._relay_events())

    async def stop(self) -> None:
        if self._relay_task:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._emitter.unsubscribe(self._queue)

    async def _handle_client(self, websocket: ServerConnection) -> None:
        # Do NOT replay history here — legacy clients get it in _handle_incoming
        # on their first non-subscribe message; typed subscribers never get it.
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                await self._handle_incoming(websocket, raw)
        except websockets.ConnectionClosed:
            pass
        except Exception:
            logger.exception("ws client coroutine raised")
            raise
        finally:
            self._clients.discard(websocket)
            self._history_replayed.discard(websocket)
            self._subscriptions.pop(websocket, None)

    async def _safe_send(self, websocket: ServerConnection, message: str) -> None:
        """Send a reply, swallowing ConnectionClosed so a dying client
        doesn't bubble a traceback through ``_handle_client``."""
        try:
            await websocket.send(message)
        except websockets.ConnectionClosed:
            return

    async def _handle_incoming(self, websocket: ServerConnection, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await self._safe_send(
                websocket, json.dumps({"ok": False, "error": "bad json"})
            )
            return

        # New typed protocol: dispatch on "type" first.
        msg_type = payload.get("type")
        if msg_type == "subscribe":
            topics = payload.get("topics", [])
            # Track per-client subscriptions for filtered relay.
            if websocket not in self._subscriptions:
                self._subscriptions[websocket] = set()
            self._subscriptions[websocket].update(topics)
            for topic in topics:
                try:
                    snapshot = await self._build_snapshot(topic)
                except ValueError as exc:
                    await self._safe_send(
                        websocket,
                        json.dumps({"type": "error", "topic": topic, "error": str(exc)}),
                    )
                    continue
                await self._safe_send(
                    websocket, json.dumps(snapshot_envelope(topic, snapshot))
                )
                # Replay any prompts that were emitted before this client connected.
                # Send as live "event" envelopes so NowScreen handles them identically
                # to real-time prompt_request events — no TUI-side changes needed.
                if topic == "prompts":
                    stale: list[str] = []
                    for pid, data in list(self._pending_prompts.items()):
                        if pid not in self.prompt_registry._pending:
                            stale.append(pid)
                            continue
                        await self._safe_send(
                            websocket,
                            json.dumps(event_envelope("prompts", "request", data)),
                        )
                    for pid in stale:
                        del self._pending_prompts[pid]
            return

        if msg_type == "command":
            name = payload.get("name")
            args = payload.get("args", {})
            if not name:
                await self._safe_send(
                    websocket,
                    json.dumps({"type": "result", "ok": False, "error": "command name required"}),
                )
                return
            # CRITICAL: do NOT await dispatch here. Long-running commands
            # like /init block on prompt round-trips that need OTHER
            # commands (prompt_reply) to come through this same read loop.
            # If we await, we deadlock: cmd_init waits for prompt_reply,
            # prompt_reply can't run because we haven't returned to read
            # the next frame. Spawn the dispatch as a background task so
            # the read loop stays responsive.
            task = asyncio.create_task(
                self._dispatch_command_safe(websocket, name, args),
                name=f"ws-cmd-{name}",
            )
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            return

        # Non-subscribe message from a legacy client: replay history once.
        if websocket not in self._history_replayed:
            self._history_replayed.add(websocket)
            for message in self._history:
                try:
                    await websocket.send(message)
                except websockets.ConnectionClosed:
                    return

        # Legacy path: existing Bun TUI uses bare command keys.
        # Keep this entire block as-is — do not modify.
        if self._orch is None:
            return

        command = payload.get("command")
        args = payload.get("args", {})

        try:
            if command == "create_ticket":
                tid = await handle_create_ticket(
                    tickets=self._orch.tickets,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                    project_path=self._orch._project_path,
                )
                await self._safe_send(
                    websocket, json.dumps({"ok": True, "ticket_id": tid})
                )
            elif command == "comment_on_ticket":
                cid = await handle_comment_on_ticket(
                    tickets=self._orch.tickets,
                    threads=self._orch.threads,
                    bus=self._orch.bus,
                    sender="user",
                    sender_cfg=None,
                    args=args,
                )
                await self._safe_send(
                    websocket, json.dumps({"ok": True, "comment_id": cid})
                )
            elif command == "update_ticket":
                updated = await handle_update_ticket(
                    tickets=self._orch.tickets,
                    threads=self._orch.threads,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await self._safe_send(
                    websocket, json.dumps({"ok": True, "status": updated.status.value})
                )
            elif command == "list_tickets":
                all_tickets = await handle_list_tickets(
                    tickets=self._orch.tickets,
                    args=args,
                )
                await self._safe_send(
                    websocket,
                    json.dumps(
                        {
                            "ok": True,
                            "tickets": [
                                {
                                    "id": t.id,
                                    "work_type": t.work_type.value,
                                    # Legacy alias for TUI clients not yet updated
                                    # to the Phase 1 schema. Remove once Task G lands.
                                    "type": t.work_type.value,
                                    "size": t.size.value,
                                    "status": t.status.value,
                                    "title": t.title,
                                    "description": t.description,
                                    "assignee": t.assignee,
                                    "parent_id": t.parent_id,
                                    "blocked_by": t.blocked_by,
                                    "workflow": t.workflow,
                                }
                                for t in all_tickets
                            ],
                        }
                    ),
                )
            elif command == "get_comments":
                ticket_id = args.get("ticket_id")
                if not ticket_id:
                    await self._safe_send(
                        websocket,
                        json.dumps({"ok": False, "error": "ticket_id required"}),
                    )
                    return
                found = await handle_read_comments(
                    threads=self._orch.threads,
                    ticket_id=ticket_id,
                    kind=args.get("kind"),
                )
                await self._safe_send(
                    websocket,
                    json.dumps(
                        {
                            "ok": True,
                            "comments": [_thread_entry_to_wire(e) for e in found],
                        }
                    ),
                )
            elif command == "answer_questions":
                result = await self._handle_answer_questions(args)
                await self._safe_send(websocket, json.dumps({"ok": True, **result}))
            elif command == "list_agents":
                agents = list_roles(self._orch._project_path)
                await self._safe_send(
                    websocket,
                    json.dumps(
                        {
                            "ok": True,
                            "agents": [a.model_dump() for a in agents],
                        }
                    ),
                )
            elif command == "preview_prompt":
                from jig.runtime import AgentSpawnContext, SpawnReason

                ticket_id = args.get("ticket_id")
                role = args.get("role")
                if not ticket_id or not role:
                    await self._safe_send(
                        websocket,
                        json.dumps(
                            {"ok": False, "error": "ticket_id and role required"}
                        ),
                    )
                    return
                ticket = await self._orch.tickets.get(ticket_id)
                if ticket is None:
                    await self._safe_send(
                        websocket,
                        json.dumps(
                            {"ok": False, "error": f"ticket {ticket_id} not found"}
                        ),
                    )
                    return
                parent = None
                if ticket.parent_id:
                    parent = await self._orch.tickets.get(ticket.parent_id)
                role_cfg = load_role(self._orch._project_path, role)
                # ``ticket.id`` is field-validated as path-safe by the
                # Ticket model; reuse it instead of the raw WS arg so
                # any drift between the two is caught.
                worktree_path = (
                    self._orch._project_path / ".jig" / "worktrees" / ticket.id
                )
                ctx = AgentSpawnContext(
                    role=role,
                    role_cfg=role_cfg,
                    spawn_reason=SpawnReason.PHASE_PRIMARY,
                    ticket=ticket,
                    parent=parent,
                    worktree_path=worktree_path,
                    project=self._orch._project,
                    tickets=self._orch.tickets,
                    threads=self._orch.threads,
                    memory=self._orch.memory,
                    bus=self._orch.bus,
                )
                prompt = await build_agent_prompt(ctx)
                await self._safe_send(
                    websocket,
                    json.dumps(
                        {
                            "ok": True,
                            "prompt": prompt,
                            "system_prompt": role_cfg.phase_prompt,
                            "char_count": len(prompt),
                        }
                    ),
                )
            elif command == "get_workflow":
                name = args.get("name", "default")
                wf = load_workflow(self._orch._project_path, name)
                await self._safe_send(
                    websocket,
                    json.dumps(
                        {
                            "ok": True,
                            "workflow": wf.model_dump(),
                        }
                    ),
                )
            else:
                await self._safe_send(
                    websocket,
                    json.dumps({"ok": False, "error": f"unknown command {command}"}),
                )
        except Exception as exc:
            await self._safe_send(
                websocket, json.dumps({"ok": False, "error": str(exc)})
            )

    async def _handle_answer_questions(self, args: dict) -> dict:
        """Post operator answers for the ticket's open Questions and
        resume the ticket if it was paused.

        Mirrors the retired ``handle_answer_questions`` from
        ``ticket_mcp``: answers bind in order to the ticket's oldest-
        to-newest unresolved Questions, extras attach to the last
        open Question (fallback to a Note if there are none), and a
        ``resume`` flag (default True) flips ``needs_info`` back to
        ``in_progress`` with a status_change audit entry.
        """
        assert self._orch is not None  # caller checks before dispatch
        orch = self._orch
        ticket_id = args["ticket_id"]
        answers: list[str] = args.get("answers", [])
        resume: bool = args.get("resume", True)

        ticket = await orch.tickets.get(ticket_id)
        if ticket is None:
            raise KeyError(f"ticket {ticket_id} not found")

        all_questions = await orch.threads.find_by_kind(ticket_id, "question")
        open_questions = [q for q in all_questions if not q.is_resolved()]

        comment_ids: list[str] = []
        for idx, text in enumerate(answers):
            if open_questions:
                qid = open_questions[min(idx, len(open_questions) - 1)].id
                entry: ThreadEntry = Answer(
                    ticket_id=ticket_id, author="user", question_id=qid, text=text
                )
                kind_for_bus = "answer"
            else:
                entry = Note(ticket_id=ticket_id, author="user", text=text)
                kind_for_bus = "note"
            cid = await orch.threads.post(entry)
            comment_ids.append(cid)
            await orch.bus.publish(
                Message(
                    sender="user",
                    to=ticket.assignee or "broadcast",
                    type=MessageType.CONTEXT_UPDATE,
                    payload={
                        "kind": "comment_posted",
                        "ticket_id": ticket_id,
                        "comment_id": cid,
                        "author": "user",
                        "content": text,
                        "comment_kind": kind_for_bus,
                    },
                    topic=f"tickets.{ticket_id}",
                )
            )

        result: dict = {"comment_ids": comment_ids}

        if resume and ticket.status == TicketStatus.NEEDS_INFO:
            updated = await orch.tickets.update(
                ticket_id, status=TicketStatus.IN_PROGRESS
            )
            await orch.threads.post(
                SystemEvent(
                    ticket_id=ticket_id,
                    author="user",
                    event_type="status_change",
                    content=f"status needs_info -> {updated.status.value}",
                )
            )
            await orch.bus.publish(
                Message(
                    sender="user",
                    to=updated.assignee or "broadcast",
                    type=MessageType.CONTEXT_UPDATE,
                    payload={
                        "kind": "ticket_updated",
                        "ticket_id": ticket_id,
                        "status": updated.status.value,
                    },
                    topic=f"tickets.{ticket_id}",
                )
            )
            result["status"] = updated.status.value
        else:
            result["status"] = ticket.status.value

        return result

    async def _dispatch_command_safe(self, websocket, name: str, args: dict) -> None:
        """Background-task wrapper around _dispatch_command. Catches
        anything _dispatch_command itself failed to handle so the task
        doesn't propagate an unhandled exception into the event loop."""
        try:
            await self._dispatch_command(websocket, name, args)
        except Exception:
            logger.exception("dispatch task crashed for command %s", name)

    async def _dispatch_command(self, websocket, name: str, args: dict) -> None:
        from jig.tui.commands import get_handler

        handler = get_handler(name)
        if handler is None:
            await self._safe_send(
                websocket,
                json.dumps({"type": "result", "ok": False,
                            "error": f"unknown command: {name}"}),
            )
            return
        try:
            result = await handler(
                args=args.get("args", []) if isinstance(args, dict) else [],
                orch=self._orch,
                project_path=self._project_path,
                prompt_registry=self.prompt_registry,
                emitter=self._emitter,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("command %s failed", name)
            await self._safe_send(
                websocket,
                json.dumps({"type": "result", "ok": False, "error": str(exc)}),
            )
            return
        await self._safe_send(
            websocket,
            json.dumps({"type": "result", **result}),
        )

    async def _build_snapshot(self, topic: str) -> Any:
        """Per-topic initial snapshot.

        When the orchestrator is in unconfigured mode (no project yet —
        before /init has run), the per-project stores (tickets, bus,
        threads) are None. Each topic returns the empty equivalent
        rather than crashing the WS handler with AttributeError.
        """
        # Unconfigured orchestrator: no stores loaded yet. Returning empty
        # equivalents lets the TUI subscribe + survive until /init promotes
        # the orchestrator to configured mode (cmd_init calls orch.reload()).
        if self._orch is None or not getattr(self._orch, "is_configured", True):
            if topic in ("tickets", "agents", "events", "threads", "prompts"):
                return []
            if topic == "spec":
                return None
            raise ValueError(f"unknown topic {topic!r}")

        if topic == "tickets":
            all_tickets = await self._orch.tickets.list_all()
            return [t.model_dump(mode="json") for t in all_tickets]
        if topic == "spec":
            import yaml

            from jig.spec_schema import StructuredSpec

            if self._project_path is None:
                return None
            spec_file = (
                self._project_path / "docs" / "project.structured.yaml"
            )
            if not spec_file.is_file():
                return None
            data = yaml.safe_load(spec_file.read_text()) or {}
            spec = StructuredSpec.model_validate(data)
            return spec.model_dump(mode="json", by_alias=True)
        if topic == "agents":
            if not hasattr(self._orch, "list_active_agents"):
                logger.warning(
                    "snapshot for topic 'agents' is empty: missing helper list_active_agents"
                )
                return []
            return await self._orch.list_active_agents()
        if topic == "events":
            msgs = await self._orch.bus.recent(limit=100)
            return [m.model_dump(mode="json") for m in msgs]
        if topic == "threads":
            # Snapshot is per-ticket; subscribers fetch on demand via command.
            return []
        if topic == "prompts":
            # No history; subscribers receive only live prompt_request events.
            return []
        raise ValueError(f"unknown topic {topic!r}")

    async def _relay_events(self) -> None:
        while True:
            event = await self._queue.get()
            # Raw broadcast: only to legacy clients (no entry in _subscriptions).
            message = event.to_json()
            self._history.append(message)
            for client in list(self._clients):
                if client in self._subscriptions:
                    # Typed subscriber — skip raw frames.
                    continue
                try:
                    await client.send(message)
                except websockets.ConnectionClosed:
                    self._clients.discard(client)
            # Track pending prompt requests for reconnect replay.
            if event.type == "prompt_request":
                pid = (event.data or {}).get("prompt_id")
                if pid:
                    self._pending_prompts[pid] = event.data
            # Typed broadcast: only to subscribers, filtered by topic.
            typed = self._classify_event(event)
            if typed is not None:
                typed_msg = json.dumps(typed)
                topic = typed.get("topic")
                for client in list(self._clients):
                    if client not in self._subscriptions:
                        continue
                    if topic not in self._subscriptions[client]:
                        continue
                    try:
                        await client.send(typed_msg)
                    except websockets.ConnectionClosed:
                        self._clients.discard(client)

    def _classify_event(self, event: Any) -> dict | None:
        """Map a bus event to a typed {topic, kind, data} envelope, or None
        to skip (event has no relevant typed projection)."""
        payload = event.data or {}
        kind = payload.get("kind")
        if kind in ("ticket_updated", "ticket_created"):
            return event_envelope("tickets", kind.removeprefix("ticket_"), payload)
        if kind in ("comment_posted",):
            return event_envelope("threads", "posted", payload)
        if event.type in (
            "agent_text", "agent_tool", "agent_tool_result", "agent_run",
            "agent_render",    # ConsoleStream output
            "agent_thinking",  # live thinking indicator (replaces \r spinner)
            "agent_start",     # role divider — TUI renders native full-width Rule
        ):
            return event_envelope(
                "agents", event.type.removeprefix("agent_"), payload
            )
        if event.type == "prompt_request":  # TuiPromptHandler request
            return event_envelope("prompts", "request", payload)
        if event.type in (
            "ticket_completed",
            "ticket_failed",
            "ticket_merge_conflict",
            "ticket_dispatched",
            "project_complete",
            "analysis_complete",
        ):
            return event_envelope("events", event.type, payload)
        # No typed projection for this event — drop it.
        # A deliberate event-tail will be added in Phase 3 once bus.recent exists.
        return None


def _thread_entry_to_wire(entry) -> dict:
    """Flatten a typed ThreadEntry into the TUI's comment payload shape.

    Preserves a ``content`` string (the human-visible text) and
    ``kind`` so existing TUI code keeps working. Per-type payload
    fields surface under their own keys where relevant (e.g.
    ``commit_sha`` for ``system_event``, ``question_id`` for
    ``answer``).
    """
    kind = entry.kind
    content_map = {
        "note": lambda e: e.text,
        "question": lambda e: e.question,
        "answer": lambda e: e.text,
        "decision": lambda e: e.decision,
        "resolution": lambda e: e.text,
        "waiver": lambda e: e.justification,
        "uncertain": lambda e: e.details,
        "escalation": lambda e: e.details,
        "objection": lambda e: e.text,
        "handoff": lambda e: e.summary,
        "proposal": lambda e: e.rationale,
        "system_event": lambda e: e.content,
    }
    content = content_map.get(kind, lambda _e: "")(entry)
    wire = {
        "id": entry.id,
        "ticket_id": entry.ticket_id,
        "author": entry.author,
        "content": content,
        "kind": kind,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }
    if kind == "system_event":
        wire["event_type"] = entry.event_type
        wire["commit_sha"] = entry.commit_sha
    elif kind == "answer":
        wire["question_id"] = entry.question_id
    elif kind == "question":
        wire["target"] = entry.target
        wire["blocking"] = entry.blocking
    return wire
