You're building the server side of a small todo HTTP API. A Python client already exists — your server must
speak the protocol this client expects. You will write the server in a single file at `app.py` (Python 3,
FastAPI, pydantic v2, standard library plus those two). Expose your FastAPI instance as the module-level `app`
variable. Persist data in the working directory in any file format you choose.

This is the existing client; treat it as given. Do not modify it — the server must work with it as written.

```python
# client.py
import httpx


class ItemNotFound(KeyError):
    """Raised when no item exists for the given id."""


class TodoClient:
    """Thin wrapper around the todo HTTP API."""

    def __init__(self, *, base_url="http://localhost:8000", http_client=None):
        """``http_client`` lets tests inject a pre-configured client (e.g.
        FastAPI's TestClient). For real usage, pass ``base_url``."""
        self._http = http_client or httpx.Client(base_url=base_url)

    def close(self):
        self._http.close()

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

    def list(self, *, status=None) -> list[dict]:
        """List all items. status may be 'open' or 'done' to filter."""
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
```

**Why we're building this.** It's the backend for a small todo app — the client above is what consumes the API.

**As a client application, I want to add a new todo item,** so the user can capture a task. Creating an item
should give back something the client can refer to that item by later.

**As a client application, I want to retrieve items** — one at a time by identifier, or the whole list at
once. The list view should also let me filter to just the open items or just the completed ones — sometimes
the user only wants to see what's left to do.

**As a client application, I want to update an item,** either to fix its text or to mark it complete.
Whichever the client changes, the change should be reflected the next time the item is read. Items already
marked complete can also be opened again.

**As a client application, I want to remove items the user no longer cares about,** so the list can stay
focused on what matters now. After deletion the item shouldn't reappear.

**As a client application, I want items to outlive the process.** If the service restarts, the items the user
created should still be there.

Operations on items that don't exist — fetching, updating, or deleting an unknown identifier — should signal
to the client that the request was about something missing, not a problem on the server.

Output your solution as a single Python code block.
