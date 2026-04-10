# tests/test_store_collection.py
import pytest
from jig.store.collection import Collection


async def test_collection_basic_crud_delegates_to_store(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=["status"])
    await col.load()
    doc_id = await col.insert({"name": "a", "status": "on"})
    assert (await col.get(doc_id))["name"] == "a"
    assert await col.count() == 1


async def test_find_where_single_field_uses_index(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=["status"])
    await col.load()
    await col.insert({"name": "a", "status": "on"})
    await col.insert({"name": "b", "status": "off"})
    results = await col.find_where(status="on")
    assert [d["name"] for d in results] == ["a"]


async def test_find_where_multiple_fields_filters_after_index(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=["status"])
    await col.load()
    await col.insert({"name": "a", "status": "on", "type": "dev"})
    await col.insert({"name": "b", "status": "on", "type": "qa"})
    await col.insert({"name": "c", "status": "off", "type": "dev"})
    results = await col.find_where(status="on", type="dev")
    assert [d["name"] for d in results] == ["a"]


async def test_find_where_no_indexed_field_falls_through(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=[])
    await col.load()
    await col.insert({"name": "a", "x": 1})
    await col.insert({"name": "b", "x": 2})
    results = await col.find_where(x=2)
    assert [d["name"] for d in results] == ["b"]


async def test_find_one_where_hit_and_miss(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=["status"])
    await col.load()
    await col.insert({"name": "a", "status": "on"})
    hit = await col.find_one_where(status="on")
    miss = await col.find_one_where(status="nope")
    assert hit["name"] == "a"
    assert miss is None
