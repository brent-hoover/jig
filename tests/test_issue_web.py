from __future__ import annotations

import http.client
import json
import threading
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from jig.issues.service import IssueService
from jig.issues.web import create_issue_board_server
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    return tmp_path


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    encoded = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if encoded is not None else {}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        conn.request(method, path, body=encoded, headers=headers)
        response = conn.getresponse()
        return (
            response.status,
            response.getheader("Content-Type", ""),
            response.read().decode(),
        )
    finally:
        conn.close()


def test_issue_board_serves_kanban_and_creates_proposed_issue(tmp_path: Path) -> None:
    root = _project(tmp_path)
    svc = IssueService(root)

    async def given_tickets() -> None:
        await svc.create(
            title="triage candidate",
            work_type="feature",
            description=TICKET_AC_PLACEHOLDER,
        )
        open_ticket = await svc.create(
            title="approved work",
            work_type="feature",
            description=TICKET_AC_PLACEHOLDER,
        )
        await svc.approve(open_ticket.key)

    import asyncio

    asyncio.run(given_tickets())
    server = create_issue_board_server(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        port = int(server.server_port)

        status, content_type, html = _request(port, "GET", "/")
        assert status == 200
        assert content_type.startswith("text/html")
        assert "Jig Issue Board" in html
        assert 'id="board"' in html

        status, content_type, body = _request(port, "GET", "/api/issues")
        assert status == 200
        assert content_type.startswith("application/json")
        data = json.loads(body)
        assert [c["status"] for c in data["columns"]][:2] == ["proposed", "open"]
        assert {t["title"]: t["status"] for t in data["tickets"]} == {
            "triage candidate": "proposed",
            "approved work": "open",
        }

        status, content_type, body = _request(
            port,
            "POST",
            "/api/issues",
            body={
                "title": "from board",
                "work_type": "feature",
                "size": "s",
                "description": TICKET_AC_PLACEHOLDER,
            },
        )
        assert status == 201
        assert content_type.startswith("application/json")
        created = json.loads(body)
        assert created["title"] == "from board"
        assert created["status"] == "proposed"

        status, _, body = _request(port, "GET", "/api/issues")
        assert status == 200
        refreshed = json.loads(body)
        assert any(t["title"] == "from board" for t in refreshed["tickets"])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_issue_board_command_is_registered() -> None:
    result = CliRunner().invoke(cli, ["issue", "board", "--help"])
    assert result.exit_code == 0, result.output
    assert "Serve the issue Kanban board" in result.output
