from __future__ import annotations

import asyncio
import json
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any, TypedDict

import click
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from jig.issues.discovery import find_project_root
from jig.issues.service import IssueService
from jig.ticket import Ticket, TicketStatus


class ColumnWire(TypedDict):
    status: str
    label: str


class TicketWire(TypedDict):
    key: str
    id: str
    status: str
    work_type: str
    size: str
    title: str
    description: str
    assignee: str | None
    labels: list[str]
    blocked_by: list[str]
    blocks: list[str]
    parent_id: str | None
    created_by: str
    created_at: str
    updated_at: str


class BoardWire(TypedDict):
    columns: list[ColumnWire]
    tickets: list[TicketWire]


class CreateIssueInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    work_type: str = "feature"
    size: str = "m"
    labels: list[str] = Field(default_factory=list)


_COLUMNS: tuple[tuple[TicketStatus, str], ...] = (
    (TicketStatus.PROPOSED, "Proposed"),
    (TicketStatus.OPEN, "Open"),
    (TicketStatus.IN_PROGRESS, "In Progress"),
    (TicketStatus.NEEDS_INFO, "Needs Info"),
    (TicketStatus.BLOCKED, "Blocked"),
    (TicketStatus.MERGE_CONFLICT, "Merge Conflict"),
    (TicketStatus.FAILED, "Failed"),
    (TicketStatus.RESOLVED, "Resolved"),
    (TicketStatus.CLOSED, "Closed"),
)


def _ticket_wire(ticket: Ticket) -> TicketWire:
    return {
        "key": ticket.key,
        "id": ticket.id,
        "status": ticket.status.value,
        "work_type": ticket.work_type.value,
        "size": ticket.size.value,
        "title": ticket.title,
        "description": ticket.description,
        "assignee": ticket.assignee,
        "labels": list(ticket.labels),
        "blocked_by": list(ticket.blocked_by),
        "blocks": list(ticket.blocks),
        "parent_id": ticket.parent_id,
        "created_by": ticket.created_by,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }


async def load_board(project_root: Path) -> BoardWire:
    service = IssueService(project_root)
    tickets = await service.list()
    ordered = sorted(
        tickets, key=lambda ticket: (ticket.status.value, ticket.created_at)
    )
    return {
        "columns": [
            {"status": status.value, "label": label} for status, label in _COLUMNS
        ],
        "tickets": [_ticket_wire(ticket) for ticket in ordered],
    }


async def create_issue(project_root: Path, payload: dict[str, Any]) -> TicketWire:
    parsed = CreateIssueInput.model_validate(payload)
    service = IssueService(project_root)
    ticket = await service.create(
        title=parsed.title,
        work_type=parsed.work_type,
        description=parsed.description,
        size=parsed.size,
        labels=parsed.labels,
        created_by="web",
    )
    return _ticket_wire(ticket)


def create_issue_board_server(
    project_root: Path, *, host: str, port: int
) -> ThreadingHTTPServer:
    handler = partial(_IssueBoardHandler, project_root=project_root)
    return ThreadingHTTPServer((host, port), handler)


def serve_issue_board(project_root: Path, *, host: str, port: int) -> None:
    server = create_issue_board_server(project_root, host=host, port=port)
    click.echo(f"Serving issue board at http://{host}:{server.server_port}/")
    with server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            click.echo("\nshutting down")


@click.command("board", help="Serve the issue Kanban board as a local Web View.")
@click.option(
    "--path",
    default=None,
    type=click.Path(exists=True, path_type=Path),
    help="Project root. Default: walk up from the current directory.",
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=8776, type=int, show_default=True)
def board_cmd(path: Path | None, host: str, port: int) -> None:
    root = path
    if root is None:
        try:
            root = find_project_root(Path.cwd())
        except FileNotFoundError as exc:
            raise click.ClickException(str(exc)) from exc
    serve_issue_board(root, host=host, port=port)


class _IssueBoardHandler(BaseHTTPRequestHandler):
    server_version = "JigIssueBoard/0.1"

    def __init__(self, *args, project_root: Path, **kwargs) -> None:
        self._project_root = project_root
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args) -> None:
        return

    def do_GET(self) -> None:
        if self.path in {"/", "/index.html"}:
            self._send_text(
                HTTPStatus.OK, _issue_board_html(), "text/html; charset=utf-8"
            )
            return
        if self.path == "/api/issues":
            self._send_json(HTTPStatus.OK, asyncio.run(load_board(self._project_root)))
            return
        self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path != "/api/issues":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            payload = self._read_json()
            ticket = asyncio.run(create_issue(self._project_root, payload))
        except json.JSONDecodeError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except ValidationError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        except ValueError as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.CREATED, ticket)

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode()
        decoded = json.loads(raw or "{}")
        if not isinstance(decoded, dict):
            raise ValueError("expected a JSON object")
        return decoded

    def _send_json(self, status: HTTPStatus, payload: Any) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: HTTPStatus, body: str, content_type: str) -> None:
        encoded = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _issue_board_html() -> str:
    return files("jig.issues").joinpath("issue_board.html").read_text()
