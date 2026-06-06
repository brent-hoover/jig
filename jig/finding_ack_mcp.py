"""MCP tool handlers for the finding-ack surface.

Two handlers, two roles:

- ``handle_mark_finding_addressed`` — back-routed phase agents (dev,
  test, document, validate) call this once per finding they believe
  they have fixed.
- ``handle_mark_finding_resolved`` — judgment-reviewer roles call this
  once per finding they confirm is fixed in the new diff.

Both validate that ``finding_id`` resolves against the ticket's current
``ReviewCommentsStore`` contents (via :mod:`jig.finding_ids`); an
unknown id raises ``UnknownFindingError`` so the agent sees its
mistake.

Both are idempotent within a cycle: a retry that produces the same
``(finding_id, kind, author, cycle)`` returns the existing ack id
without writing a second row. A different cycle or a different author
does create a new row — the audit trail tracks history. The dedup is
"first wins" — the prose from the first call is preserved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from jig.finding_ids import compute_finding_ids
from jig.store.finding_acks import FindingAck, FindingAcksStore
from jig.store.review_comments import ReviewCommentsStore

_MAX_PROSE = 500

AckKind = Literal["addressed", "resolved", "reject"]


class UnknownFindingError(ValueError):
    """Raised when a tool call references a ``finding_id`` that doesn't
    resolve to any current finding on the ticket. Sub-classes
    ``ValueError`` so existing argument-validation handlers keep working.
    """

    def __init__(self, ticket_id: str, finding_id: str) -> None:
        self.ticket_id = ticket_id
        self.finding_id = finding_id
        super().__init__(
            f"unknown finding {finding_id!r} on ticket {ticket_id!r} — "
            "no reviewer comment maps to this id"
        )


def _review_comments_path(project_path: Path) -> Path:
    return project_path / ".jig" / "store" / "review_comments.jsonl"


def _finding_acks_path(project_path: Path) -> Path:
    return project_path / ".jig" / "store" / "finding_acks.jsonl"


async def _validate_finding_id(
    project_path: Path, ticket_id: str, finding_id: str
) -> None:
    store = ReviewCommentsStore(_review_comments_path(project_path))
    await store.load()
    comments = await store.for_ticket_chronological(ticket_id)
    ids = compute_finding_ids(comments)
    if finding_id not in ids.values():
        raise UnknownFindingError(ticket_id, finding_id)


def _validate_prose(prose: str) -> None:
    if len(prose) > _MAX_PROSE:
        raise ValueError(
            f"prose exceeds {_MAX_PROSE} character limit (got {len(prose)} chars)"
        )


async def _write_ack(
    project_path: Path,
    *,
    ticket_id: str,
    finding_id: str,
    kind: AckKind,
    author: str,
    cycle: int,
    prose: str,
) -> str:
    """Write or dedupe an ack row.

    Idempotency window: same (ticket_id, finding_id, kind, author, cycle)
    — re-calls within that tuple return the existing row id without a
    new write. Anything broader (different cycle, different author)
    creates a fresh row.
    """
    acks_path = _finding_acks_path(project_path)
    acks_path.parent.mkdir(parents=True, exist_ok=True)
    acks_store = FindingAcksStore(acks_path)
    await acks_store.load()
    existing = await acks_store.find_id_for(
        ticket_id=ticket_id,
        finding_id=finding_id,
        kind=kind,
        author=author,
        cycle=cycle,
    )
    if existing is not None:
        return existing
    return await acks_store.append(
        FindingAck(
            ticket_id=ticket_id,
            finding_id=finding_id,
            kind=kind,
            author=author,
            cycle=cycle,
            prose=prose,
        )
    )


async def handle_mark_finding_addressed(
    *,
    project_path: Path,
    ticket_id: str,
    finding_id: str,
    author: str,
    cycle: int,
    how_resolved: str,
    kind: Literal["addressed", "reject"] = "addressed",
) -> str:
    """Record the dev's claim that ``finding_id`` is fixed or disputed.

    ``ticket_id``, ``author``, and ``cycle`` come from the MCP factory's
    per-agent context. ``finding_id``, ``how_resolved``, and ``kind`` come
    from the agent's tool call. ``kind`` defaults to ``"addressed"``; pass
    ``kind="reject"`` to record a disagreement with the finding (requires
    prose rationale). ``kind="resolved"`` is reserved for reviewer agents
    and is rejected here.
    """
    if kind not in ("addressed", "reject"):
        raise ValueError(
            f"invalid kind {kind!r} for mark_finding_addressed — "
            "use 'addressed' to claim a fix or 'reject' to dispute the finding"
        )
    _validate_prose(how_resolved)
    await _validate_finding_id(project_path, ticket_id, finding_id)
    return await _write_ack(
        project_path,
        ticket_id=ticket_id,
        finding_id=finding_id,
        kind=kind,
        author=author,
        cycle=cycle,
        prose=how_resolved,
    )


async def handle_mark_finding_resolved(
    *,
    project_path: Path,
    ticket_id: str,
    finding_id: str,
    author: str,
    cycle: int,
    confirmation: str,
) -> str:
    """Record a reviewer's confirmation that ``finding_id`` is fixed."""
    _validate_prose(confirmation)
    await _validate_finding_id(project_path, ticket_id, finding_id)
    return await _write_ack(
        project_path,
        ticket_id=ticket_id,
        finding_id=finding_id,
        kind="resolved",
        author=author,
        cycle=cycle,
        prose=confirmation,
    )


__all__ = [
    "UnknownFindingError",
    "handle_mark_finding_addressed",
    "handle_mark_finding_resolved",
]
