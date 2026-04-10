# jig-store: Design Specification

**Date:** 2026-04-10
**Status:** Approved, ready for implementation planning
**Source:** Brainstormed from `store/REQUIREMENTS.TXT` with decisions recorded below.

## Overview

`jig-store` is an async-native storage and messaging library built inside the existing `jig` Python package. It provides document storage, multi-key querying, a pub/sub message bus, and agent memory — all backed by append-only JSONL files with in-memory working sets.

It replaces the ad-hoc JSONL helpers currently in `jig/persistence.py` and the simple `MessageBus` in `jig/bus.py`. YAML-backed config files (`config.yaml`, `issues/`, `tasks/`, `agent_types/`, `workflows/`, `project_context.yaml`) are **not** migrated — they stay as they are because they are human-authored and slowly changing.

## Design Principles

- **Async-native:** all public APIs are `async`. No sync wrappers.
- **Append-only JSONL:** writes are always appends. In-memory state is the working set; JSONL is the source of truth for crash recovery.
- **In-memory working set:** documents are loaded into memory at startup and kept there. Appropriate for Jig's scale (tens to low hundreds of documents per collection).
- **Pydantic-native at the edges:** `TypedCollection[T]` gives callers Pydantic models end-to-end. `core.py` and `collection.py` remain importable without pydantic.
- **One model = one collection:** each `TypedCollection` holds a single model type. Heterogeneous data splits into separate files.
- **Single-process only:** no file locking, no multi-process safety. Jig core is single-process asyncio.

## Module Structure

```
jig/store/
  __init__.py     # re-exports public API
  core.py         # JsonlStore — append-only JSONL + in-memory dict + indexes
  collection.py   # Collection, Database
  models.py       # StoreModel, TypedCollection[T]
  bus.py          # Message, MessageType, MessageBus
  memory.py       # Handoff, Learning, MemoryStore
```

**Dependency direction** (strict, downward only):
- `core.py` — no internal deps, no pydantic
- `collection.py` → `core.py`
- `models.py` → `collection.py`, pydantic
- `bus.py` → `models.py`, `collection.py`, pydantic
- `memory.py` → `models.py`, `collection.py`, pydantic

## `__init__.py` Exports

```python
from .core import JsonlStore
from .collection import Collection, Database
from .models import StoreModel, TypedCollection
from .bus import Message, MessageType, MessageBus
from .memory import Handoff, Learning, MemoryStore
```

## On-disk layout

```
.jig/
  store/
    messages.jsonl         # MessageBus
    handoffs.jsonl         # MemoryStore (handoffs)
    learnings.jsonl        # MemoryStore (learnings)
    phase_history.jsonl    # orchestrator phase history (via Database)
    <name>.jsonl           # any other Collection created via Database
```

## `core.py` — JsonlStore

The base layer. One JSONL file, one in-memory dict, optional secondary indexes.

### Log record format

Every write appends a record with an `_op` field:

```json
{"_op": "insert", "_id": "uuid", "field": "value", ...}
{"_op": "update", "_id": "uuid", "field": "new_value"}
{"_op": "delete", "_id": "uuid"}
```

### Class: `JsonlStore`

```python
class JsonlStore:
    def __init__(self, path: Path, index_fields: list[str] | None = None): ...
```

**State:**
- `self._docs: dict[str, dict]` — `_id` → doc
- `self._indexes: dict[str, dict[Any, set[str]]]` — one per index field
- `self._lock: asyncio.Lock` — serializes mutations and file appends
- `self._path: Path`
- `self._loaded: bool`

**Methods:**

```python
async def load(self) -> None
```
Creates parent directory and file if missing. Streams the JSONL line-by-line, replaying ops in order: `insert` installs a new doc, `update` merges changes into the existing doc, `delete` removes it. Indexes are rebuilt from the final reconstructed docs. Sets `_loaded = True`.

Must be called before any other method.

```python
async def insert(self, doc: dict) -> str
```
If `_id` not present, assigns `uuid4()`. Appends `{"_op": "insert", **doc}` to disk via `asyncio.to_thread`. Updates in-memory dict and indexes. Returns the `_id`.

```python
async def get(self, doc_id: str) -> dict | None
```
In-memory dict lookup. Lock-free.

```python
async def find(self, predicate: Callable[[dict], bool] | None = None) -> list[dict]
```
Linear scan over all docs, optionally filtered. Returns a new list.

```python
async def find_by(self, field: str, value: Any) -> list[dict]
```
If `field` is in `index_fields`, uses the index. Otherwise:
- If `len(self._docs) > 1000`, raises `ValueError` with guidance to declare the field in `index_fields`.
- Otherwise, falls back to a linear scan.

```python
async def update(self, doc_id: str, changes: dict) -> bool
```
Merges changes into existing doc, appends `{"_op": "update", "_id": ..., **changes}`, reindexes (remove from old buckets, insert into new). Returns `False` if doc not found (no file write).

```python
async def delete(self, doc_id: str) -> bool
```
Removes from dict and all index buckets, appends `{"_op": "delete", "_id": ...}`. Returns `False` if not found.

```python
async def count(self, predicate: Callable[[dict], bool] | None = None) -> int
```

### Concurrency

- One `asyncio.Lock` per store. All mutations (`insert`, `update`, `delete`) hold it for the full duration of memory-mutation + file-append.
- File writes use `asyncio.to_thread(_append_line)`; `_append_line` opens in append mode, writes one line, closes. Slower than a long-lived handle but crash-safer.
- Reads (`get`, `find`, `find_by`, `count`) are lock-free. They're CPython dict reads on a dict that's only mutated under the lock, so they're consistent with the last completed write.

### Error handling

- `load()` raises immediately on: malformed JSON, missing `_op`, missing `_id`. Errors include the line number. Corruption should be loud, not silent.
- `find_by()` on an unindexed field in a >1000 doc collection raises `ValueError`.

## `collection.py` — Collection, Database

### `Collection`

Thin wrapper over `JsonlStore`. Constructs its own `JsonlStore(path, index_fields)`. `load()` delegates. `insert`, `get`, `find`, `find_by`, `update`, `delete`, `count` all pass through.

**Added methods:**

```python
async def find_where(self, **kwargs) -> list[dict]
```
Equality filter on multiple fields. Picks the first kwarg key that's in the store's `index_fields`, uses `find_by` to get a candidate set, then filters candidates against the remaining kwargs in memory. If no kwarg field is indexed, falls back to `find` with a predicate (subject to the >1000 doc safeguard).

```python
async def find_one_where(self, **kwargs) -> dict | None
```
Returns the first match or `None`.

```python
async def upsert(self, match: dict, doc: dict) -> str
```
If a document matching all `match` fields exists, update it with `doc`; otherwise insert `doc`. Held under the store's lock so it's atomic against concurrent writers. Returns the `_id`.

### `Database`

Named-collection factory.

```python
class Database:
    def __init__(self, base_path: Path): ...

    async def collection(
        self,
        name: str,
        index_fields: list[str] | None = None,
        model: type[T] | None = None,
    ) -> Collection | TypedCollection[T]: ...
```

**State:** `self._base_path: Path`, `self._collections: dict[str, Collection | TypedCollection]`.

**Behavior:**
- First call for a name constructs a `Collection` (or `TypedCollection` if `model` given) at `base_path / f"{name}.jsonl"`, calls `load()`, caches, returns.
- Second call with the same name returns the cached instance.
- Second call with mismatched `index_fields` or `model` → raises `ValueError`. (Programmer error; better loud than silent.)

## `models.py` — StoreModel, TypedCollection

### `StoreModel`

```python
class StoreModel(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    model_config = ConfigDict(populate_by_name=True)
```

All models intended for storage inherit from `StoreModel`. Serialization is always `model.model_dump(by_alias=True)` so `_id` round-trips through the dict layer. Deserialization is always `Model.model_validate(doc)`.

### `TypedCollection[T]`

```python
class TypedCollection(Generic[T]):
    def __init__(
        self,
        path: Path,
        model: type[T],
        index_fields: list[str] | None = None,
    ): ...

    async def load(self) -> None: ...
    async def insert(self, doc: T) -> str: ...
    async def get(self, doc_id: str) -> T | None: ...
    async def find(self, predicate: Callable[[T], bool] | None = None) -> list[T]: ...
    async def find_where(self, **kwargs) -> list[T]: ...
    async def find_one_where(self, **kwargs) -> T | None: ...
    async def update(self, doc_id: str, changes: dict) -> bool: ...
    async def delete(self, doc_id: str) -> bool: ...
    async def upsert(self, match: dict, doc: T) -> str: ...
```

Internally holds a `Collection` plus the model class. Each method wraps the dict-returning equivalent with serialize/deserialize:

- `insert(doc)` → `col.insert(doc.model_dump(mode="json", by_alias=True))`
- `get`, `find`, `find_where`, `find_one_where` → dicts materialized to models
- `update(doc_id, changes)` — `changes` is a plain dict of field updates, NOT a model instance. Delegated as-is.
- `upsert(match, doc)` → `col.upsert(match, doc.model_dump(mode="json", by_alias=True))`
- `find(predicate)` — materializes every doc to a model before applying the predicate (the spec types the predicate as `Callable[[T], bool]`). Fine at Jig's scale.

**Why `mode="json"`:** pydantic's default `model_dump` keeps non-JSON-native field values in their Python form (`datetime`, `UUID`, sets, etc.), which `json.dumps` can't serialize. `mode="json"` converts these to JSON-compatible primitives (ISO strings for datetimes, etc.). On read, `Model.model_validate(doc)` coerces them back. This makes `TypedCollection` robust to any model with datetime/UUID/set fields (e.g., `PhaseHistoryEntry.timestamp: datetime`).

### Alias handling

`Message` uses `Field(alias="from")` because `from` is a Python keyword. `populate_by_name=True` plus `by_alias=True` on dump means JSONL sees `{"from": ...}` and Python code uses `message.sender`. Verified by a round-trip test in `test_store_models.py`.

## `bus.py` — Message, MessageType, MessageBus

### Enums and models

```python
class MessageType(str, Enum):
    TASK_ASSIGNMENT = "task_assignment"
    TASK_COMPLETION = "task_completion"
    QUESTION = "question"
    ANSWER = "answer"
    CONTEXT_UPDATE = "context_update"
    STATUS = "status"


class Message(StoreModel):
    sender: str = Field(alias="from")
    to: str
    type: MessageType
    payload: dict
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    correlation_id: str | None = None
    topic: str

    model_config = ConfigDict(populate_by_name=True)
```

`MessageType` and `Message` live in `jig/store/bus.py` and are the source of truth. The existing `MessageType` and `Message` in `jig/models.py` are deleted during migration (Section: Migration).

Using `datetime.now(timezone.utc).isoformat()`, not `datetime.utcnow()` (the latter is deprecated in Python 3.12+).

### `MessageBus`

```python
class MessageBus:
    def __init__(self, path: Path): ...
    async def load(self) -> None: ...
    async def publish(self, message: Message | dict) -> str: ...
    async def subscribe(self, topic: str) -> asyncio.Queue[Message]: ...
    async def unsubscribe(self, topic: str, queue: asyncio.Queue) -> None: ...
    async def get_history(self, topic: str, limit: int = 100) -> list[Message]: ...
    async def add_websocket_listener(
        self, callback: Callable[[Message], Awaitable[None]]
    ) -> None: ...
```

**State:**
- `self._collection: TypedCollection[Message]` — backed by the given path, indexed on `["topic"]`
- `self._subscribers: dict[str, list[asyncio.Queue[Message]]]` — one list per topic, fan-out
- `self._listeners: list[Callable[[Message], Awaitable[None]]]` — websocket callbacks
- `self._lock: asyncio.Lock` — serializes subscribe/unsubscribe/publish bookkeeping

**`__init__(path)`** — constructs the typed collection. Caller supplies the exact file path (e.g., `.jig/store/messages.jsonl`).

**`load()`** — delegates to the collection. Queues are NOT pre-populated: the log is replayed into the collection but NOT re-delivered to subscribers. Queues only see messages published after startup. Historical retrieval is via `get_history()`.

**`publish(message)`**
1. If `message` is a dict, construct `Message.model_validate(message)`. If already a `Message`, use it.
2. Insert into the collection (assigns `_id`, writes to JSONL).
3. Under `self._lock`, snapshot `self._subscribers.get(message.topic, [])` and `self._listeners` into local lists, then release the lock. This prevents subscribe/unsubscribe from mutating the lists during fan-out without holding the lock across awaits (which would let a slow or hung listener block the whole bus).
4. Fan out to every snapshot queue via `await queue.put(message)`. Queues are unbounded so `put` never blocks on capacity.
5. Invoke every snapshot listener via `await callback(message)`. Exceptions are caught, logged at WARNING via stdlib `logging`, and suppressed — they must not affect queue delivery or other listeners.
6. Return the `_id`.

**`subscribe(topic)`** — creates a new unbounded `asyncio.Queue`, appends to `self._subscribers[topic]`, returns it. Every call creates a new queue. Two calls with the same topic give two independent queues that both receive every subsequent publish.

**`unsubscribe(topic, queue)`** — removes the queue from the list. Silent no-op if not found (makes shutdown cleanup idempotent).

**`get_history(topic, limit=100)`** — `self._collection.find_where(topic=topic)`, sorted by `timestamp` ascending, sliced to the last `limit` entries, returned as `list[Message]`.

**`add_websocket_listener(callback)`** — appends to `self._listeners`.

### Semantics: topic fan-out + caller-side filter

This is a behavior change from the old bus. The old `subscribe(issue_id, subscriber_name)` routed by subscriber name; the new `subscribe(topic)` fans out to every subscriber of that topic. Consumers filter by `message.to` themselves:

```python
queue = await bus.subscribe(issue_id)
while True:
    msg = await queue.get()
    if msg.to not in (my_name, "broadcast"):
        continue
    handle(msg)
```

A small helper may be introduced if the pattern appears in three or more places.

## `memory.py` — Handoff, Learning, MemoryStore

One model per collection, two files on disk.

### Models

```python
class Handoff(StoreModel):
    issue_id: str
    from_phase: str
    to_phase: str
    summary: str
    artifacts: list[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class Learning(StoreModel):
    issue_id: str
    phase: str
    content: str
    tags: list[str] = Field(default_factory=list)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
```

No `type` discriminator field (redundant — the file identifies the type).

### `MemoryStore`

```python
class MemoryStore:
    def __init__(self, path: Path): ...  # directory path, not a file path
    async def load(self) -> None: ...

    async def write_handoff(
        self,
        issue_id: str,
        from_phase: str,
        to_phase: str,
        summary: str,
        artifacts: list[str] | None = None,
    ) -> str: ...

    async def read_handoff(self, issue_id: str, to_phase: str) -> Handoff | None: ...

    async def add_learning(
        self,
        issue_id: str,
        phase: str,
        content: str,
        tags: list[str] | None = None,
    ) -> str: ...

    async def get_learnings(
        self,
        issue_id: str,
        tags: list[str] | None = None,
        limit: int = 10,
    ) -> list[Learning]: ...

    async def get_context_block(self, issue_id: str, to_phase: str) -> str: ...
```

**Internal state:**

```python
self._handoffs: TypedCollection[Handoff] = TypedCollection(
    path / "handoffs.jsonl",
    model=Handoff,
    index_fields=["issue_id", "to_phase"],
)
self._learnings: TypedCollection[Learning] = TypedCollection(
    path / "learnings.jsonl",
    model=Learning,
    index_fields=["issue_id"],
)
```

**Note:** `__init__` takes a **directory** path, not a file path. This differs from the original REQUIREMENTS.TXT, which had it ambiguous. The directory is typically `.jig/store`.

**Method behavior:**
- `write_handoff(...)` → construct `Handoff(...)`, `await self._handoffs.insert(handoff)`
- `read_handoff(issue_id, to_phase)` → `find_where(issue_id=..., to_phase=...)`, sort by `timestamp` descending, return `results[0] if results else None`
- `add_learning(...)` → construct `Learning(...)`, `await self._learnings.insert(learning)`
- `get_learnings(issue_id, tags, limit)`:
  1. `results = await self._learnings.find_where(issue_id=issue_id)`
  2. If `tags` given, filter to entries where `any(t in learning.tags for t in tags)`
  3. Sort by `timestamp` descending
  4. Slice `[:limit]`
  5. Return

- `get_context_block(issue_id, to_phase)`:
  1. `handoff = await self.read_handoff(issue_id, to_phase)`
  2. `learnings = await self.get_learnings(issue_id, limit=10)` (no tag filter)
  3. Compose a markdown block:
     ```
     ## Handoff from [from_phase]
     [summary]

     ## Learnings
     - [content] (tags: [tags])
     - ...
     ```
  4. Omit the handoff section if none exists. Omit the learnings section if the list is empty. If both are empty, return an empty string.

## Migration Plan

A single switchover commit after all `jig/store/` modules are green.

### Deletions

**`jig/bus.py`** — delete entirely.

**`jig/persistence.py`** — delete only these functions (everything else stays):
- `append_message`, `load_messages`
- `append_phase_history`, `load_phase_history`
- `_phase_history_path`

**`jig/models.py`** — delete only:
- `class Message` (the old one)
- `class MessageType`

### Rewrites

**`jig/agent.py`**
- Imports: `from jig.bus import MessageBus` → `from jig.store import MessageBus`
- Instantiation: `MessageBus(project_path)` → `MessageBus(project_path / ".jig" / "store" / "messages.jsonl")` + `await bus.load()`
- Any subscribe-and-read loops move to the topic-fan-out + filter-by-`to` pattern.

**`jig/mcp_tools.py`**
- Imports updated to pull `Message` and `MessageType` from `jig.store`.
- `handle_publish_message` constructs the new `Message` with `sender=sender` (serialized as `from`), `to=args["recipient"]`, `type=MessageType(...)`, `topic=issue_id`, `payload=payload`. The MCP tool's public arg name stays as `recipient` — only the internal field name changes.

**`jig/orchestrator.py`**
- Replace `append_phase_history(...)` → `await phase_history.insert(entry)` where `phase_history` is a `TypedCollection[PhaseHistoryEntry]` obtained via `Database.collection("phase_history", index_fields=["issue_id"], model=PhaseHistoryEntry)`.
- Replace `load_phase_history(...)` → `await phase_history.find_where(issue_id=issue_id)` + sort.
- MessageBus instantiation at startup: construct once, `await bus.load()`, pass to agents.

**`jig/models.py` (PhaseHistoryEntry changes)** — two edits required for phase history to fit the new store:
1. Change base class from `BaseModel` to `StoreModel` (adds an auto-assigned `_id`). Verified no field conflict — `PhaseHistoryEntry` has no existing `id` field.
2. **Add `issue_id: str` field.** The old layout scoped phase history per-issue via the file path (`.jig/issues/<id>/phase_history.jsonl`). The new layout stores all phase history in a single `.jig/store/phase_history.jsonl` keyed by `issue_id`, so the field has to move into the record itself.
3. The `timestamp: datetime` field stays as `datetime` — `TypedCollection.insert` uses `model_dump(mode="json", ...)` which serializes `datetime` to an ISO string automatically.

**`jig/ws_server.py`** — no migration edits. (The optional `bus.add_websocket_listener` wiring is net-new behavior, not a migration, and is captured in the implementation plan rather than this spec.)

### Tests

- `tests/test_bus.py` — rewrite against the new API.
- `tests/test_persistence.py` — delete tests for the removed functions. Keep everything else.
- `tests/test_agent.py`, `tests/test_mcp_tools.py`, `tests/test_orchestrator.py` — update MessageBus setup and any direct message-construction helpers.

### Safety net

After the switchover commit, run `uv run pytest` to confirm all module tests pass, then run the jig CLI against a scratch project (`jig init` + `jig start`) to confirm the orchestrator-agent-bus path still works end-to-end. If anything fails, revert the single switchover commit — nothing else is built on top of it yet.

## Testing Strategy

All tests live under `tests/`. Each store module gets its own test file. Uses `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"` already set). Every test uses `tmp_path`; nothing writes to the real `.jig/`. All test runs use `uv run pytest`.

TDD is applied to each new module (red → green → refactor). The migration step is not TDD — it's mechanical caller rewrites.

### `tests/test_store_core.py`
- `insert` assigns `_id`, persists, returns id
- `insert` with pre-assigned `_id` keeps it
- `get` hit / miss
- `find` with and without predicate
- `find_by` with indexed field
- `find_by` with non-indexed field under 1000 docs (falls through)
- `find_by` with non-indexed field over 1000 docs (raises `ValueError`)
- `update` merges fields, returns `True`
- `update` on missing id returns `False` without writing
- `update` on an indexed field reindexes correctly
- `delete` removes from memory and indexes, returns `True`
- `delete` on missing id returns `False`
- `count` with and without predicate
- Crash recovery: mixed insert/update/delete ops, new instance, `load()`, state matches
- Corrupted log: malformed JSON line → `load()` raises clearly

### `tests/test_store_collection.py`
- `find_where` single field (uses index)
- `find_where` multiple fields (first indexed, rest filtered)
- `find_where` with no indexed field (falls through)
- `find_one_where` hit / miss
- `upsert` hit path (updates)
- `upsert` miss path (inserts)
- `upsert` returns correct `_id`
- `Database.collection(name)` caches
- `Database.collection(name, ...)` with mismatched `index_fields` raises `ValueError`
- `Database.collection(name, model=...)` returns a `TypedCollection`
- Crash recovery via `Database`

### `tests/test_store_models.py`
- `TypedCollection.insert(model)` round-trips through JSONL and comes back as a model
- `get`, `find`, `find_where`, `find_one_where` return model instances
- `update(doc_id, dict_changes)` works (confirming `changes` is a dict)
- `delete` works
- `upsert` with a model works
- Alias handling: a model with `Field(alias="from")` round-trips (pins the `Message.sender` / `"from"` wire format)
- Crash recovery with a `TypedCollection`

### `tests/test_store_bus.py`
- `publish` accepts a `Message` instance
- `publish` accepts a plain dict and validates
- `publish` returns new `_id`
- Single subscriber receives one copy
- Two subscribers to the same topic both receive every message (fan-out)
- Subscriber to topic A does NOT receive topic B messages
- `unsubscribe` stops delivery; other queues still get messages
- `get_history` returns prior messages oldest-first
- `get_history(limit=N)` caps at N
- `add_websocket_listener` fires on publish
- Listener that raises is caught; queue delivery still happens
- `load()` does NOT replay to queues (subscribe after load, no messages arrive until new publishes)
- Crash recovery: publish some, re-open, `get_history` returns them

### `tests/test_store_memory.py`
- `write_handoff` + `read_handoff` round trip
- `read_handoff` returns the most recent when multiple exist
- `read_handoff` miss returns `None`
- `add_learning` + `get_learnings` round trip
- `get_learnings` filters by any-tag overlap when `tags` given
- `get_learnings` sorts most-recent-first and respects `limit`
- `get_context_block` with both handoff and learnings
- `get_context_block` with only learnings (omits handoff section)
- `get_context_block` with only handoff (omits learnings section)
- `get_context_block` with neither returns empty string
- Crash recovery for both collections

## Error Handling Summary

| Area | Condition | Behavior |
|---|---|---|
| `JsonlStore.load()` | Malformed JSON / missing `_op` / missing `_id` | Raise with line number. No silent skip. |
| `JsonlStore.find_by()` | Unindexed field on >1000 docs | `ValueError` with guidance |
| `Collection.upsert()` | — | No special errors; delegates |
| `Database.collection()` | Cached name + mismatched `index_fields` or `model` | `ValueError` |
| `MessageBus.publish(dict)` | Invalid dict | `pydantic.ValidationError` bubbles up |
| `MessageBus` websocket listener | Listener raises | Caught, logged WARNING, suppressed. Queue delivery unaffected. |
| `MessageBus.unsubscribe()` | Queue not registered | Silent no-op (idempotent shutdown) |
| `MemoryStore` | Invalid model construction | `pydantic.ValidationError` |

## Concurrency Summary

- `JsonlStore` holds a single `asyncio.Lock`. Mutations hold it across memory-mutation + file-append.
- File I/O goes through `asyncio.to_thread`. Each append opens, writes one line, closes.
- Reads are lock-free (CPython dict reads on a dict only mutated under lock).
- `MessageBus` holds its own lock around subscriber-list bookkeeping and publish fan-out. The underlying collection's lock serializes the insert.
- Single-process only. No file locking, no multi-process safety.

## Dependencies

- `pydantic>=2.0` (already in `pyproject.toml`)
- Standard library only for `core.py` and `collection.py`

## Out of Scope

- Embedding-based retrieval for learnings (deferred)
- JSONL compaction (deferred — files stay small at Jig's scale)
- Multi-process safety
- Any external dependencies beyond pydantic
- Migrating YAML-backed config (`config.yaml`, `issues/`, `tasks/`, `agent_types/`, `workflows/`, `project_context.yaml`) — those stay as they are

## Decisions Log

Explicit decisions made during brainstorming that deviate from or clarify `REQUIREMENTS.TXT`:

1. **Full migration** of existing callers (`jig/bus.py`, message + phase-history parts of `jig/persistence.py`) in the same project. Not a separate follow-up.
2. **YAML config files stay as YAML.** Only messages, phase history, and memory move to the store.
3. **No migration of existing on-disk data.** Pre-release; users re-init with `jig init`.
4. **Topic fan-out semantics** as specified: every subscriber to a topic gets every message; consumers filter by `message.to`.
5. **`MessageType` is an enum**, not a free-form string. Moved from `jig/models.py` to `jig/store/bus.py` as the source of truth.
6. **One model per collection.** `MemoryStore` splits into `handoffs.jsonl` + `learnings.jsonl` instead of a single `memory.jsonl` with a type discriminator. Drops the `type: Literal[...]` field from both models. `MemoryStore.__init__` takes a directory path, not a file path.
7. **`Database.collection()` with mismatched args** on a cached name raises `ValueError`. Spec was silent.
8. **`datetime.now(timezone.utc).isoformat()`**, not the deprecated `datetime.utcnow()`.
9. **`load()` raises on malformed log lines** with line numbers. Spec was silent.
10. **Approach 1 build order:** bottom-up TDD (`core` → `collection` → `models` → `bus` → `memory`), then a single switchover commit to migrate callers.
11. **`TypedCollection` uses `model_dump(mode="json", by_alias=True)`.** Required for any model with `datetime` / `UUID` / `set` fields (notably `PhaseHistoryEntry.timestamp`). Pydantic coerces back on `model_validate`.
12. **`PhaseHistoryEntry` gains `issue_id: str`** and inherits from `StoreModel`. The old layout scoped by file path; the new layout keys records by the field.
