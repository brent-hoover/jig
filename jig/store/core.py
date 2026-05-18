# jig/store/core.py
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Callable


# Default per-record byte cap for JSONL stores. 1 MiB is generous
# enough for typical thread entries, ticket payloads, and analytics
# events while being small enough to refuse a malicious agent that
# tries to write a multi-megabyte blob in one call (SEC-I2 in
# v2-review-findings-security.md). Stores that legitimately need
# larger records can pass ``max_record_bytes=...``.
DEFAULT_MAX_RECORD_BYTES: int = 1_048_576


class RecordTooLargeError(ValueError):
    """Raised when an insert / update record exceeds the store's cap."""


class JsonlStore:
    def __init__(
        self,
        path: Path,
        index_fields: list[str] | None = None,
        *,
        max_record_bytes: int = DEFAULT_MAX_RECORD_BYTES,
    ) -> None:
        self._path = path
        self._index_fields = list(index_fields or [])
        self._docs: dict[str, dict] = {}
        self._indexes: dict[str, dict[Any, set[str]]] = {
            field: {} for field in self._index_fields
        }
        self._lock = asyncio.Lock()
        self._loaded = False
        self._max_record_bytes = max_record_bytes

    async def load(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.touch(exist_ok=True)
        self._docs.clear()
        for field in self._index_fields:
            self._indexes[field] = {}
        live_ids: set[str] = set()
        with self._path.open("r") as f:
            for line_no, raw in enumerate(f, start=1):
                line = raw.rstrip("\n")
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as e:
                    raise ValueError(
                        f"{self._path}: malformed JSON on line {line_no}: {e}"
                    ) from e
                if "_op" not in record:
                    raise ValueError(f"{self._path}: missing _op on line {line_no}")
                if "_id" not in record:
                    raise ValueError(f"{self._path}: missing _id on line {line_no}")
                op = record["_op"]
                doc_id = record["_id"]
                if op == "insert":
                    doc = {k: v for k, v in record.items() if k != "_op"}
                    self._docs[doc_id] = doc
                    live_ids.add(doc_id)
                elif op == "update":
                    if doc_id not in live_ids:
                        raise ValueError(
                            f"{self._path}:{line_no}: update for unknown id {doc_id!r}"
                        )
                    current = self._docs[doc_id]
                    changes = {
                        k: v for k, v in record.items() if k not in ("_op", "_id")
                    }
                    current.update(changes)
                elif op == "delete":
                    if doc_id not in live_ids:
                        raise ValueError(
                            f"{self._path}:{line_no}: delete for unknown id {doc_id!r}"
                        )
                    self._docs.pop(doc_id, None)
                    live_ids.discard(doc_id)
                else:
                    raise ValueError(
                        f"{self._path}: unknown _op {op!r} on line {line_no}"
                    )
        for doc in self._docs.values():
            self._index_insert(doc)
        self._loaded = True

    def _append_line(self, record: dict) -> None:
        line = json.dumps(record)
        if len(line.encode("utf-8")) > self._max_record_bytes:
            raise RecordTooLargeError(
                f"{self._path.name}: record exceeds "
                f"{self._max_record_bytes} bytes "
                f"(_id={record.get('_id')!r})"
            )
        with self._path.open("a") as f:
            f.write(line + "\n")

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
            elif doc["_id"] in self._docs:
                # Caller supplied an explicit id that already exists.
                # Enforce uniqueness here, under the same lock that
                # guards the in-memory map and the JSONL append, so
                # the check-then-insert is atomic against concurrent
                # callers (no TOCTOU window).
                raise ValueError(f"document with _id {doc['_id']!r} already exists")
            record = {"_op": "insert", **doc}
            await asyncio.to_thread(self._append_line, record)
            stored = dict(doc)
            self._docs[doc["_id"]] = stored
            self._index_insert(stored)
            return doc["_id"]

    async def get(self, doc_id: str) -> dict | None:
        if doc_id not in self._docs:
            return None
        return dict(self._docs[doc_id])

    async def find(self, predicate: Callable[[dict], bool] | None = None) -> list[dict]:
        if predicate is None:
            return [dict(d) for d in self._docs.values()]
        return [dict(d) for d in self._docs.values() if predicate(d)]

    async def count(self, predicate: Callable[[dict], bool] | None = None) -> int:
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
            return [dict(self._docs[i]) for i in ids]
        if len(self._docs) > 1000:
            raise ValueError(
                f"field {field!r} is not indexed and collection has "
                f"{len(self._docs)} docs; declare it in index_fields"
            )
        return [dict(d) for d in self._docs.values() if d.get(field) == value]
