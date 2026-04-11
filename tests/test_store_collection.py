# tests/test_store_collection.py
import pytest
from jig.store.collection import Collection, Database
from jig.store.models import StoreModel, TypedCollection


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


async def test_upsert_inserts_when_no_match(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=["name"])
    await col.load()
    doc_id = await col.upsert({"name": "a"}, {"name": "a", "age": 10})
    assert isinstance(doc_id, str)
    fetched = await col.get(doc_id)
    assert fetched == {"_id": doc_id, "name": "a", "age": 10}


async def test_upsert_updates_when_match_exists(tmp_path):
    col = Collection(tmp_path / "c.jsonl", index_fields=["name"])
    await col.load()
    original_id = await col.insert({"name": "a", "age": 10})
    returned_id = await col.upsert({"name": "a"}, {"age": 11})
    assert returned_id == original_id
    fetched = await col.get(original_id)
    assert fetched["age"] == 11


async def test_database_collection_creates_and_caches(tmp_path):
    db = Database(tmp_path)
    col1 = await db.collection("agents", index_fields=["status"])
    col2 = await db.collection("agents")
    assert col1 is col2


async def test_database_collection_file_location(tmp_path):
    db = Database(tmp_path)
    await db.collection("agents")
    assert (tmp_path / "agents.jsonl").exists()


async def test_database_collection_mismatched_index_fields_raises(tmp_path):
    db = Database(tmp_path)
    await db.collection("agents", index_fields=["status"])
    with pytest.raises(ValueError, match="already cached"):
        await db.collection("agents", index_fields=["type"])


async def test_database_crash_recovery_round_trip(tmp_path):
    db1 = Database(tmp_path)
    col = await db1.collection("agents", index_fields=["status"])
    doc_id = await col.insert({"name": "a", "status": "on"})

    db2 = Database(tmp_path)
    col2 = await db2.collection("agents", index_fields=["status"])
    fetched = await col2.get(doc_id)
    assert fetched["name"] == "a"


class Thing(StoreModel):
    name: str


async def test_database_collection_with_model_returns_typed(tmp_path):
    db = Database(tmp_path)
    col = await db.collection("things", model=Thing)
    assert isinstance(col, TypedCollection)
    doc_id = await col.insert(Thing(name="a"))
    fetched = await col.get(doc_id)
    assert isinstance(fetched, Thing)
    assert fetched.name == "a"


async def test_database_collection_mismatched_model_raises(tmp_path):
    class Other(StoreModel):
        name: str

    db = Database(tmp_path)
    await db.collection("things", model=Thing)
    with pytest.raises(ValueError, match="different model"):
        await db.collection("things", model=Other)
