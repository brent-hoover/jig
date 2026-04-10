# jig/store/core.py
import asyncio
import json
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
