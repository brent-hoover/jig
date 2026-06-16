from __future__ import annotations

import contextlib
import http.client
import json
import threading
from collections.abc import Iterator
from pathlib import Path

from jig.issues.web import create_issue_board_server
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    return tmp_path


@contextlib.contextmanager
def _server(root: Path) -> Iterator[int]:
    server = create_issue_board_server(root, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(
    port: int,
    method: str,
    path: str,
    *,
    body: dict[str, str] | None = None,
    raw_body: str | None = None,
    content_type: str | None = "application/json",
) -> tuple[int, str, str]:
    if body is not None:
        encoded = json.dumps(body).encode()
    elif raw_body is not None:
        encoded = raw_body.encode()
    else:
        encoded = None
    headers = {"Content-Type": content_type} if content_type is not None else {}
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


def test_issue_board_rejects_malformed_json_create_requests(tmp_path: Path) -> None:
    with _server(_project(tmp_path)) as port:
        status, content_type, body = _request(
            port, "POST", "/api/issues", raw_body="{not-json"
        )

    assert status == 400
    assert content_type.startswith("application/json")
    assert "Expecting property name" in body


def test_issue_board_rejects_empty_create_requests(tmp_path: Path) -> None:
    with _server(_project(tmp_path)) as port:
        status, content_type, body = _request(port, "POST", "/api/issues", raw_body="")

    assert status == 400
    assert content_type.startswith("application/json")
    assert "request body is required" in body


def test_issue_board_rejects_create_requests_with_missing_fields(
    tmp_path: Path,
) -> None:
    with _server(_project(tmp_path)) as port:
        status, content_type, body = _request(port, "POST", "/api/issues", body={})

    assert status == 400
    assert content_type.startswith("application/json")
    assert "Field required" in body


def test_issue_board_rejects_create_requests_with_bad_work_type(
    tmp_path: Path,
) -> None:
    with _server(_project(tmp_path)) as port:
        status, content_type, body = _request(
            port,
            "POST",
            "/api/issues",
            body={
                "title": "bad type",
                "description": TICKET_AC_PLACEHOLDER,
                "work_type": "nonsense",
            },
        )

    assert status == 400
    assert content_type.startswith("application/json")
    assert "Unknown work_type" in body


def test_issue_board_returns_json_404_for_unknown_paths(tmp_path: Path) -> None:
    with _server(_project(tmp_path)) as port:
        get_status, get_content_type, get_body = _request(
            port, "GET", "/api/other", content_type=None
        )
        post_status, post_content_type, post_body = _request(
            port, "POST", "/api/other", body={"title": "x"}
        )

    assert (get_status, post_status) == (404, 404)
    assert get_content_type.startswith("application/json")
    assert post_content_type.startswith("application/json")
    assert json.loads(get_body) == {"error": "not found"}
    assert json.loads(post_body) == {"error": "not found"}


def test_issue_board_returns_json_500_for_corrupt_ticket_store(
    tmp_path: Path,
) -> None:
    root = _project(tmp_path)
    (root / ".jig" / "store" / "tickets.jsonl").write_text("not-json\n")

    with _server(root) as port:
        status, content_type, body = _request(
            port, "GET", "/api/issues", content_type=None
        )

    assert status == 500
    assert content_type.startswith("application/json")
    assert "malformed JSON" in body
