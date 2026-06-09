import asyncio
import contextlib
import fcntl
import inspect
import logging
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Union

from jig.store.models import TypedCollection
from jig.ticket import Ticket, TicketStatus, WorkType

logger = logging.getLogger(__name__)

StatusChangeCallback = Callable[
    [str, Union[str, None], str], Union[Awaitable[None], None]
]

CreateCallback = Callable[[Ticket], Union[Awaitable[None], None]]

# All shipped work_types currently participate in the workflow pipeline.
# Phase 2 will introduce per-work_type workflow selection via config.yaml,
# but for now every top-level ticket is eligible.
TOP_LEVEL_WORK_TYPES = set(WorkType)

# Backwards-compat alias used by older imports — remove with TicketType.
TOP_LEVEL_TYPES = TOP_LEVEL_WORK_TYPES


class TicketStore:
    def __init__(self, path: Path) -> None:
        self._collection: TypedCollection[Ticket] = TypedCollection(
            path,
            model=Ticket,
            index_fields=["work_type", "status", "assignee", "parent_id"],
        )
        # jig-N key counter and its cross-process lock live alongside the
        # JSONL so every process sharing the store dir serializes on them.
        self._seq_path = path.parent / "issue_seq"
        self._lock_path = path.parent / ".issue.lock"
        self._on_status_change: StatusChangeCallback | None = None
        self._on_create: CreateCallback | None = None
        self._background_tasks: set[asyncio.Task] = set()

    def set_status_change_callback(self, cb: StatusChangeCallback | None) -> None:
        """Register a callback fired on every observed status transition.

        Used by the orchestrator to feed ``TicketStateChanged`` analytics
        events without coupling the store to the analytics schema. The
        callback receives ``(ticket_id, from_state, to_state)``; sync or
        async returns are both supported. ``from_state`` is None for the
        first observed status (initial creation paths).
        """
        self._on_status_change = cb

    def set_create_callback(self, cb: CreateCallback | None) -> None:
        """Register a callback fired after every successful ticket create.

        The orchestrator (and ``jig.ticket_events.wire_create_publisher``
        for non-orchestrator paths) use this to publish ``ticket_created``
        events on the bus with the full ticket payload, so every direct
        ``tickets.create(Ticket(...))`` call (init flow, CLI commands,
        Coordinator materialize, spike proposals) announces itself
        without the caller having to remember. Previously only the
        MCP ``handle_create_ticket`` published, leaving the TUI with
        half-empty ticket dicts for system tickets.

        Sync or async returns are both supported. Pass ``None`` to
        clear the callback (useful in tests). To skip the callback
        for a single create call (e.g. when the caller publishes the
        event itself), pass ``fire_create_callback=False`` to
        ``create()`` rather than mutating shared store state across
        an await.
        """
        self._on_create = cb

    async def load(self) -> None:
        await self._collection.load()

    async def drain_background_tasks(self) -> None:
        """Await every in-flight callback task scheduled by ``create()``
        or ``update_status()``.

        One-shot callers (the ``jig plan`` / ``jig canonicalize`` CLI
        commands, ``run_init``) must call this before returning from
        their ``asyncio.run()`` block. Otherwise the loop exits while
        async callbacks are still queued — ``asyncio.run`` cancels
        pending tasks at teardown, dropping the broadcast events the
        TUI relies on.

        No-op when no callback is registered or no tasks are
        outstanding. Safe to call repeatedly.
        """
        if not self._background_tasks:
            return
        # Snapshot the set: completed tasks remove themselves via
        # ``_on_done``, so iterating the live set during gather would
        # mutate-while-iterating.
        await asyncio.gather(*list(self._background_tasks), return_exceptions=True)

    @contextlib.asynccontextmanager
    async def _key_lock(self) -> AsyncIterator[None]:
        """Hold an exclusive cross-process ``flock`` for the duration of the
        key-assignment + append critical section.

        The in-process ``asyncio.Lock`` inside ``Collection.insert`` keeps a
        single process's in-memory state consistent; this ``flock`` extends
        the guarantee across separate OS processes (CLI / standalone MCP /
        orchestrator) so the ``jig-N`` counter and the JSONL append never
        interleave.

        Acquisition uses a non-blocking ``flock`` with async backoff rather
        than a blocking call. Two failure modes are avoided:

        - A *synchronous* blocking ``flock`` would block the event-loop thread
          while the lock is held across the awaited critical section. Two
          coroutines creating on the same store in one loop would then
          deadlock: the second's blocking acquire wedges the loop, so the
          first can never resume to release.
        - ``asyncio.to_thread(flock, ...)`` frees the loop but opens a
          cancellation race: a CancelledError at the ``await`` closes ``fd`` in
          ``finally`` while the worker thread is still blocked in ``flock``,
          which then acquires on a closed/recycled fd and never releases.

        ``LOCK_NB`` + ``asyncio.sleep`` sidesteps both: each attempt returns
        immediately, the loop stays free between attempts, and no thread holds
        a reference to ``fd`` — so cancellation can only ever happen with the
        lock cleanly held or not held. Contention is near-zero and brief, so
        the poll interval is never meaningfully exercised.
        """
        fd = os.open(self._lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.01)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    def _read_seq(self) -> int:
        try:
            return int(self._seq_path.read_text().strip() or "0")
        except FileNotFoundError:
            return 0

    def _write_seq(self, value: int) -> None:
        self._seq_path.write_text(str(value))

    async def all(self) -> list[Ticket]:
        """Return every ticket. Used by the issue front doors for listing."""
        return await self._collection.find()

    async def resolve_ref(self, ref: str) -> Ticket | None:
        """Resolve a ticket by internal ``id`` (UUID) or ``jig-N`` key."""
        direct = await self.get(ref)
        if direct is not None:
            return direct
        matches = await self._collection.find(lambda t: t.key == ref)
        return matches[0] if matches else None

    async def create(self, ticket: Ticket, *, fire_create_callback: bool = True) -> str:
        # Uniqueness is enforced inside Collection.insert under its
        # asyncio.Lock — no TOCTOU window between check and append.
        # We re-raise with a ticket-specific message so callers (CLI,
        # init flow) get a domain-friendly error.
        #
        # Key assignment + append run under a cross-process flock: read the
        # counter from disk (other processes may have advanced it), assign the
        # jig-N key, append, then persist the counter — all before releasing
        # the lock so a concurrent process can never observe a half-updated
        # counter or reissue a key.
        try:
            async with self._key_lock():
                if not ticket.key:
                    next_seq = self._read_seq() + 1
                    ticket.key = f"jig-{next_seq}"
                    # Persist the counter BEFORE the append. If the process
                    # dies between here and the insert, the consumed number
                    # becomes a harmless gap; the alternative ordering would
                    # let the next creator reissue the same jig-N key.
                    self._write_seq(next_seq)
                ticket_id = await self._collection.insert(ticket)
        except ValueError as e:
            if "already exists" in str(e):
                raise ValueError(f"ticket with id {ticket.id!r} already exists") from e
            raise
        # Fire the create callback so the orchestrator can announce
        # the new ticket on the bus. Done after the JSONL write
        # commits so subscribers can immediately read the ticket back
        # if they want to (no read-after-write race).
        # ``fire_create_callback=False`` is the per-call escape hatch
        # for callers (``ticket_mcp.handle_create_ticket``) that
        # publish events themselves and want to avoid the
        # store-callback double-publish.
        if fire_create_callback and self._on_create is not None:
            self._fire_create(ticket)
        return ticket_id

    def _fire_create(self, ticket: Ticket) -> None:
        cb = self._on_create
        if cb is None:
            return
        try:
            result = cb(ticket)
        except Exception as exc:
            logger.warning("ticket-create callback raised: %s", exc, exc_info=exc)
            return
        if inspect.isawaitable(result):
            task = asyncio.create_task(result)  # type: ignore[arg-type]
            self._background_tasks.add(task)

            def _on_done(t: asyncio.Task) -> None:
                self._background_tasks.discard(t)
                if not t.cancelled() and (exc := t.exception()):
                    logger.warning(
                        "ticket-create callback raised: %s", exc, exc_info=exc
                    )

            task.add_done_callback(_on_done)

    async def get(self, ticket_id: str) -> Ticket | None:
        return await self._collection.get(ticket_id)

    async def approve(self, ticket_id: str) -> Ticket:
        """Promote a PROPOSED ticket to OPEN (the dispatchable state).

        This is the ONLY sanctioned path for the PROPOSED -> OPEN transition.
        The generic ``update`` path rejects that transition unconditionally —
        there is no bypass flag on the public API — so neither the agent MCP
        nor a standalone-MCP caller can self-approve and skip the operator gate.

        Raises ``ValueError`` if the ticket is not currently PROPOSED, so
        approve cannot silently reopen a closed/resolved/failed ticket.
        """
        prev = await self._collection.get(ticket_id)
        if prev is None:
            raise KeyError(ticket_id)
        if prev.status is not TicketStatus.PROPOSED:
            raise ValueError(
                f"ticket {ticket_id!r}: approve requires PROPOSED status "
                f"(is {prev.status.value})"
            )
        return await self._write_update(ticket_id, {"status": TicketStatus.OPEN})

    async def update(self, ticket_id: str, **fields) -> Ticket:
        prev = await self._collection.get(ticket_id)
        if prev is None:
            raise KeyError(ticket_id)
        # Operator-only approval gate: PROPOSED -> OPEN is reachable only via
        # ``approve()``. There is deliberately no escape-hatch parameter on this
        # public method — every generic update is gated, so the transition
        # cannot be smuggled through (e.g. an MCP/agent update_ticket call).
        new_status = fields.get("status")
        if new_status is not None:
            new_value = (
                new_status.value
                if isinstance(new_status, TicketStatus)
                else str(new_status)
            )
            if (
                prev.status is TicketStatus.PROPOSED
                and new_value == TicketStatus.OPEN.value
            ):
                raise ValueError(
                    f"ticket {ticket_id!r}: PROPOSED -> OPEN requires approval; "
                    "use TicketStore.approve()"
                )
        return await self._write_update(ticket_id, fields)

    async def _write_update(self, ticket_id: str, fields: dict) -> Ticket:
        """Validated write of an update row. NOT gated — callers (``update``,
        ``approve``) enforce their own transition rules first.

        Validate the would-be result BEFORE appending the update row to JSONL.
        Without this, an invalid update (e.g. a description change that drops
        the AC section) would land on disk before the model validator ran,
        leaving a row any subsequent store load would fail to deserialize. We
        construct the merged ``Ticket`` in-memory first — if the merge violates
        any model invariant Pydantic raises here, and the JSONL stays untouched.
        """
        prev = await self._collection.get(ticket_id)
        if prev is None:
            raise KeyError(ticket_id)
        prev_status = prev.status.value
        fields.setdefault("updated_at", datetime.now(timezone.utc))
        merged = prev.model_dump(by_alias=True)
        for key, value in fields.items():
            merged[key] = value
        Ticket.model_validate(merged)  # raises ValidationError if invalid
        await self._collection.update(ticket_id, fields)
        loaded = await self._collection.get(ticket_id)
        if loaded is None:
            raise RuntimeError(
                f"ticket {ticket_id!r} vanished after its own update write"
            )
        if self._on_status_change is not None and "status" in fields:
            new_status = loaded.status.value
            if new_status != prev_status:
                self._fire_status_change(ticket_id, prev_status, new_status)
        return loaded

    def _fire_status_change(
        self, ticket_id: str, from_state: str | None, to_state: str
    ) -> None:
        cb = self._on_status_change
        if cb is None:
            return
        try:
            result = cb(ticket_id, from_state, to_state)
        except Exception as exc:
            logger.warning("status-change callback raised: %s", exc, exc_info=exc)
            return
        if inspect.isawaitable(result):
            task = asyncio.create_task(result)  # type: ignore[arg-type]
            self._background_tasks.add(task)

            def _on_done(t: asyncio.Task) -> None:
                self._background_tasks.discard(t)
                if not t.cancelled() and (exc := t.exception()):
                    logger.warning(
                        "status-change callback raised: %s", exc, exc_info=exc
                    )

            task.add_done_callback(_on_done)

    async def update_status(self, ticket_id: str, status: TicketStatus) -> Ticket:
        return await self.update(ticket_id, status=status)

    async def find_in_progress_top_level(self) -> list[Ticket]:
        results: list[Ticket] = []
        for wt in TOP_LEVEL_WORK_TYPES:
            for status in (TicketStatus.IN_PROGRESS, TicketStatus.NEEDS_INFO):
                found = await self._collection.find_where(work_type=wt, status=status)
                results.extend(t for t in found if t.workflow != "thread")
        return results

    async def find_by_assignee(self, assignee: str) -> list[Ticket]:
        return await self._collection.find_where(assignee=assignee)

    async def find_by_parent(self, parent_id: str) -> list[Ticket]:
        return await self._collection.find_where(parent_id=parent_id)

    async def list_all(self) -> list[Ticket]:
        return await self._collection.find()

    async def find_ready(self) -> list[Ticket]:
        """Find open top-level tickets whose dependencies are all resolved."""
        candidates: list[Ticket] = []
        for wt in TOP_LEVEL_WORK_TYPES:
            candidates.extend(
                t
                for t in await self._collection.find_where(
                    work_type=wt, status=TicketStatus.OPEN
                )
                if t.workflow != "thread"
            )
        ready: list[Ticket] = []
        for ticket in candidates:
            if not ticket.blocked_by:
                ready.append(ticket)
                continue
            all_resolved = True
            for dep_id in ticket.blocked_by:
                dep = await self.get(dep_id)
                if dep is None or dep.status != TicketStatus.RESOLVED:
                    all_resolved = False
                    break
            if all_resolved:
                ready.append(ticket)
        return ready
