import uuid
from typing import Any, Callable, Generic, TypeVar
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from jig.store.collection import Collection


class StoreModel(BaseModel):
    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        alias="_id",
    )
    model_config = ConfigDict(populate_by_name=True)
