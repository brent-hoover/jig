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

**The product's capabilities** are specified below in our project-spec format (a subset — runtime metadata
fields are omitted).

```yaml
name: todo_api
summary: A small HTTP API for managing a todo list.
capabilities:
  - id: create-items
    title: Create todo items
    state: planned
    user_story:
      as: client application
      want: to add a new todo item
      benefit: my users can capture tasks
    behaviors:
      - id: create
        description: Add a new item to the list.
        acceptance_criteria:
          - "Creating an item makes it appear in subsequent listings."
          - "Newly created items are not yet completed."
          - "Creating multiple items preserves their order."

  - id: read-items
    title: Read items
    state: planned
    user_story:
      as: client application
      want: to retrieve existing items
      benefit: my users can see their list
    behaviors:
      - id: read-one
        description: Fetch a single item by identifier.
        acceptance_criteria:
          - "Fetching an existing item returns its representation."
          - "Fetching an item that doesn't exist signals not-found to the client."
      - id: list-all
        description: Fetch the list of items, optionally filtered by status.
        acceptance_criteria:
          - "The default listing contains every item that has been created."
          - "The listing can be filtered to only items that are open (not yet completed)."
          - "The listing can be filtered to only items that are completed."

  - id: update-items
    title: Update items
    state: planned
    user_story:
      as: client application
      want: to change an item's text or completion status
      benefit: my users can edit their list and check things off
    behaviors:
      - id: update
        description: Apply changes to an item.
        acceptance_criteria:
          - "After marking an item complete, it appears as completed in subsequent reads."
          - "After changing an item's text, the new text appears in subsequent reads."
          - "An item can be marked open again after being marked complete."
          - "Updating an item that doesn't exist signals not-found to the client."

  - id: delete-items
    title: Delete items
    state: planned
    user_story:
      as: client application
      want: to remove items I no longer need
      benefit: my users can clean up their list
    behaviors:
      - id: delete
        description: Remove an item.
        acceptance_criteria:
          - "After deletion, the item is no longer present in listings."
          - "Fetching a deleted item signals not-found to the client."
          - "Deleting an item that doesn't exist signals not-found to the client."

  - id: persistence
    title: Persist items
    state: planned
    user_story:
      as: client application
      want: items I've created to outlive the process
      benefit: my users' data isn't lost when the service restarts
    behaviors:
      - id: persist
        description: Items survive across separate runs of the service.
        acceptance_criteria:
          - "Items created in one run are still present after the service is restarted."

non_goals:
  - id: no-auth
    text: User accounts, authentication, authorization
    rationale: Single-user scope.
  - id: no-pagination
    text: Pagination
    rationale: Out of scope for this version.
  - id: no-soft-delete
    text: Soft delete / archive
    rationale: Delete is permanent in this version.
```
