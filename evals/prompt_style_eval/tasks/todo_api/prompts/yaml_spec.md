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

The product's capabilities are specified below in our project-spec format (a subset — runtime metadata fields
are omitted).

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
        description: Accept a request to add a new item.
        acceptance_criteria:
          - "Creating an item returns the item with an identifier the client can use later."
          - "A new item is not yet completed."
          - "A subsequent listing includes the new item."

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
          - "Items created in one run of the service are still present after the service is restarted."

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

Output your solution as a single Python code block.
