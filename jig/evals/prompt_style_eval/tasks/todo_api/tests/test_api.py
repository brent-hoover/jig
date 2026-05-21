"""Hidden behavioral tests for the ``todo_api`` task.

Tests interact with the candidate's server exclusively through ``client.py``
(provided to the candidate verbatim). The client encodes the API contract;
the tests check behaviors. No raw URLs or HTTP details appear here.

Per-test isolation: each test reimports ``app`` with cwd set to a fresh
``tmp_path``, so file-backed storage starts empty and module-level state
doesn't bleed.
"""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def todo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    import app as user_app  # noqa: PLC0415
    from client import TodoClient  # noqa: PLC0415

    http = TestClient(user_app.app)
    client = TodoClient(http_client=http)
    yield client
    client.close()


# --- create ----------------------------------------------------------------


def test_create_returns_item_with_id_text_and_open_status(todo) -> None:
    item = todo.create("buy milk")
    assert "id" in item
    assert item["text"] == "buy milk"
    assert item["done"] is False


def test_created_item_appears_in_list(todo) -> None:
    created = todo.create("buy milk")
    assert any(item["id"] == created["id"] for item in todo.list())


def test_creating_multiple_items_preserves_them_all(todo) -> None:
    for text in ("buy milk", "feed cat", "read book"):
        todo.create(text)
    items = todo.list()
    assert {item["text"] for item in items} == {"buy milk", "feed cat", "read book"}


# --- read ------------------------------------------------------------------


def test_get_returns_the_item(todo) -> None:
    created = todo.create("buy milk")
    fetched = todo.get(created["id"])
    assert fetched["text"] == "buy milk"


def test_get_missing_item_raises(todo) -> None:
    from client import ItemNotFound  # noqa: PLC0415

    with pytest.raises(ItemNotFound):
        todo.get("nonexistent-id-12345")


# --- update ----------------------------------------------------------------


def test_mark_done_persists(todo) -> None:
    item = todo.create("buy milk")
    todo.mark_done(item["id"])
    assert todo.get(item["id"])["done"] is True


def test_update_text_persists(todo) -> None:
    item = todo.create("buy milk")
    todo.update_text(item["id"], "buy oat milk")
    assert todo.get(item["id"])["text"] == "buy oat milk"


def test_mark_done_can_unset(todo) -> None:
    item = todo.create("buy milk")
    todo.mark_done(item["id"], True)
    todo.mark_done(item["id"], False)
    assert todo.get(item["id"])["done"] is False


def test_update_on_missing_item_raises(todo) -> None:
    from client import ItemNotFound  # noqa: PLC0415

    with pytest.raises(ItemNotFound):
        todo.mark_done("nonexistent-id-12345")


# --- filter ----------------------------------------------------------------


def test_filter_status_open_excludes_completed(todo) -> None:
    open_item = todo.create("open one")
    done_item = todo.create("done one")
    todo.mark_done(done_item["id"])

    open_ids = {item["id"] for item in todo.list(status="open")}
    assert open_item["id"] in open_ids
    assert done_item["id"] not in open_ids


def test_filter_status_done_excludes_open(todo) -> None:
    open_item = todo.create("open one")
    done_item = todo.create("done one")
    todo.mark_done(done_item["id"])

    done_ids = {item["id"] for item in todo.list(status="done")}
    assert done_item["id"] in done_ids
    assert open_item["id"] not in done_ids


# --- delete ----------------------------------------------------------------


def test_delete_removes_item_from_list_and_reads(todo) -> None:
    from client import ItemNotFound  # noqa: PLC0415

    item = todo.create("buy milk")
    todo.delete(item["id"])

    assert all(other["id"] != item["id"] for other in todo.list())
    with pytest.raises(ItemNotFound):
        todo.get(item["id"])


def test_delete_missing_item_raises(todo) -> None:
    from client import ItemNotFound  # noqa: PLC0415

    with pytest.raises(ItemNotFound):
        todo.delete("nonexistent-id-12345")


# --- persistence -----------------------------------------------------------


def test_items_survive_a_fresh_module_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The candidate must persist to disk, not just an in-memory structure
    that survives because the module wasn't reloaded."""
    monkeypatch.chdir(tmp_path)
    sys.modules.pop("app", None)
    import app as first_app  # noqa: PLC0415
    from client import TodoClient  # noqa: PLC0415

    first = TodoClient(http_client=TestClient(first_app.app))
    item = first.create("buy milk")
    first.close()

    sys.modules.pop("app", None)
    import app as reloaded_app  # noqa: PLC0415

    second = TodoClient(http_client=TestClient(reloaded_app.app))
    fetched = second.get(item["id"])
    second.close()
    assert fetched["text"] == "buy milk"
