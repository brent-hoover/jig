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
from jig.ticket_mcp import (
    handle_answer_questions,
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
            await self._safe_send(websocket, json.dumps({"ok": False, "error": "bad json"}))
            return

        command = payload.get("command")
        args = payload.get("args", {})

        try:
            if command == "create_ticket":
                tid = await handle_create_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await self._safe_send(websocket, json.dumps({"ok": True, "ticket_id": tid}))
            elif command == "comment_on_ticket":
                cid = await handle_comment_on_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    sender_cfg=None,
                    args=args,
                )
                await self._safe_send(websocket, json.dumps({"ok": True, "comment_id": cid}))
            elif command == "update_ticket":
                updated = await handle_update_ticket(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
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
                await self._safe_send(websocket, json.dumps({
                    "ok": True,
                    "tickets": [
                        {
                            "id": t.id,
                            "type": t.type.value,
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
                }))
            elif command == "get_comments":
                ticket_id = args.get("ticket_id")
                if not ticket_id:
                    await self._safe_send(
                        websocket, json.dumps({"ok": False, "error": "ticket_id required"})
                    )
                    return
                found = await handle_read_comments(
                    comments=self._orch.comments,
                    ticket_id=ticket_id,
                    kind=args.get("kind"),
                )
                await self._safe_send(websocket, json.dumps({
                    "ok": True,
                    "comments": [
                        {
                            "id": c.id,
                            "ticket_id": c.ticket_id,
                            "author": c.author,
                            "content": c.content,
                            "kind": c.kind,
                            "created_at": c.created_at.isoformat() if c.created_at else None,
                            "commit_sha": c.commit_sha,
                        }
                        for c in found
                    ],
                }))
            elif command == "answer_questions":
                result = await handle_answer_questions(
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    bus=self._orch.bus,
                    sender="user",
                    args=args,
                )
                await self._safe_send(websocket, json.dumps({"ok": True, **result}))
            elif command == "list_agents":
                agents = list_roles(self._orch._project_path)
                await self._safe_send(websocket, json.dumps({
                    "ok": True,
                    "agents": [a.model_dump() for a in agents],
                }))
            elif command == "preview_prompt":
                from jig.runtime import AgentSpawnContext, SpawnReason
                ticket_id = args.get("ticket_id")
                role = args.get("role")
                if not ticket_id or not role:
                    await self._safe_send(
                        websocket, json.dumps({"ok": False, "error": "ticket_id and role required"})
                    )
                    return
                ticket = await self._orch.tickets.get(ticket_id)
                if ticket is None:
                    await self._safe_send(
                        websocket, json.dumps({"ok": False, "error": f"ticket {ticket_id} not found"})
                    )
                    return
                parent = None
                if ticket.parent_id:
                    parent = await self._orch.tickets.get(ticket.parent_id)
                role_cfg = load_role(self._orch._project_path, role)
                worktree_path = self._orch._project_path / ".jig" / "worktrees" / ticket_id
                ctx = AgentSpawnContext(
                    role=role,
                    role_cfg=role_cfg,
                    spawn_reason=SpawnReason.PHASE_PRIMARY,
                    ticket=ticket,
                    parent=parent,
                    worktree_path=worktree_path,
                    project=self._orch._project,
                    tickets=self._orch.tickets,
                    comments=self._orch.comments,
                    memory=self._orch.memory,
                    bus=self._orch.bus,
                )
                prompt = await build_agent_prompt(ctx)
                await self._safe_send(websocket, json.dumps({
                    "ok": True,
                    "prompt": prompt,
                    "system_prompt": role_cfg.phase_prompt,
                    "char_count": len(prompt),
                }))
            elif command == "get_workflow":
                name = args.get("name", "default")
                wf = load_workflow(self._orch._project_path, name)
                await self._safe_send(websocket, json.dumps({
                    "ok": True,
                    "workflow": wf.model_dump(),
                }))
            else:
                await self._safe_send(
                    websocket, json.dumps({"ok": False, "error": f"unknown command {command}"})
                )
        except Exception as exc:
            await self._safe_send(websocket, json.dumps({"ok": False, "error": str(exc)}))

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
