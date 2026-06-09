"""IssueService — context-free CRUD/link/approve over the per-project store.

This is the single validated path shared by the ``jig issue`` CLI and the
standalone stdio MCP. It takes no worktree, agent identity, or message bus —
just a project root — so any process that can reach ``<root>/.jig/store/`` can
file and read well-formed work items.

Issues created here land as ``PROPOSED`` (non-dispatchable). Promotion to the
dispatchable ``OPEN`` state is only via :meth:`approve`; the generic update
path rejects it, keeping the operator gate intact.
"""

from pathlib import Path

from pydantic import ValidationError

from jig.store.tickets import TicketStore
from jig.store.threads import ThreadStore
from jig.thread import Note, ThreadEntry
from jig.ticket import Size, Ticket, TicketStatus, WorkType


class IssueService:
    def __init__(self, project_root: Path) -> None:
        store_dir = project_root / ".jig" / "store"
        self._tickets = TicketStore(store_dir / "tickets.jsonl")
        self._threads = ThreadStore(store_dir / "comments.jsonl")
        self._loaded = False

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self._tickets.load()
            await self._threads.load()
            self._loaded = True

    async def _require(self, ref: str) -> Ticket:
        ticket = await self._tickets.resolve_ref(ref)
        if ticket is None:
            raise KeyError(f"issue {ref!r} not found")
        return ticket

    # ---- create ---------------------------------------------------------

    async def create(
        self,
        *,
        title: str,
        work_type: str,
        description: str,
        size: str = "m",
        created_by: str = "cli",
        labels: list[str] | None = None,
        parent: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> Ticket:
        await self._ensure_loaded()

        try:
            wt = WorkType(work_type)
        except ValueError:
            raise ValueError(
                f"Unknown work_type {work_type!r}. "
                f"Valid values: {[w.value for w in WorkType]}"
            ) from None
        try:
            sz = Size(size)
        except ValueError:
            raise ValueError(
                f"Unknown size {size!r}. Valid values: {[s.value for s in Size]}"
            ) from None

        parent_id = (await self._require(parent)).id if parent else None

        try:
            ticket = Ticket(
                work_type=wt,
                size=sz,
                status=TicketStatus.PROPOSED,
                title=title,
                description=description,
                created_by=created_by,
                labels=labels or [],
                parent_id=parent_id,
            )
        except ValidationError as exc:
            # AC-required (and any other model invariant) surfaces here BEFORE
            # anything is written — fail loud with a clean message.
            raise ValueError(str(exc)) from exc

        await self._tickets.create(ticket)
        if blocked_by:
            await self.link(ticket.key, blocked_by=blocked_by)
        return await self._require(ticket.key)

    # ---- read -----------------------------------------------------------

    async def get(self, ref: str) -> Ticket:
        await self._ensure_loaded()
        return await self._require(ref)

    async def list(
        self,
        *,
        status: str | None = None,
        work_type: str | None = None,
        label: str | None = None,
        assignee: str | None = None,
    ) -> list[Ticket]:
        await self._ensure_loaded()
        tickets = await self._tickets.all()

        def keep(t: Ticket) -> bool:
            if status is not None and t.status.value != status:
                return False
            if work_type is not None and t.work_type.value != work_type:
                return False
            if label is not None and label not in t.labels:
                return False
            if assignee is not None and t.assignee != assignee:
                return False
            return True

        return [t for t in tickets if keep(t)]

    async def comments(self, ref: str) -> list[ThreadEntry]:
        await self._ensure_loaded()
        ticket = await self._require(ref)
        return await self._threads.for_ticket(ticket.id)

    # ---- mutate ---------------------------------------------------------

    async def update(self, ref: str, **fields) -> Ticket:
        await self._ensure_loaded()
        ticket = await self._require(ref)
        return await self._tickets.update(ticket.id, **fields)

    async def close(self, ref: str) -> Ticket:
        return await self.update(ref, status=TicketStatus.CLOSED)

    async def approve(self, ref: str) -> Ticket:
        await self._ensure_loaded()
        ticket = await self._require(ref)
        return await self._tickets.approve(ticket.id)

    async def comment(self, ref: str, text: str, *, author: str = "cli") -> str:
        await self._ensure_loaded()
        ticket = await self._require(ref)
        return await self._threads.post(
            Note(ticket_id=ticket.id, author=author, text=text)
        )

    async def link(
        self,
        ref: str,
        *,
        blocks: list[str] | None = None,
        blocked_by: list[str] | None = None,
        parent: str | None = None,
        remove: bool = False,
    ) -> Ticket:
        await self._ensure_loaded()
        ticket = await self._require(ref)

        if parent is not None:
            new_parent = None if remove else (await self._require(parent)).id
            await self._tickets.update(ticket.id, parent_id=new_parent)

        for dep_ref in blocked_by or []:
            dep = await self._require(dep_ref)
            await self._edge(ticket.id, "blocked_by", dep.id, remove)
            await self._edge(dep.id, "blocks", ticket.id, remove)

        for tgt_ref in blocks or []:
            tgt = await self._require(tgt_ref)
            await self._edge(ticket.id, "blocks", tgt.id, remove)
            await self._edge(tgt.id, "blocked_by", ticket.id, remove)

        return await self._require(ref)

    async def _edge(self, ticket_id: str, field: str, value: str, remove: bool) -> None:
        ticket = await self._tickets.get(ticket_id)
        assert ticket is not None
        current: list[str] = list(getattr(ticket, field))
        if remove:
            updated = [x for x in current if x != value]
        else:
            updated = current if value in current else current + [value]
        if updated != current:
            await self._tickets.update(ticket_id, **{field: updated})
