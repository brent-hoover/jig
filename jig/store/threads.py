"""Thread-entry store (doc 08 canonical).

``ThreadStore`` is the canonical store for doc-08 thread entries.
It operates against ``.jig/store/comments.jsonl`` and migrates legacy
Phase-3 Comment-shaped records on read (``_migrate_legacy``) so
historical JSONL files remain readable.

The store is intentionally thin — no index tricks for the
``has_unresolved_blocking`` helper because the in-memory scan is
fast enough at the ticket granularity we actually care about, and
keeping logic in pure Python means the index stays a performance
detail rather than part of the semantics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jig.store.collection import Collection
from jig.thread import ThreadEntry, parse_thread_entry


# Legacy ``Comment.kind`` values that fold into ``SystemEvent``.
# Phase 3 wrote these via the old typed-comment model; Phase 4 reads
# them back as ``SystemEvent(event_type=<kind>)``.
_LEGACY_SYSTEM_EVENT_KINDS = {"commit", "phase_run", "status_change"}


# Non-envelope fields used by the legacy Comment shape that we strip
# or rename rather than carry forward. Keyed by new-shape kind for
# clarity.
_LEGACY_PROPOSAL_RENAMES = {
    "proposal_target": "target",
    "proposal_section": "section",
    "proposal_change": "change",
    "proposal_state": "state",
    "proposal_parent_id": "parent_id",
    "proposal_owners": "owners",
    "proposal_spec_version": "spec_version",
}


def _envelope(raw: dict[str, Any]) -> dict[str, Any]:
    """Pull the shared envelope fields (id + ticket/author/timestamp).

    Normalizes the legacy ``_id`` alias back into ``_id`` so the
    discriminated-union parser sees it under the expected key.
    """
    env: dict[str, Any] = {
        "ticket_id": raw["ticket_id"],
        "author": raw["author"],
    }
    if "_id" in raw:
        env["_id"] = raw["_id"]
    elif "id" in raw:
        env["_id"] = raw["id"]
    if "created_at" in raw:
        env["created_at"] = raw["created_at"]
    return env


def _migrate_legacy(raw: dict[str, Any]) -> dict[str, Any]:
    """Transform a legacy Comment-shape record into a ThreadEntry dict.

    Idempotent: records already in the new shape pass through
    untouched. Unknown legacy shapes (missing ``kind``, etc.) are
    left alone — ``parse_thread_entry`` will fail loud with the
    pydantic validation error and the caller gets a readable
    traceback rather than a silent Note fallback.
    """
    kind = raw.get("kind")
    if kind is None:
        return raw

    # ---- system-primitive kinds → SystemEvent -----------------------
    if kind in _LEGACY_SYSTEM_EVENT_KINDS:
        migrated = _envelope(raw)
        migrated.update(
            kind="system_event",
            event_type=kind,
            content=raw.get("content", ""),
            commit_sha=raw.get("commit_sha"),
            phase_result=raw.get("phase_result"),
            phase_branch=raw.get("phase_branch"),
        )
        return migrated

    # ---- plain "comment" → Note -------------------------------------
    if kind == "comment":
        migrated = _envelope(raw)
        migrated.update(kind="note", text=raw.get("content", ""))
        return migrated

    # ---- legacy "decision" (Comment-shape) → Decision ---------------
    # New Decision needs decision+rationale; legacy only had content.
    if kind == "decision" and "decision" not in raw:
        migrated = _envelope(raw)
        migrated.update(
            kind="decision",
            decision=raw.get("content", ""),
            rationale="",
        )
        return migrated

    # ---- legacy "question" (Comment-shape) → Question ---------------
    # Old shape had content but no target/question split.
    if kind == "question" and "question" not in raw:
        migrated = _envelope(raw)
        migrated.update(
            kind="question",
            target=raw.get("target", "any_human"),
            question=raw.get("content", ""),
            blocking=bool(raw.get("blocking", False)),
        )
        return migrated

    # ---- legacy "answer" (Comment-shape) → Answer -------------------
    if kind == "answer" and "text" not in raw:
        migrated = _envelope(raw)
        migrated.update(
            kind="answer",
            question_id=raw.get("question_id", ""),
            text=raw.get("content", ""),
        )
        return migrated

    # ---- Phase 3E proposal envelope (on Comment) → Proposal ---------
    # Detectable by ``proposal_target`` presence; new Proposal uses
    # ``target``.
    if kind == "proposal" and "target" not in raw:
        migrated = _envelope(raw)
        migrated["kind"] = "proposal"
        for legacy_key, new_key in _LEGACY_PROPOSAL_RENAMES.items():
            if legacy_key in raw and raw[legacy_key] is not None:
                migrated[new_key] = raw[legacy_key]
        # Content on the legacy envelope mapped to prose; carry it
        # to rationale so the context isn't dropped.
        if raw.get("content"):
            migrated.setdefault("rationale", raw["content"])
        # target is required on the new shape; default empty and let
        # pydantic surface the problem if the record is actually
        # malformed.
        migrated.setdefault("target", raw.get("proposal_target") or "")
        migrated.setdefault("state", "pending")
        return migrated

    # Already in the new shape, or a kind native to Phase 4 (note,
    # objection, waiver, etc.) — no transform needed.
    return raw


# ---- store ----------------------------------------------------------------


class ThreadStore:
    """Typed thread-entry store.

    Writes use the new discriminated-union shape. Reads migrate
    legacy Phase-3 Comment-shaped records through ``_migrate_legacy``
    before handing them to ``parse_thread_entry``.
    """

    def __init__(self, path: Path) -> None:
        self._collection = Collection(
            path,
            index_fields=["ticket_id", "kind", "author"],
        )

    async def load(self) -> None:
        await self._collection.load()

    # ---- writes ----------------------------------------------------------

    async def post(self, entry: ThreadEntry) -> str:
        """Append a thread entry. Returns the entry's id.

        ``entry`` must already be a typed ``ThreadEntry`` (Question /
        Objection / etc.) — construct through the public pydantic
        models. Passing a raw dict is not supported; callers who have
        raw data should go through ``parse_thread_entry`` first.
        """
        raw = entry.model_dump(mode="json", by_alias=True)
        return await self._collection.insert(raw)

    async def update(self, entry_id: str, changes: dict[str, Any]) -> bool:
        """Patch fields on an existing entry — the close-by-asker
        and accept-handoff paths use this to flip state.

        The caller is responsible for semantically valid field
        names; the store doesn't re-validate the whole record on
        update (that's a Collection limitation).
        """
        return await self._collection.update(entry_id, changes)

    # ---- reads -----------------------------------------------------------

    def _load(self, raw: dict[str, Any]) -> ThreadEntry:
        return parse_thread_entry(_migrate_legacy(raw))

    async def get(self, entry_id: str) -> ThreadEntry | None:
        raw = await self._collection.get(entry_id)
        return None if raw is None else self._load(raw)

    async def for_ticket(self, ticket_id: str) -> list[ThreadEntry]:
        raws = await self._collection.find_where(ticket_id=ticket_id)
        entries = [self._load(r) for r in raws]
        entries.sort(key=lambda e: e.created_at)
        return entries

    async def find_by_kind(
        self, ticket_id: str, kind: str
    ) -> list[ThreadEntry]:
        """Entries on a ticket of the given new-shape ``kind``.

        Legacy ``commit`` / ``phase_run`` / ``status_change`` records
        surface under ``kind="system_event"`` — the migration collapses
        them. Other legacy kinds match 1:1 to the new shape.
        """
        entries = await self.for_ticket(ticket_id)
        return [e for e in entries if e.kind == kind]

    async def all_by_kind(self, kind: str) -> list[ThreadEntry]:
        """All entries of the given ``kind`` across every ticket.

        Intended for cross-ticket queries (e.g. "list every proposal
        in the project") so callers don't need to reach into
        ``_collection`` / ``_load`` private APIs. Ordering matches the
        underlying JSONL insertion order; sort downstream if needed.
        """
        raws = await self._collection.find(lambda r: r.get("kind") == kind)
        return [self._load(r) for r in raws]

    async def has_unresolved_blocking(
        self, ticket_id: str
    ) -> list[ThreadEntry]:
        """Blocking entries still open on this ticket.

        The dispatch layer (Task H) calls this before advancing a
        phase and refuses to move if the list is non-empty.
        """
        entries = await self.for_ticket(ticket_id)
        return [e for e in entries if e.is_blocking()]


__all__ = [
    "ThreadStore",
]
