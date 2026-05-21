# todo_api — task notes

## What this task is for

The candidate is asked to build the *server* side of a small todo HTTP API. The contract isn't invented by the
spec — it's inherited from a pre-existing client (`client.py`) shipped as a task fixture. The candidate sees
the client code in the prompt and must make a server that satisfies it.

This is the realistic shape for HTTP APIs: contracts come from clients (existing consumers, frontends, other
services), not from product specs. The product spec describes what the system does for the user; the client
encodes the wire-level details (URLs, body shapes, status conventions, error semantics).

## What the hidden tests cover (behaviorally)

Hidden tests use the same `client.py` the candidate was shown. They don't construct raw HTTP requests; they
call `todo.create(...)`, `todo.list(status="open")`, etc. and verify behavior:

- `create` returns an item with an `id`, the text, and `done=False`.
- A created item appears in `list()`.
- Multiple created items are all preserved.
- `get(id)` returns the item; `get(missing_id)` raises `ItemNotFound`.
- `mark_done(id)` persists; `update_text(id, ...)` persists; `mark_done(id, False)` re-opens.
- Updating a missing id raises `ItemNotFound`.
- `list(status="open")` excludes completed items; `list(status="done")` excludes open ones.
- `delete(id)` removes the item from listings and reads; deleting a missing id raises `ItemNotFound`.
- Items survive across separate process imports (file-backed, not in-memory).

What the tests do NOT check: HTTP status codes, URL shapes, response body field names, JSON encoding choices.
Those are encoded once in `client.py` — the candidate gets them from there, the tests get them from there, no
duplication.

## Prompt asymmetry is intentional

Same principle as `todo_cli`:

- `yaml_spec.md` is in jig's project-spec format with explicit testable behavioral acceptance criteria.
- `prose_spec.md` is in user-story voice.

Both share the same project-context prefix: the implementation constraints (Python 3, FastAPI, single file at
`app.py`) and the client code that defines the wire contract.

## Sandbox setup

`task.yaml` lists `client.py` as a fixture. The sandbox copies it into the sandbox tmpdir alongside the
candidate's `app.py` and the hidden `tests/` directory. From inside the sandbox, both the candidate's app and
the tests can `from client import TodoClient`.

## What this task discriminates between prompt styles

- Both prompts hand the model the same wire contract via the client code. The variable is the spec part:
  does jig's structured AC list produce more reliable behavior than the prose user stories?
- Plausible signal points: filter semantics (does the candidate correctly route `?status=open|done` and
  *implement* the filter), update semantics (do PUT changes persist), error semantics (does `get` of a missing
  id return 404 so the client raises `ItemNotFound`), persistence (do items actually hit disk?).

## Implementation-choice observation (not via tests)

Things the model can still vary: the persistence file format, ID generation strategy, internal data
structures, validation strictness, code organization. All observable from the persisted `extracted_code` —
surfaced by the reporter, not the tests.
