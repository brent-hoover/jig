"""WebSocket server for broadcasting events to TUI clients."""

import asyncio
import json
import os
from typing import TYPE_CHECKING

import websockets
from websockets.asyncio.server import serve, ServerConnection

from jig.agent import build_agent_prompt
from jig.events import EventEmitter
from jig.persistence import list_roles, load_role, load_workflow
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


class WebSocketServer:
    def __init__(
        self,
        emitter: EventEmitter,
        host: str = "0.0.0.0" if os.environ.get("JIG_IN_CONTAINER") else "127.0.0.1",
        port: int = 9100,
        orchestrator: "Orchestrator | None" = None,
    ) -> None:
        self._emitter = emitter
        self._host = host
        self._port = port
        self._orch = orchestrator
        self._server = None
        self._relay_task = None
        self._clients: set[ServerConnection] = set()
        self._queue = emitter.subscribe()
        self._history: list[str] = []

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
        # Replay event history to late-joining clients
        for message in self._history:
            try:
                await websocket.send(message)
            except websockets.ConnectionClosed:
                return
        self._clients.add(websocket)
        try:
            async for raw in websocket:
                await self._handle_incoming(websocket, raw)
        except websockets.ConnectionClosed:
            pass
        finally:
            self._clients.discard(websocket)

    async def _safe_send(self, websocket: ServerConnection, message: str) -> None:
        """Send a reply, swallowing ConnectionClosed so a dying client
        doesn't bubble a traceback through ``_handle_client``."""
        try:
            await websocket.send(message)
        except websockets.ConnectionClosed:
            return

    async def _handle_incoming(self, websocket: ServerConnection, raw: str) -> None:
        if self._orch is None:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            await self._safe_send(
                websocket, json.dumps({"ok": False, "error": "bad json"})
            )
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
                worktree_path = (
                    self._orch._project_path / ".jig" / "worktrees" / ticket_id
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

    async def _relay_events(self) -> None:
        while True:
            event = await self._queue.get()
            message = event.to_json()
            self._history.append(message)
            for client in list(self._clients):
                try:
                    await client.send(message)
                except websockets.ConnectionClosed:
                    self._clients.discard(client)


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
