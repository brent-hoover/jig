### app.py

```python
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

_STORE = Path("todos.json")


class TodoIn(BaseModel):
    text: str


class TodoUpdate(BaseModel):
    text: str | None = None
    done: bool | None = None


class Todo(BaseModel):
    id: str
    text: str
    done: bool


def _load() -> list[dict]:
    if not _STORE.exists():
        return []
    return json.loads(_STORE.read_text(encoding="utf-8"))


def _save(items: list[dict]) -> None:
    _STORE.write_text(json.dumps(items), encoding="utf-8")


app = FastAPI()


@app.post("/todos", status_code=201)
def create_todo(payload: TodoIn) -> Todo:
    items = _load()
    todo = Todo(id=str(uuid.uuid4()), text=payload.text, done=False)
    items.append(todo.model_dump())
    _save(items)
    return todo


@app.get("/todos")
def list_todos(status: Literal["open", "done"] | None = None) -> list[Todo]:
    items = _load()
    if status == "open":
        items = [item for item in items if not item["done"]]
    elif status == "done":
        items = [item for item in items if item["done"]]
    return [Todo(**item) for item in items]


@app.get("/todos/{todo_id}")
def get_todo(todo_id: str) -> Todo:
    for item in _load():
        if item["id"] == todo_id:
            return Todo(**item)
    raise HTTPException(status_code=404, detail="not found")


@app.put("/todos/{todo_id}")
def update_todo(todo_id: str, payload: TodoUpdate) -> Todo:
    items = _load()
    for i, item in enumerate(items):
        if item["id"] == todo_id:
            if payload.text is not None:
                item["text"] = payload.text
            if payload.done is not None:
                item["done"] = payload.done
            items[i] = item
            _save(items)
            return Todo(**item)
    raise HTTPException(status_code=404, detail="not found")


@app.delete("/todos/{todo_id}", status_code=204)
def delete_todo(todo_id: str) -> None:
    items = _load()
    for i, item in enumerate(items):
        if item["id"] == todo_id:
            items.pop(i)
            _save(items)
            return
    raise HTTPException(status_code=404, detail="not found")
```

### client.py

```python
from __future__ import annotations

import httpx


class ItemNotFound(KeyError):
    """Raised when no item exists for the given id."""


class TodoClient:
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._http = http_client or httpx.Client(base_url=base_url)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TodoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def create(self, text: str) -> dict:
        response = self._http.post("/todos", json={"text": text})
        response.raise_for_status()
        return response.json()

    def get(self, item_id: str) -> dict:
        response = self._http.get(f"/todos/{item_id}")
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
        return response.json()

    def list(self, *, status: str | None = None) -> list[dict]:
        params = {"status": status} if status is not None else None
        response = self._http.get("/todos", params=params)
        response.raise_for_status()
        return response.json()

    def mark_done(self, item_id: str, done: bool = True) -> dict:
        response = self._http.put(f"/todos/{item_id}", json={"done": done})
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
        return response.json()

    def update_text(self, item_id: str, text: str) -> dict:
        response = self._http.put(f"/todos/{item_id}", json={"text": text})
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
        return response.json()

    def delete(self, item_id: str) -> None:
        response = self._http.delete(f"/todos/{item_id}")
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
```
