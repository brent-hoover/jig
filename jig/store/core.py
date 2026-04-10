# jig/store/core.py
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable


class JsonlStore:
    def __init__(self, path: Path, index_fields: list[str] | None = None) -> None:
        self._path = path
        self._index_fields = list(index_fields or [])
        self._docs: dict[str, dict] = {}
        self._indexes: dict[str, dict[Any, set[str]]] = {
            field: {} for field in self._index_fields
        }
        self._lock = asyncio.Lock()
        self._loaded = False

    async def load(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        self._loaded = True

    def _append_line(self, record: dict) -> None:
        with self._path.open("a") as f:
            f.write(json.dumps(record) + "\n")

    def _index_insert(self, doc: dict) -> None:
        for field in self._index_fields:
            if field in doc:
                self._indexes[field].setdefault(doc[field], set()).add(doc["_id"])

    def _index_remove(self, doc: dict) -> None:
        for field in self._index_fields:
            if field in doc:
                bucket = self._indexes[field].get(doc[field])
                if bucket is not None:
                    bucket.discard(doc["_id"])
                    if not bucket:
                        del self._indexes[field][doc[field]]

    async def insert(self, doc: dict) -> str:
        async with self._lock:
            if "_id" not in doc:
                doc = {**doc, "_id": str(uuid.uuid4())}
            record = {"_op": "insert", **doc}
            await asyncio.to_thread(self._append_line, record)
            stored = dict(doc)
            self._docs[doc["_id"]] = stored
            self._index_insert(stored)
            return doc["_id"]

    async def get(self, doc_id: str) -> dict | None:
        return self._docs.get(doc_id)

    async def find(
        self, predicate: Callable[[dict], bool] | None = None
    ) -> list[dict]:
        if predicate is None:
            return list(self._docs.values())
        return [d for d in self._docs.values() if predicate(d)]

    async def count(
        self, predicate: Callable[[dict], bool] | None = None
    ) -> int:
        if predicate is None:
            return len(self._docs)
        return sum(1 for d in self._docs.values() if predicate(d))

    async def delete(self, doc_id: str) -> bool:
        async with self._lock:
            current = self._docs.get(doc_id)
            if current is None:
                return False
            record = {"_op": "delete", "_id": doc_id}
            await asyncio.to_thread(self._append_line, record)
            self._index_remove(current)
            del self._docs[doc_id]
            return True

    async def update(self, doc_id: str, changes: dict) -> bool:
        async with self._lock:
            current = self._docs.get(doc_id)
            if current is None:
                return False
            # Drop any attempt to overwrite _id
            changes = {k: v for k, v in changes.items() if k != "_id"}
            record = {"_op": "update", "_id": doc_id, **changes}
            await asyncio.to_thread(self._append_line, record)
            self._index_remove(current)
            current.update(changes)
            self._index_insert(current)
            return True

    async def find_by(self, field: str, value: Any) -> list[dict]:
        if field in self._index_fields:
            ids = self._indexes[field].get(value, set())
            return [self._docs[i] for i in ids]
        if len(self._docs) > 1000:
            raise ValueError(
                f"field {field!r} is not indexed and collection has "
                f"{len(self._docs)} docs; declare it in index_fields"
            )
        return [d for d in self._docs.values() if d.get(field) == value]
