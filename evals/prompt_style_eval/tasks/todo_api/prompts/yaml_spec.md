Build a small HTTP API for managing a todo list. Use Python 3, FastAPI, and pydantic v2. Single file at `app.py`,
exposing your FastAPI instance as the module-level `app` variable. Persist data in the working directory in any
file format you choose. Standard library plus FastAPI and pydantic only — no other third-party packages.

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
      want: to add a new todo item over HTTP
      benefit: my users can capture tasks
    behaviors:
      - id: create
        description: Accept a request to add a new item.
        acceptance_criteria:
          - "Posting a new item returns a successful response."
          - "The returned representation includes an identifier the client can use to refer to the item later."
          - "The returned representation includes the item's text and a not-yet-completed status."
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
          - "Fetching an item that doesn't exist returns a client-error response."
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
      want: to change an item's text or mark it complete
      benefit: my users can edit their list and check things off
    behaviors:
      - id: update
        description: Apply changes to an item by identifier.
        acceptance_criteria:
          - "Updating an item returns its updated representation."
          - "After marking an item complete, it appears as completed in subsequent reads."
          - "After changing an item's text, the new text appears in subsequent reads."
          - "Updating an item that doesn't exist returns a client-error response."

  - id: delete-items
    title: Delete items
    state: planned
    user_story:
      as: client application
      want: to remove items I no longer need
      benefit: my users can clean up their list
    behaviors:
      - id: delete
        description: Remove an item by identifier.
        acceptance_criteria:
          - "Deleting an existing item returns a successful response."
          - "After deletion, the item is no longer present in listings."
          - "Fetching a deleted item returns a client-error response."
          - "Deleting an item that doesn't exist returns a client-error response."

  - id: persistence
    title: Persist items
    state: planned
    user_story:
      as: client application
      want: items I've created to be there next time the service starts
      benefit: my users' data isn't lost when the process restarts
    behaviors:
      - id: persist
        description: Items survive across separate runs of the service.
        acceptance_criteria:
          - "Items created in one run of the service are still present when the service is restarted."

non_goals:
  - id: no-auth
    text: User accounts, authentication, authorization
    rationale: Single-user scope keeps this task focused.
  - id: no-pagination
    text: Pagination
    rationale: Out of scope for this version.
  - id: no-soft-delete
    text: Soft delete / archive
    rationale: Delete is permanent in this version.
```

Output your solution as a single Python code block.
