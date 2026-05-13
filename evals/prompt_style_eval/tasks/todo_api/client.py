"""Client library for the todo API. This file is provided to the candidate
verbatim and must NOT be modified — the server they build must satisfy
this client.
"""

from __future__ import annotations

import httpx


class ItemNotFound(KeyError):
    """Raised when no item exists for the given id."""


class TodoClient:
    """Thin wrapper around the todo HTTP API."""

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        http_client: httpx.Client | None = None,
    ) -> None:
        """``http_client`` lets tests inject a pre-configured client (e.g.
        FastAPI's ``TestClient`` for in-process testing). For real usage,
        pass ``base_url`` and the client will manage its own httpx connection.
        """
        self._http = http_client or httpx.Client(base_url=base_url)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "TodoClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- operations --------------------------------------------------------

    def create(self, text: str) -> dict:
        """Create a new item. Returns the full item dict: id, text, done."""
        response = self._http.post("/todos", json={"text": text})
        response.raise_for_status()
        return response.json()

    def get(self, item_id: str) -> dict:
        """Fetch one item by id. Raises ItemNotFound if absent."""
        response = self._http.get(f"/todos/{item_id}")
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
        return response.json()

    def list(self, *, status: str | None = None) -> list[dict]:
        """List all items. ``status`` may be ``"open"`` or ``"done"`` to filter."""
        params = {"status": status} if status is not None else None
        response = self._http.get("/todos", params=params)
        response.raise_for_status()
        return response.json()

    def mark_done(self, item_id: str, done: bool = True) -> dict:
        """Mark an item complete (or open again). Returns the updated item."""
        response = self._http.put(f"/todos/{item_id}", json={"done": done})
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
        return response.json()

    def update_text(self, item_id: str, text: str) -> dict:
        """Change an item's text. Returns the updated item."""
        response = self._http.put(f"/todos/{item_id}", json={"text": text})
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
        return response.json()

    def delete(self, item_id: str) -> None:
        """Remove an item. Raises ItemNotFound if absent."""
        response = self._http.delete(f"/todos/{item_id}")
        if response.status_code == 404:
            raise ItemNotFound(item_id)
        response.raise_for_status()
