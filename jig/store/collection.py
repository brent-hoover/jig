from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, TypeAlias

if TYPE_CHECKING:
    from jig.store.models import StoreModel, TypedCollection

from jig.store.core import JsonlStore


class Collection:
    def __init__(self, path: Path, index_fields: list[str] | None = None) -> None:
        self._store = JsonlStore(path, index_fields=index_fields)
        self._index_fields = list(index_fields or [])

    async def load(self) -> None:
        await self._store.load()

    async def insert(self, doc: dict) -> str:
        return await self._store.insert(doc)

    async def get(self, doc_id: str) -> dict | None:
        return await self._store.get(doc_id)

    async def find(self, predicate: Callable[[dict], bool] | None = None) -> list[dict]:
        return await self._store.find(predicate)

    async def find_by(self, field: str, value: Any) -> list[dict]:
        return await self._store.find_by(field, value)

    async def update(self, doc_id: str, changes: dict) -> bool:
        return await self._store.update(doc_id, changes)

    async def delete(self, doc_id: str) -> bool:
        return await self._store.delete(doc_id)

    async def count(self, predicate: Callable[[dict], bool] | None = None) -> int:
        return await self._store.count(predicate)

    async def find_where(self, **kwargs) -> list[dict]:
        if not kwargs:
            return await self.find()
        indexed = next((k for k in kwargs if k in self._index_fields), None)
        if indexed is not None:
            candidates = await self._store.find_by(indexed, kwargs[indexed])
            remaining = {k: v for k, v in kwargs.items() if k != indexed}
            if not remaining:
                return candidates
            return [
                d
                for d in candidates
                if all(d.get(k) == v for k, v in remaining.items())
            ]
        # No indexed field — linear scan via find (subject to 1000-doc guard
        # only if find_by is used; find itself is unbounded)
        return [
            d
            for d in await self._store.find()
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


# Type-checking-only union: statically a database collection may be the untyped
# ``Collection`` or a ``TypedCollection`` (the two are unrelated classes). At
# runtime the alias collapses to ``Collection`` alone, so this is for annotations
# only — do NOT use ``DatabaseCollection`` as an ``isinstance`` target, as that
# would silently miss ``TypedCollection`` instances.
if TYPE_CHECKING:
    DatabaseCollection: TypeAlias = Collection | TypedCollection[StoreModel]
else:
    DatabaseCollection: TypeAlias = Collection


class Database:
    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path
        self._collections: dict[str, tuple[DatabaseCollection, tuple, type | None]] = {}

    async def collection(
        self,
        name: str,
        index_fields: list[str] | None = None,
        model: type[StoreModel] | None = None,
    ) -> DatabaseCollection:
        key_index = tuple(index_fields or [])
        if name in self._collections:
            cached, cached_index, cached_model = self._collections[name]
            if index_fields is not None and cached_index != key_index:
                raise ValueError(
                    f"collection {name!r} already cached with different "
                    f"index_fields {list(cached_index)} != {list(key_index)}"
                )
            if model is not None and cached_model is not model:
                raise ValueError(
                    f"collection {name!r} already cached with a different model"
                )
            return cached

        path = self._base_path / f"{name}.jsonl"
        col: DatabaseCollection
        if model is None:
            col = Collection(path, index_fields=index_fields)
        else:
            # Imported lazily so `collection.py` stays pydantic-free at import time
            from jig.store.models import TypedCollection

            col = TypedCollection(path, model=model, index_fields=index_fields)
        await col.load()
        self._collections[name] = (col, key_index, model)
        return col
