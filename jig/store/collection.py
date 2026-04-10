from pathlib import Path
from typing import Any, Callable

from jig.store.core import JsonlStore


class Collection:
    def __init__(
        self, path: Path, index_fields: list[str] | None = None
    ) -> None:
        self._store = JsonlStore(path, index_fields=index_fields)
        self._index_fields = list(index_fields or [])

    async def load(self) -> None:
        await self._store.load()

    async def insert(self, doc: dict) -> str:
        return await self._store.insert(doc)

    async def get(self, doc_id: str) -> dict | None:
        return await self._store.get(doc_id)

    async def find(
        self, predicate: Callable[[dict], bool] | None = None
    ) -> list[dict]:
        return await self._store.find(predicate)

    async def find_by(self, field: str, value: Any) -> list[dict]:
        return await self._store.find_by(field, value)

    async def update(self, doc_id: str, changes: dict) -> bool:
        return await self._store.update(doc_id, changes)

    async def delete(self, doc_id: str) -> bool:
        return await self._store.delete(doc_id)

    async def count(
        self, predicate: Callable[[dict], bool] | None = None
    ) -> int:
        return await self._store.count(predicate)

    async def find_where(self, **kwargs) -> list[dict]:
        if not kwargs:
            return await self.find()
        indexed = next(
            (k for k in kwargs if k in self._index_fields), None
        )
        if indexed is not None:
            candidates = await self._store.find_by(indexed, kwargs[indexed])
            remaining = {k: v for k, v in kwargs.items() if k != indexed}
            if not remaining:
                return candidates
            return [
                d for d in candidates
                if all(d.get(k) == v for k, v in remaining.items())
            ]
        # No indexed field — linear scan via find (subject to 1000-doc guard
        # only if find_by is used; find itself is unbounded)
        return [
            d for d in await self._store.find()
            if all(d.get(k) == v for k, v in kwargs.items())
        ]

    async def find_one_where(self, **kwargs) -> dict | None:
        results = await self.find_where(**kwargs)
        return results[0] if results else None

    async def upsert(self, match: dict, doc: dict) -> str:
        existing = await self.find_one_where(**match)
        if existing is not None:
            doc_id = existing["_id"]
            await self.update(doc_id, doc)
            return doc_id
        return await self.insert(doc)
