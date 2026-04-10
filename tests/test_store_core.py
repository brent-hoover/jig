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


async def test_find_returns_all_when_no_predicate(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    await store.insert({"name": "a"})
    await store.insert({"name": "b"})
    docs = await store.find()
    assert len(docs) == 2


async def test_find_filters_by_predicate(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    await store.insert({"name": "a", "age": 10})
    await store.insert({"name": "b", "age": 20})
    await store.insert({"name": "c", "age": 30})
    docs = await store.find(lambda d: d["age"] >= 20)
    assert sorted(d["name"] for d in docs) == ["b", "c"]


async def test_count_with_and_without_predicate(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    await store.insert({"x": 1})
    await store.insert({"x": 2})
    await store.insert({"x": 3})
    assert await store.count() == 3
    assert await store.count(lambda d: d["x"] >= 2) == 2


async def test_find_by_uses_index(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl", index_fields=["status"])
    await store.load()
    await store.insert({"name": "a", "status": "on"})
    await store.insert({"name": "b", "status": "off"})
    await store.insert({"name": "c", "status": "on"})
    on = await store.find_by("status", "on")
    assert sorted(d["name"] for d in on) == ["a", "c"]


async def test_find_by_unindexed_small_collection_falls_through(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl", index_fields=[])
    await store.load()
    await store.insert({"x": 1})
    await store.insert({"x": 2})
    results = await store.find_by("x", 1)
    assert len(results) == 1
    assert results[0]["x"] == 1


async def test_find_by_unindexed_large_collection_raises(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl", index_fields=[])
    await store.load()
    for i in range(1001):
        await store.insert({"x": i})
    with pytest.raises(ValueError, match="not indexed"):
        await store.find_by("x", 500)


async def test_update_merges_fields_and_returns_true(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl")
    await store.load()
    doc_id = await store.insert({"name": "a", "age": 10})
    ok = await store.update(doc_id, {"age": 11})
    assert ok is True
    doc = await store.get(doc_id)
    assert doc == {"_id": doc_id, "name": "a", "age": 11}


async def test_update_missing_id_returns_false_and_writes_nothing(tmp_path):
    path = tmp_path / "s.jsonl"
    store = JsonlStore(path)
    await store.load()
    before = path.read_text()
    assert await store.update("nope", {"x": 1}) is False
    assert path.read_text() == before


async def test_update_reindexes_when_indexed_field_changes(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl", index_fields=["status"])
    await store.load()
    doc_id = await store.insert({"name": "a", "status": "on"})
    await store.update(doc_id, {"status": "off"})
    on = await store.find_by("status", "on")
    off = await store.find_by("status", "off")
    assert on == []
    assert len(off) == 1 and off[0]["name"] == "a"
