"""Hidden behavioral tests for the ``todo_api`` task.

Per-test isolation: each test re-imports the candidate's ``app`` module
with ``cwd`` set to a fresh ``tmp_path``, so file-backed storage starts
empty and module-level state doesn't bleed across tests.

Status-code checks are by *class* (2xx for success, 4xx for client error)
rather than exact codes — 201 vs 200 and 204 vs 200 are conventions the
developer picks, not behaviors.
"""

import sys
from pathlib import Path

import pytest


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    import app as user_app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    return TestClient(user_app.app)


def _is_2xx(response) -> bool:
    return response.status_code // 100 == 2


def _is_4xx(response) -> bool:
    return response.status_code // 100 == 4


# --- create ----------------------------------------------------------------


def test_creating_an_item_returns_success_with_id_and_text(client) -> None:
    response = client.post("/todos", json={"text": "buy milk"})
    assert _is_2xx(response), response.text
    body = response.json()
    assert "id" in body
    assert body["text"] == "buy milk"
    assert body["done"] is False


def test_created_item_appears_in_listing(client) -> None:
    item_id = client.post("/todos", json={"text": "buy milk"}).json()["id"]
    response = client.get("/todos")
    assert _is_2xx(response)
    assert any(item["id"] == item_id for item in response.json())


def test_creating_multiple_items_preserves_them_all(client) -> None:
    for text in ("buy milk", "feed cat", "read book"):
        client.post("/todos", json={"text": text})
    items = client.get("/todos").json()
    assert {item["text"] for item in items} == {"buy milk", "feed cat", "read book"}


# --- read ------------------------------------------------------------------


def test_fetching_an_existing_item_by_id_works(client) -> None:
    item_id = client.post("/todos", json={"text": "buy milk"}).json()["id"]
    response = client.get(f"/todos/{item_id}")
    assert _is_2xx(response)
    assert response.json()["text"] == "buy milk"


def test_fetching_a_missing_item_is_a_client_error(client) -> None:
    response = client.get("/todos/nonexistent-id-12345")
    assert _is_4xx(response)


# --- update ----------------------------------------------------------------


def test_marking_an_item_complete_persists(client) -> None:
    item_id = client.post("/todos", json={"text": "buy milk"}).json()["id"]
    update = client.put(f"/todos/{item_id}", json={"done": True})
    assert _is_2xx(update)
    fetched = client.get(f"/todos/{item_id}").json()
    assert fetched["done"] is True


def test_changing_an_items_text_persists(client) -> None:
    item_id = client.post("/todos", json={"text": "buy milk"}).json()["id"]
    update = client.put(f"/todos/{item_id}", json={"text": "buy oat milk"})
    assert _is_2xx(update)
    fetched = client.get(f"/todos/{item_id}").json()
    assert fetched["text"] == "buy oat milk"


def test_updating_a_missing_item_is_a_client_error(client) -> None:
    response = client.put("/todos/nonexistent-id-12345", json={"done": True})
    assert _is_4xx(response)


# --- filter ----------------------------------------------------------------


def test_filtering_by_status_open_excludes_completed(client) -> None:
    open_id = client.post("/todos", json={"text": "open one"}).json()["id"]
    done_id = client.post("/todos", json={"text": "done one"}).json()["id"]
    client.put(f"/todos/{done_id}", json={"done": True})

    response = client.get("/todos?status=open")
    assert _is_2xx(response)
    ids = {item["id"] for item in response.json()}
    assert open_id in ids
    assert done_id not in ids


def test_filtering_by_status_done_excludes_open(client) -> None:
    open_id = client.post("/todos", json={"text": "open one"}).json()["id"]
    done_id = client.post("/todos", json={"text": "done one"}).json()["id"]
    client.put(f"/todos/{done_id}", json={"done": True})

    response = client.get("/todos?status=done")
    assert _is_2xx(response)
    ids = {item["id"] for item in response.json()}
    assert done_id in ids
    assert open_id not in ids


# --- delete ----------------------------------------------------------------


def test_deleting_an_existing_item_succeeds(client) -> None:
    item_id = client.post("/todos", json={"text": "buy milk"}).json()["id"]
    response = client.delete(f"/todos/{item_id}")
    assert _is_2xx(response)


def test_deleted_item_disappears_from_listing_and_reads(client) -> None:
    item_id = client.post("/todos", json={"text": "buy milk"}).json()["id"]
    client.delete(f"/todos/{item_id}")

    listed = client.get("/todos").json()
    assert all(item["id"] != item_id for item in listed)

    fetched = client.get(f"/todos/{item_id}")
    assert _is_4xx(fetched)


def test_deleting_a_missing_item_is_a_client_error(client) -> None:
    response = client.delete("/todos/nonexistent-id-12345")
    assert _is_4xx(response)


# --- persistence -----------------------------------------------------------


def test_state_survives_a_fresh_module_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verifies the candidate persists to disk, not just an in-memory
    structure that survives because the module didn't reload."""
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    import app as user_app  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415

    first = TestClient(user_app.app)
    item_id = first.post("/todos", json={"text": "buy milk"}).json()["id"]

    sys.modules.pop("app", None)
    import app as reloaded_app  # noqa: PLC0415

    second = TestClient(reloaded_app.app)
    response = second.get(f"/todos/{item_id}")
    assert _is_2xx(response), (
        "expected the item to survive a fresh module import — looks like "
        "state is in-memory rather than file-backed"
    )
