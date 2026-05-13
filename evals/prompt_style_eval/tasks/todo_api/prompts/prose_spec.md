Build a small HTTP API for managing a todo list. Use Python 3, FastAPI, and pydantic v2. Single file at `app.py`,
exposing your FastAPI instance as the module-level `app` variable. Persist data in the working directory in any
file format you choose. Standard library plus FastAPI and pydantic only — no other third-party packages.

**Why we're building this.** It's the backend for a small todo app. Client applications will hit this API to let
their users capture tasks, see their list, check things off, and clean up.

**As a client application, I want to add a new todo item,** so the user can capture a task. The API should accept
the item's text and hand back a representation that includes some identifier the client can use to refer to that
item later.

**As a client application, I want to retrieve items.** Either one at a time, by identifier, or the whole list.
The list view should also let me filter to just the open items or just the completed ones — sometimes the user
only wants to see what's left to do.

**As a client application, I want to update an item,** either to fix its text or to mark it complete. Whichever
the client changes, the change should be reflected the next time the item is read.

**As a client application, I want to remove items the user no longer cares about,** so the list can stay
focused on what matters now. After deletion the item shouldn't reappear in listings or by identifier.

**As a client application, I want items to outlive the process.** If the service restarts, the items the user
created should still be there.

Operations on items that don't exist — fetching, updating, or deleting an unknown identifier — should signal to
the client that the request was a problem with what was asked for, not a problem on the server.

Output your solution as a single Python code block.
