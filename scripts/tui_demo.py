#!/usr/bin/env python3
"""Fake jig daemon for TUI visual debugging.

Usage (two terminals):

    Terminal 1 (project dir):  uv run scripts/tui_demo.py
    Terminal 2 (project dir):  jig

Press SPACE or ENTER to advance through scenes.
Press Q to quit the demo (also kills Terminal 1 server).

The script writes a daemon.addr file so the TUI connects automatically.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import termios
import tty
from pathlib import Path

import websockets
from websockets.asyncio.server import ServerConnection, serve


# ── Scene definitions ──────────────────────────────────────────────────────

TICKET_ID = "aa11bb22"
TICKET_ID2 = "cc33dd44"
TICKET_TITLE = "Add user authentication endpoint"
TICKET_TITLE2 = "Write integration tests for auth"


def _ev(topic: str, kind: str, data: dict) -> dict:
    return {"type": "event", "topic": topic, "kind": kind, "data": data}


def _snap(topic: str, data) -> dict:
    return {"type": "snapshot", "topic": topic, "data": data}


TICKET_OPEN = {
    "id": TICKET_ID,
    "title": TICKET_TITLE,
    "status": "open",
    "type": "feature",
    "size": "M",
    "assignee": None,
    "description": "Implement JWT-based auth with /login and /me endpoints.",
    "depends_on": [],
    "parent_id": None,
    "created_at": "2026-05-09T18:00:00Z",
    "updated_at": "2026-05-09T18:00:00Z",
}

TICKET_OPEN2 = {
    "id": TICKET_ID2,
    "title": TICKET_TITLE2,
    "status": "open",
    "type": "feature",
    "size": "S",
    "assignee": None,
    "description": "Integration tests for the auth endpoints.",
    "depends_on": [TICKET_ID],
    "parent_id": None,
    "created_at": "2026-05-09T18:00:05Z",
    "updated_at": "2026-05-09T18:00:05Z",
}

TICKET_DISPATCHED = dict(TICKET_OPEN) | {"status": "dispatched", "assignee": "spec"}
TICKET_RESOLVED = dict(TICKET_OPEN) | {"status": "resolved"}
TICKET_FAILED = dict(TICKET_OPEN) | {"status": "failed"}

SCENES: list[tuple[str, list[dict]]] = [
    # 0 ── empty state (no tickets, no activity)
    (
        "Empty state",
        [],  # snapshots sent on connect; no extra events
    ),
    # 1 ── ticket created (snapshot update)
    (
        "Ticket created",
        [
            _ev("tickets", "created", TICKET_OPEN),
        ],
    ),
    # 2 ── second ticket created
    (
        "Second ticket created",
        [
            _ev("tickets", "created", TICKET_OPEN2),
        ],
    ),
    # 3 ── ticket dispatched to spec agent
    (
        "Ticket dispatched → spec",
        [
            _ev("events", "ticket_dispatched", {
                "ticket_id": TICKET_ID,
                "ticket_title": TICKET_TITLE,
                "role": "spec",
            }),
            _ev("tickets", "updated", TICKET_DISPATCHED),
        ],
    ),
    # 4 ── spec agent starts
    (
        "Agent start (spec)",
        [
            _ev("agents", "start", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "ticket_title": TICKET_TITLE,
                "phase": "spec",
            }),
        ],
    ),
    # 5 ── agent thinking
    (
        "Agent thinking",
        [
            _ev("agents", "thinking", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "elapsed": 3,
                "active": True,
            }),
        ],
    ),
    # 6 ── agent reads a file
    (
        "Agent tool use (Read)",
        [
            _ev("agents", "tool", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "tool": "Read",
                "detail": "jig/models.py",
            }),
        ],
    ),
    # 7 ── tool result
    (
        "Agent tool result",
        [
            _ev("agents", "tool_result", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "tool": "Read",
                "is_error": False,
                "excerpt": "1  from __future__ import annotations\n2  from pydantic import BaseModel\n...",
            }),
        ],
    ),
    # 8 ── agent writes some text output
    (
        "Agent text output",
        [
            _ev("agents", "text", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "text": (
                    "I'll draft the spec for the auth endpoint. "
                    "The ticket calls for JWT-based authentication with /login and /me "
                    "endpoints. I'll define the request/response shapes and the "
                    "acceptance criteria now."
                ),
            }),
        ],
    ),
    # 8b ── PM text with embedded Markdown table + Unicode ambiguous-width
    # chars. Verbatim from a real hn-cli planning run that triggers the
    # scrollback corruption (truncated narrative, "kets"/"ing"/"by-s"
    # fragments bleeding into the sidebar gutter). Single event with
    # narrative + table on the same line — no newlines.
    (
        "PM planning text (corruption repro)",
        [
            _ev("agents", "text", {
                "role": "pm",
                "ticket_id": TICKET_ID,
                "text": (
                    "Now I have the full picture. Let me draft the plan "
                    "and post it for approval."
                ),
            }),
            _ev("agents", "text", {
                "role": "pm",
                "ticket_id": TICKET_ID,
                "text": (
                    "Here's the plan I've drafted: **4 tickets, linear "
                    "chain** (≤2 tickets → no parallelism needed): "
                    "| # | Title | Type | Size | Depends on | "
                    "|---|-------|------|------|------------| "
                    "| 1 | Core: project setup + `hn-cli top --limit N` "
                    "| feature | m | — | "
                    "| 2 | Filtering: `--min-score` and `--type` flags "
                    "| feature | s | 1 | "
                    "| 3 | JSON output: `--format json` | feature | s | 2 | "
                    "| 4 | Integration validation | feature | "
                    "m (validation) | 3 |"
                ),
            }),
        ],
    ),
    # 9 ── agent uses WebSearch
    (
        "Agent tool use (WebSearch)",
        [
            _ev("agents", "tool", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "tool": "WebSearch",
                "detail": "FastAPI JWT authentication best practices 2025",
            }),
            _ev("agents", "thinking", {
                "role": "spec",
                "ticket_id": TICKET_ID,
                "elapsed": 12,
                "active": True,
            }),
        ],
    ),
    # 10 ── phase transition: test agent starts
    (
        "Phase change → test agent",
        [
            _ev("agents", "start", {
                "role": "test",
                "ticket_id": TICKET_ID,
                "ticket_title": TICKET_TITLE,
                "phase": "test",
            }),
        ],
    ),
    # 11 ── test agent writes files
    (
        "Test agent writing files",
        [
            _ev("agents", "tool", {
                "role": "test",
                "ticket_id": TICKET_ID,
                "tool": "Write",
                "detail": "tests/test_auth.py",
            }),
            _ev("agents", "text", {
                "role": "test",
                "ticket_id": TICKET_ID,
                "text": "Writing the failing test suite for the /login endpoint...",
            }),
        ],
    ),
    # 12 ── implement agent starts (parallel: two agents active)
    (
        "Two parallel agents",
        [
            _ev("agents", "start", {
                "role": "implement",
                "ticket_id": TICKET_ID,
                "ticket_title": TICKET_TITLE,
                "phase": "implement",
            }),
            _ev("agents", "thinking", {
                "role": "implement",
                "ticket_id": TICKET_ID,
                "elapsed": 2,
                "active": True,
            }),
            _ev("agents", "thinking", {
                "role": "test",
                "ticket_id": TICKET_ID,
                "elapsed": 45,
                "active": True,
            }),
        ],
    ),
    # 13 ── prompt: brief approval
    (
        "Prompt: brief approval",
        [
            _ev("prompts", "request", {
                "prompt_id": "p-brief-001",
                "prompt_type": "brief_approval",
                "question": "Here is the project brief I've drafted. Does this look right?",
                "options": [
                    {"key": "y", "label": "Approve", "default": True},
                    {"key": "n", "label": "Request changes"},
                ],
                "rendered": (
                    "## Project Brief\n\n"
                    "**Goal**: Build a REST API with JWT authentication.\n\n"
                    "**Scope**:\n- POST /login — issue JWT tokens\n"
                    "- GET /me — return current user\n\n"
                    "**Stack**: FastAPI, python-jose, SQLAlchemy\n\n"
                    "**Out of scope**: OAuth2 flows, refresh tokens (v2)"
                ),
            }),
        ],
    ),
    # 14 ── prompt: branch choice
    (
        "Prompt: branch choice",
        [
            _ev("prompts", "request", {
                "prompt_id": "p-branch-002",
                "prompt_type": "branch_choice",
                "question": "Which branch should I base this work on?",
                "options": [
                    {"key": "1", "label": "main", "default": True},
                    {"key": "2", "label": "develop"},
                    {"key": "3", "label": "feature/auth-v2"},
                ],
                "rendered": (
                    "I found the following branches. Pick the one to base this ticket on:\n\n"
                    "- **main** (default, 3 commits behind develop)\n"
                    "- **develop** (active, up to date)\n"
                    "- **feature/auth-v2** (WIP, 14 commits ahead of develop)"
                ),
            }),
        ],
    ),
    # 15 ── prompt: question from PM/agent
    (
        "Prompt: agent question",
        [
            _ev("prompts", "request", {
                "prompt_id": "p-qa-003",
                "prompt_type": "question_answer",
                "question": (
                    "Should the /me endpoint return the full user object "
                    "(including email, created_at) or just the user ID?"
                ),
                "options": [
                    {"key": "f", "label": "Full user object", "default": True},
                    {"key": "i", "label": "ID only"},
                    {"key": "l", "label": "Let me decide later"},
                ],
                "rendered": None,
            }),
        ],
    ),
    # 16 ── ticket completed
    (
        "Ticket completed",
        [
            _ev("events", "ticket_completed", {
                "ticket_id": TICKET_ID,
                "ticket_title": TICKET_TITLE,
            }),
            _ev("tickets", "updated", TICKET_RESOLVED),
        ],
    ),
    # 17 ── second ticket dispatched and then failed
    (
        "Ticket failed",
        [
            _ev("events", "ticket_dispatched", {
                "ticket_id": TICKET_ID2,
                "ticket_title": TICKET_TITLE2,
                "role": "test",
            }),
            _ev("events", "ticket_failed", {
                "ticket_id": TICKET_ID2,
                "ticket_title": TICKET_TITLE2,
                "reason": "test suite exited with code 1 after 3 retries",
            }),
            _ev("tickets", "updated", dict(TICKET_OPEN2) | {"status": "failed"}),
        ],
    ),
    # 18 ── merge conflict
    (
        "Merge conflict",
        [
            _ev("events", "ticket_merge_conflict", {
                "ticket_id": TICKET_ID2,
                "ticket_title": TICKET_TITLE2,
                "conflicted_files": ["src/auth.py", "tests/test_auth.py"],
            }),
        ],
    ),
]


# ── WebSocket server ───────────────────────────────────────────────────────

class DemoServer:
    def __init__(self) -> None:
        self._clients: set[ServerConnection] = set()
        self._scene_idx = 0
        # Build initial snapshots (empty — before any scenes play)
        self._ticket_snapshot: list[dict] = []
        self._events_snapshot: list[dict] = []

    async def handler(self, ws: ServerConnection) -> None:
        self._clients.add(ws)
        try:
            # Send initial snapshots for all subscribed topics
            for msg in self._build_initial_snapshots():
                await ws.send(json.dumps(msg))
            # Re-play all events up to current scene so late-joiners are caught up
            for i in range(1, self._scene_idx + 1):
                for evt in SCENES[i][1]:
                    await ws.send(json.dumps(evt))
            # Now just keep the connection alive until it closes
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Honor subscribe messages silently (TUI always sends one)
                if msg.get("type") == "subscribe":
                    pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._clients.discard(ws)

    def _build_initial_snapshots(self) -> list[dict]:
        return [
            _snap("tickets", self._ticket_snapshot),
            _snap("agents", []),
            _snap("events", self._events_snapshot),
            _snap("prompts", []),
            _snap("spec", None),
        ]

    async def advance(self) -> None:
        if self._scene_idx >= len(SCENES) - 1:
            print(f"\r[demo] already at last scene ({self._scene_idx})", flush=True)
            return
        self._scene_idx += 1
        label, events = SCENES[self._scene_idx]
        print(f"\r[demo] scene {self._scene_idx}/{len(SCENES)-1}: {label}", flush=True)
        for evt in events:
            await self._broadcast(evt)

    async def _broadcast(self, msg: dict) -> None:
        dead: list[ServerConnection] = []
        for ws in list(self._clients):
            try:
                await ws.send(json.dumps(msg))
            except websockets.exceptions.ConnectionClosed:
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)


# ── Keyboard input (raw stdin in a thread) ────────────────────────────────

async def _keyboard_loop(
    server: DemoServer,
    advance_queue: asyncio.Queue[bool],
) -> None:
    """Drain advance_queue items: True = quit, False = advance."""
    while True:
        quit_signal = await advance_queue.get()
        if quit_signal:
            print("\r[demo] quit", flush=True)
            # Cancel all tasks to exit cleanly
            for task in asyncio.all_tasks():
                if task is not asyncio.current_task():
                    task.cancel()
            return
        await server.advance()


def _read_stdin(loop: asyncio.AbstractEventLoop, q: asyncio.Queue[bool]) -> None:
    """Blocking stdin reader in a thread. Puts True=quit, False=advance."""
    while True:
        ch = sys.stdin.read(1)
        if not ch or ch in ("q", "Q", "\x03"):  # q / ctrl+c
            asyncio.run_coroutine_threadsafe(q.put(True), loop)
            break
        if ch in (" ", "\r", "\n"):
            asyncio.run_coroutine_threadsafe(q.put(False), loop)


# ── Main ──────────────────────────────────────────────────────────────────

async def main(project_path: Path) -> None:
    server = DemoServer()
    advance_queue: asyncio.Queue[bool] = asyncio.Queue()

    run_dir = project_path / ".jig" / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    addr_file = run_dir / "daemon.addr"
    pid_file = run_dir / "daemon.pid"

    async with serve(server.handler, "127.0.0.1", 0) as ws_server:
        port = ws_server.sockets[0].getsockname()[1]
        addr = f"ws://127.0.0.1:{port}"
        # Write PID first so daemon_status() sees a live process and jig
        # doesn't overwrite daemon.addr by starting a real daemon.
        pid_file.write_text(str(os.getpid()))
        addr_file.write_text(addr)
        print(f"[demo] WS server on {addr}", flush=True)
        print(f"[demo] addr written to {addr_file}", flush=True)
        print(f"[demo] {len(SCENES)} scenes — SPACE/ENTER to advance, Q to quit", flush=True)
        print(f"[demo] scene 0/{len(SCENES)-1}: {SCENES[0][0]}", flush=True)

        loop = asyncio.get_running_loop()

        # Raw mode so we get keypresses without waiting for Enter
        old_settings = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin.fileno())
        try:
            # Run stdin reader in a thread
            import concurrent.futures
            executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            loop.run_in_executor(executor, _read_stdin, loop, advance_queue)

            await _keyboard_loop(server, advance_queue)
        except asyncio.CancelledError:
            pass
        finally:
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, old_settings)
            addr_file.unlink(missing_ok=True)
            pid_file.unlink(missing_ok=True)
            print("\r[demo] cleaned up", flush=True)


if __name__ == "__main__":
    project_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.cwd()
    try:
        asyncio.run(main(project_dir))
    except KeyboardInterrupt:
        pass
