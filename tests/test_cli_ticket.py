"""Tests for `jig ticket create`.

The CLI opens a WebSocket to a running orchestrator, sends a
`create_ticket` command, and prints the returned ticket_id. We spin up
a real `WebSocketServer` + `Orchestrator` on an ephemeral port (same
pattern as `test_tui_can_create_ticket_via_ws`) and invoke the CLI via
`CliRunner`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.events import EventEmitter
from jig.orchestrator import Orchestrator
from jig.project import Project, save_project
from jig.ws_server import WebSocketServer


async def _invoke_cli(args: list[str]):
    """Run the CLI in a worker thread.

    The CLI uses ``asyncio.run`` to drive its WebSocket client, which
    blows up if called from the pytest-asyncio event loop thread. A real
    ``jig`` invocation runs in its own process, so there's no loop yet —
    we reproduce that condition here via ``to_thread``.
    """
    runner = CliRunner()
    return await asyncio.to_thread(runner.invoke, cli, args)


@pytest.fixture
async def orchestrator_ws(tmp_path: Path):
    """Bring up a real orchestrator + WS server on an ephemeral port."""
    save_project(tmp_path, Project(id="p", name="p", path=str(tmp_path)))
    orch = Orchestrator(project_path=tmp_path)
    await orch.startup()
    emitter = EventEmitter()
    server = WebSocketServer(
        emitter=emitter, host="127.0.0.1", port=0, orchestrator=orch
    )
    await server.start()
    try:
        yield orch, server
    finally:
        await server.stop()
        await orch.shutdown()


@pytest.mark.asyncio
async def test_ticket_create_basic(orchestrator_ws) -> None:
    orch, server = orchestrator_ws
    result = await _invoke_cli(
        [
            "ticket",
            "create",
            "--title",
            "cli ticket",
            "--work-type",
            "feature",
            "--size",
            "s",
            "--description",
            "from the cli",
            "--ws-url",
            f"ws://127.0.0.1:{server.port}",
        ]
    )
    assert result.exit_code == 0, result.output
    # The CLI's sole stdout line is the ticket_id so callers can pipe it.
    ticket_id = result.output.strip()
    assert ticket_id
    tickets = await orch.tickets.list_all()
    assert any(t.id == ticket_id and t.title == "cli ticket" for t in tickets), (
        f"ticket {ticket_id!r} not found in store"
    )


@pytest.mark.asyncio
async def test_ticket_create_defaults(orchestrator_ws) -> None:
    """work-type and size should have sensible defaults."""
    orch, server = orchestrator_ws
    result = await _invoke_cli(
        [
            "ticket",
            "create",
            "--title",
            "defaulted",
            "--ws-url",
            f"ws://127.0.0.1:{server.port}",
        ]
    )
    assert result.exit_code == 0, result.output
    ticket_id = result.output.strip()
    ticket = await orch.tickets.get(ticket_id)
    assert ticket is not None
    assert ticket.work_type.value == "feature"
    assert ticket.size.value == "m"
    assert ticket.title == "defaulted"


@pytest.mark.asyncio
async def test_ticket_create_description_file(orchestrator_ws, tmp_path: Path) -> None:
    """--description-file reads the body from disk (for multi-line briefs)."""
    orch, server = orchestrator_ws
    brief = tmp_path / "brief.md"
    brief.write_text("## Goal\n\nAdd the thing.\n\n## Acceptance\n\n- it works\n")
    result = await _invoke_cli(
        [
            "ticket",
            "create",
            "--title",
            "from-file",
            "--description-file",
            str(brief),
            "--ws-url",
            f"ws://127.0.0.1:{server.port}",
        ]
    )
    assert result.exit_code == 0, result.output
    ticket_id = result.output.strip()
    ticket = await orch.tickets.get(ticket_id)
    assert ticket is not None
    assert "Add the thing." in ticket.description
    assert "- it works" in ticket.description


@pytest.mark.asyncio
async def test_ticket_create_description_and_file_conflict(
    orchestrator_ws, tmp_path: Path
) -> None:
    """--description and --description-file together is a user error."""
    _, server = orchestrator_ws
    brief = tmp_path / "brief.md"
    brief.write_text("body\n")
    result = await _invoke_cli(
        [
            "ticket",
            "create",
            "--title",
            "conflict",
            "--description",
            "inline",
            "--description-file",
            str(brief),
            "--ws-url",
            f"ws://127.0.0.1:{server.port}",
        ]
    )
    assert result.exit_code != 0
    # Usage error shows on stderr (mixed into output by CliRunner).
    assert "description" in result.output.lower()


def test_ticket_create_no_server() -> None:
    """Clean error when the orchestrator isn't running, not a traceback."""
    # No active event loop here — plain synchronous invoke is fine.
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "ticket",
            "create",
            "--title",
            "no-server",
            # Port 1 — guaranteed not bound.
            "--ws-url",
            "ws://127.0.0.1:1",
        ],
    )
    assert result.exit_code != 0
    assert (
        "could not connect" in result.output.lower()
        or "refused" in result.output.lower()
    )


@pytest.mark.asyncio
async def test_ticket_create_server_error(orchestrator_ws) -> None:
    """Backend rejection surfaces as a CLI error, not success."""
    _, server = orchestrator_ws
    # work-type is the required field on the backend; force rejection by
    # passing an invalid size. Click's Choice stops most bad values at
    # the CLI layer, so we go around it with an unrecognized size —
    # sizes aren't constrained by Choice (a design trade-off) and the
    # backend will reject 'jumbo'.
    result = await _invoke_cli(
        [
            "ticket",
            "create",
            "--title",
            "bad-size",
            "--size",
            "jumbo",
            "--ws-url",
            f"ws://127.0.0.1:{server.port}",
        ]
    )
    # Either Click validates (exit != 0) or the server does — both acceptable,
    # but the message must mention the field.
    assert result.exit_code != 0
    assert "size" in result.output.lower()
