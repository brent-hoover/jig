import uuid
from typing import Any, Callable, Generic, TypeVar
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from pydantic_core import to_jsonable_python

from jig.store.collection import Collection


class StoreModel(BaseModel):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        alias="_id",
    )
    model_config = ConfigDict(populate_by_name=True)


T = TypeVar("T", bound=StoreModel)


class TypedCollection(Generic[T]):
    def __init__(
        self,
        path: Path,
        model: type[T],
        index_fields: list[str] | None = None,
    ) -> None:
        self._collection = Collection(path, index_fields=index_fields)
        self._model = model

    async def load(self) -> None:
        await self._collection.load()

    def _to_dict(self, doc: T) -> dict:
        return doc.model_dump(mode="json", by_alias=True)

    def _to_model(self, raw: dict) -> T:
        return self._model.model_validate(raw)

    async def insert(self, doc: T) -> str:
        return await self._collection.insert(self._to_dict(doc))

    async def get(self, doc_id: str) -> T | None:
        raw = await self._collection.get(doc_id)
        return None if raw is None else self._to_model(raw)

    async def find(
        self, predicate: Callable[[T], bool] | None = None
    ) -> list[T]:
        raws = await self._collection.find()
        models = [self._to_model(r) for r in raws]
        if predicate is None:
            return models
        return [m for m in models if predicate(m)]

    async def find_where(self, **kwargs) -> list[T]:
        raws = await self._collection.find_where(**kwargs)
        return [self._to_model(r) for r in raws]

    async def find_one_where(self, **kwargs) -> T | None:
        raw = await self._collection.find_one_where(**kwargs)
        return None if raw is None else self._to_model(raw)

    async def update(self, doc_id: str, changes: dict) -> bool:
        serialized = {k: to_jsonable_python(v) for k, v in changes.items()}
        return await self._collection.update(doc_id, serialized)

    async def delete(self, doc_id: str) -> bool:
        return await self._collection.delete(doc_id)

    async def upsert(self, match: dict, doc: T) -> str:
        return await self._collection.upsert(match, self._to_dict(doc))
