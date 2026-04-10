# tests/test_store_core.py
import pytest
from jig.store.core import JsonlStore


async def test_load_creates_missing_file_and_parent(tmp_path):
    path = tmp_path / "sub" / "store.jsonl"
    store = JsonlStore(path)
    await store.load()
    assert path.exists()
    assert path.read_text() == ""


async def test_insert_assigns_id_and_returns_it(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    doc_id = await store.insert({"name": "alice"})
    assert isinstance(doc_id, str)
    assert len(doc_id) > 0


async def test_insert_preserves_existing_id(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    doc_id = await store.insert({"_id": "fixed-id", "name": "alice"})
    assert doc_id == "fixed-id"


async def test_get_returns_inserted_document(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    doc_id = await store.insert({"name": "alice"})
    doc = await store.get(doc_id)
    assert doc is not None
    assert doc["name"] == "alice"
    assert doc["_id"] == doc_id


async def test_get_returns_none_for_missing_id(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    assert await store.get("nope") is None


async def test_insert_writes_record_to_file(tmp_path):
    path = tmp_path / "s.jsonl"
    store = JsonlStore(path)
    await store.load()
    await store.insert({"_id": "x", "name": "alice"})
    lines = path.read_text().splitlines()
    assert len(lines) == 1
    import json
    record = json.loads(lines[0])
    assert record == {"_op": "insert", "_id": "x", "name": "alice"}
