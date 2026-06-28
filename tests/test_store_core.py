# tests/test_store_core.py
import asyncio

import pytest
from jig.store.core import JsonlStore, RecordTooLargeError


async def test_load_is_serialized_against_concurrent_writes(tmp_path):
    """load() clears and rebuilds _docs/_indexes, so it must hold the write lock
    — otherwise a refresh of a live store interleaves with insert/update/delete
    and corrupts the in-memory map (KeyError, lost writes). Hammering reload
    against concurrent writers must stay consistent and exception-free."""
    store = JsonlStore(tmp_path / "s.jsonl", index_fields=["n"])
    await store.load()
    await store.insert({"_id": "a", "n": 0})

    async def writer():
        for i in range(1, 101):
            await store.update("a", {"n": i})

    async def reloader():
        for _ in range(100):
            await store.load()

    # No exception (a race would surface as a KeyError in load()'s update replay
    # or update()'s in-memory mutation), and the final value is exactly the
    # writer's last update — every op is on disk, so any later reload converges
    # on it (a lost-update interleave would leave a stale n).
    await asyncio.gather(writer(), reloader(), reloader())
    doc = await store.get("a")
    assert doc is not None
    assert doc["n"] == 100


async def test_load_leaves_live_state_intact_on_replay_error(tmp_path):
    """A reload of a live store whose file is corrupt must raise without
    damaging the current in-memory state (no empty/half-rebuilt store)."""
    path = tmp_path / "s.jsonl"
    store = JsonlStore(path, index_fields=["n"])
    await store.load()
    await store.insert({"_id": "a", "n": 1})

    # Append a malformed line, then reload: it must raise but keep the good doc.
    with path.open("a") as f:
        f.write("{ not valid json\n")
    with pytest.raises(ValueError, match="malformed JSON"):
        await store.load()

    doc = await store.get("a")
    assert doc is not None
    assert doc["n"] == 1
    # Index still consistent (not emptied by the failed reload).
    assert await store.find_by("n", 1) == [{"_id": "a", "n": 1}]


async def test_insert_rejects_oversize_record(tmp_path):
    """SEC-I2: per-record cap stops a single agent call from writing
    a multi-megabyte blob into the store."""
    store = JsonlStore(tmp_path / "s.jsonl", max_record_bytes=1024)
    await store.load()
    # 2 KiB payload comfortably exceeds the 1 KiB cap.
    big = "x" * 2048
    with pytest.raises(RecordTooLargeError, match="exceeds 1024 bytes"):
        await store.insert({"text": big})
    # No record was written.
    assert (tmp_path / "s.jsonl").read_text() == ""


async def test_update_rejects_oversize_record(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl", max_record_bytes=1024)
    await store.load()
    doc_id = await store.insert({"text": "small"})
    with pytest.raises(RecordTooLargeError):
        await store.update(doc_id, {"text": "x" * 2048})
    # Original value preserved.
    doc = await store.get(doc_id)
    assert doc is not None
    assert doc["text"] == "small"


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


async def test_delete_removes_doc_and_returns_true(tmp_path):
    store = JsonlStore(tmp_path / "s.jsonl", index_fields=["status"])
    await store.load()
    doc_id = await store.insert({"name": "a", "status": "on"})
    ok = await store.delete(doc_id)
    assert ok is True
    assert await store.get(doc_id) is None
    assert await store.find_by("status", "on") == []


async def test_delete_missing_id_returns_false_and_writes_nothing(tmp_path):
    path = tmp_path / "s.jsonl"
    store = JsonlStore(path)
    await store.load()
    before = path.read_text()
    assert await store.delete("nope") is False
    assert path.read_text() == before


async def test_load_replays_insert_update_delete(tmp_path):
    path = tmp_path / "s.jsonl"
    store1 = JsonlStore(path, index_fields=["status"])
    await store1.load()
    a_id = await store1.insert({"name": "a", "status": "on"})
    b_id = await store1.insert({"name": "b", "status": "on"})
    await store1.update(a_id, {"status": "off"})
    await store1.delete(b_id)

    store2 = JsonlStore(path, index_fields=["status"])
    await store2.load()
    assert await store2.get(a_id) == {"_id": a_id, "name": "a", "status": "off"}
    assert await store2.get(b_id) is None
    assert await store2.find_by("status", "off") == [
        {"_id": a_id, "name": "a", "status": "off"}
    ]
    assert await store2.find_by("status", "on") == []


async def test_load_raises_on_update_for_unknown_id(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"_op": "update", "_id": "ghost", "x": 1}\n')
    store = JsonlStore(path)
    with pytest.raises(ValueError, match="update for unknown"):
        await store.load()


async def test_load_raises_on_delete_for_unknown_id(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"_op": "delete", "_id": "ghost"}\n')
    store = JsonlStore(path)
    with pytest.raises(ValueError, match="delete for unknown"):
        await store.load()


async def test_load_raises_on_update_after_delete(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(
        '{"_op": "insert", "_id": "a", "x": 1}\n'
        '{"_op": "delete", "_id": "a"}\n'
        '{"_op": "update", "_id": "a", "x": 2}\n'
    )
    store = JsonlStore(path)
    with pytest.raises(ValueError, match="update for unknown"):
        await store.load()


async def test_load_raises_on_malformed_json_with_line_number(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"_op": "insert", "_id": "x"}\n{not valid json\n')
    store = JsonlStore(path)
    with pytest.raises(ValueError, match="line 2"):
        await store.load()


async def test_load_raises_when_op_missing(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"_id": "x", "name": "a"}\n')
    store = JsonlStore(path)
    with pytest.raises(ValueError, match="line 1"):
        await store.load()


async def test_load_raises_when_id_missing(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text('{"_op": "insert", "name": "a"}\n')
    store = JsonlStore(path)
    with pytest.raises(ValueError, match="line 1"):
        await store.load()


def test_public_api_exports():
    from jig.store import (
        JsonlStore,
    )

    # If all imports succeed, the test passes
    assert JsonlStore is not None


async def test_get_returns_copy_not_live_reference(tmp_path):
    store = JsonlStore(tmp_path / "db.jsonl")
    await store.load()
    doc_id = await store.insert({"name": "a", "count": 1})
    fetched = await store.get(doc_id)
    fetched["count"] = 999
    fetched_again = await store.get(doc_id)
    assert fetched_again["count"] == 1
