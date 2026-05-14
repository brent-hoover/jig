You're building a small todo system that has two parts: an HTTP server and a Python client that talks to it.
Use Python 3, FastAPI, pydantic v2, and httpx. Standard library plus those three — no other third-party packages.
You will emit two files.

**Output format.** Emit each file as a separately-headed Python code block, in this order:

```
### app.py
```python
…server code…
```

### client.py
```python
…client code…
```
```

**File contents.**

- `app.py` must expose your FastAPI instance as the module-level `app` variable. The server persists data in
  the working directory in any file format you choose.
- `client.py` must expose a class `TodoClient` with this Python API. Tests call these methods directly; the
  signatures, return shapes, and raised exceptions are the contract:

```python
class ItemNotFound(KeyError):
    """Raised when no item exists for the given id."""


class TodoClient:
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:8000",
        http_client: httpx.Client | None = None,
    ) -> None:
        """``http_client`` lets tests inject a pre-configured client (e.g.
        FastAPI's TestClient). For real usage, pass ``base_url``."""
        ...

    def close(self) -> None: ...

    def create(self, text: str) -> dict:
        """Create a new item. Returns the item dict: id, text, done."""

    def get(self, item_id: str) -> dict:
        """Fetch one item by id. Raises ItemNotFound if absent."""

    def list(self, *, status: str | None = None) -> list[dict]:
        """List items. ``status`` may be 'open' or 'done' to filter."""

    def mark_done(self, item_id: str, done: bool = True) -> dict:
        """Mark an item complete (or open again). Returns the updated item."""

    def update_text(self, item_id: str, text: str) -> dict:
        """Change an item's text. Returns the updated item."""

    def delete(self, item_id: str) -> None:
        """Remove an item. Raises ItemNotFound if absent."""
```

The wire protocol between client and server (URLs, request/response shapes, status codes) is your design —
just make the client work with the server you build.

**Why we're building this.** It's the backend (and matching Python client) for a small todo app.

**As a client application, I want to add a new todo item,** so the user can capture a task. New items shouldn't
already be marked complete, and creating several items in a row should preserve the order I added them.

**As a client application, I want to retrieve items** — one at a time, or the whole list at once. The list view
should also let me filter to just the open items or just the completed ones, since sometimes the user only
wants to see what's left to do.

**As a client application, I want to update an item,** either to fix its text or to mark it complete. Whichever
the client changes, the change should be reflected the next time the item is read. Items already marked
complete can also be opened again.

**As a client application, I want to remove items the user no longer cares about,** so the list can stay
focused on what matters now. After deletion the item shouldn't reappear in listings or by identifier.

**As a client application, I want items to outlive the process.** If the service restarts, the items the user
created should still be there.

Operations on items that don't exist — fetching, updating, or deleting an unknown identifier — should signal
to the client that the request was about something missing, not a problem on the server.
