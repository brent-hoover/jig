from datetime import datetime, timezone

from jig.store.models import StoreModel, TypedCollection


class Widget(StoreModel):
    name: str
    count: int = 0


def test_storemodel_assigns_id_by_default():
    w = Widget(name="a")
    assert isinstance(w.id, str)
    assert len(w.id) > 0


def test_storemodel_dumps_id_as_underscore_id():
    w = Widget(name="a", id="fixed")
    assert w.model_dump(by_alias=True)["_id"] == "fixed"


def test_storemodel_roundtrip_via_alias():
    w1 = Widget(name="a", id="x")
    as_dict = w1.model_dump(by_alias=True)
    w2 = Widget.model_validate(as_dict)
    assert w2.id == "x"
    assert w2.name == "a"


class Item(StoreModel):
    name: str
    status: str


class ItemWithTime(StoreModel):
    name: str
    when: datetime


async def test_typed_collection_insert_get_roundtrip(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item, index_fields=["status"])
    await col.load()
    doc_id = await col.insert(Item(name="a", status="on"))
    fetched = await col.get(doc_id)
    assert isinstance(fetched, Item)
    assert fetched.name == "a"
    assert fetched.id == doc_id


async def test_typed_collection_find_returns_models(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item, index_fields=["status"])
    await col.load()
    await col.insert(Item(name="a", status="on"))
    await col.insert(Item(name="b", status="on"))
    results = await col.find()
    assert all(isinstance(r, Item) for r in results)
    assert {r.name for r in results} == {"a", "b"}


async def test_typed_collection_find_where_returns_models(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item, index_fields=["status"])
    await col.load()
    await col.insert(Item(name="a", status="on"))
    await col.insert(Item(name="b", status="off"))
    results = await col.find_where(status="on")
    assert len(results) == 1
    assert results[0].name == "a"


async def test_typed_collection_find_one_where(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item, index_fields=["status"])
    await col.load()
    await col.insert(Item(name="a", status="on"))
    hit = await col.find_one_where(status="on")
    miss = await col.find_one_where(status="nope")
    assert hit.name == "a"
    assert miss is None


async def test_typed_collection_update_takes_plain_dict(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item, index_fields=["status"])
    await col.load()
    doc_id = await col.insert(Item(name="a", status="on"))
    await col.update(doc_id, {"status": "off"})
    fetched = await col.get(doc_id)
    assert fetched.status == "off"


async def test_typed_collection_delete(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item)
    await col.load()
    doc_id = await col.insert(Item(name="a", status="on"))
    assert await col.delete(doc_id) is True
    assert await col.get(doc_id) is None


async def test_typed_collection_upsert_with_model(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=Item, index_fields=["name"])
    await col.load()
    id1 = await col.upsert({"name": "a"}, Item(name="a", status="on"))
    id2 = await col.upsert({"name": "a"}, Item(name="a", status="off"))
    assert id1 == id2
    fetched = await col.get(id1)
    assert fetched.status == "off"


async def test_typed_collection_mode_json_handles_datetime(tmp_path):
    col = TypedCollection(tmp_path / "items.jsonl", model=ItemWithTime)
    await col.load()
    now = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
    doc_id = await col.insert(ItemWithTime(name="a", when=now))

    # Re-open and verify round-trip
    col2 = TypedCollection(tmp_path / "items.jsonl", model=ItemWithTime)
    await col2.load()
    fetched = await col2.get(doc_id)
    assert fetched.when == now


async def test_typed_collection_update_serializes_datetime(tmp_path):
    """Regression test: update() must serialize non-JSON values like datetime.

    Previously, raw datetime values passed into update() would crash at
    json.dumps in the underlying JsonlStore. This test confirms that update()
    serializes non-JSON-primitive values via pydantic_core.to_jsonable_python
    before passing to the underlying collection.
    """
    col = TypedCollection(tmp_path / "items.jsonl", model=ItemWithTime)
    await col.load()
    doc_id = await col.insert(
        ItemWithTime(name="a", when=datetime(2020, 1, 1, tzinfo=timezone.utc))
    )

    # Pass a raw datetime to update — must not raise
    new_when = datetime(2030, 1, 1, tzinfo=timezone.utc)
    await col.update(doc_id, {"when": new_when})

    # Verify the value was persisted correctly
    fetched = await col.get(doc_id)
    assert fetched.when == new_when
