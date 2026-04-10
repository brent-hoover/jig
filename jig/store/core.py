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

    async def insert(self, doc: dict) -> str:
        async with self._lock:
            if "_id" not in doc:
                doc = {**doc, "_id": str(uuid.uuid4())}
            record = {"_op": "insert", **doc}
            await asyncio.to_thread(self._append_line, record)
            self._docs[doc["_id"]] = dict(doc)
            # index updates come in Task 5
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
