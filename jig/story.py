"""Per-ticket narrative story assembly."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import (
    Answer,
    Decision,
    Escalation,
    Handoff,
    Note,
    Objection,
    Proposal,
    Question,
    Resolution,
    SystemEvent,
    ThreadEntry,
    Uncertain,
    Waiver,
)

_logger = logging.getLogger(__name__)


class StorySource(str, Enum):
    thread = "thread"
    log = "log"
    finding = "finding"


@dataclass(frozen=True)
class StoryEvent:
    ts: datetime
    source: StorySource
    kind: str
    level: str
    message: str
    ticket_id: str
    phase: str | None
    role: str | None
    raw: dict


# ---- Per-kind thread entry renderers --------------------------------------


def _render_thread_entry(entry: ThreadEntry) -> tuple[str, str]:
    """Return (kind_label, message) for a thread entry."""
    if isinstance(entry, Handoff):
        n_outputs = len(entry.outputs)
        return (
            "handoff",
            f"HANDOFF phase={entry.phase} by={entry.author} "
            f"outputs={n_outputs} state={entry.acceptance_state or 'pending'} "
            f"summary={entry.summary!r}",
        )
    if isinstance(entry, Question):
        q_text = (entry.question or "")[:80]
        blocking = " [BLOCKING]" if entry.is_blocking() else ""
        return (
            "question",
            f"QUESTION{blocking} by={entry.author} target={entry.target}: {q_text}",
        )
    if isinstance(entry, Answer):
        a_text = (entry.text or "")[:80]
        return (
            "answer",
            f"ANSWER by={entry.author} responds_to={entry.question_id}: {a_text}",
        )
    if isinstance(entry, Escalation):
        return (
            "escalation",
            f"ESCALATION by={entry.author} target={entry.target} reason={entry.reason}",
        )
    if isinstance(entry, Objection):
        return (
            "objection",
            f"OBJECTION by={entry.author}: {(entry.text or '')[:80]}",
        )
    if isinstance(entry, Resolution):
        return (
            "resolution",
            f"RESOLUTION by={entry.author}: responds_to={entry.objection_id}",
        )
    if isinstance(entry, Waiver):
        target_id = entry.objection_id or entry.check_failure_id or ""
        return (
            "waiver",
            f"WAIVER by={entry.author}: responds_to={target_id}",
        )
    if isinstance(entry, Decision):
        return (
            "decision",
            f"DECISION by={entry.author}: {(entry.decision or '')[:80]}",
        )
    if isinstance(entry, Note):
        return (
            "note",
            f"NOTE by={entry.author}: {(entry.text or '')[:80]}",
        )
    if isinstance(entry, Uncertain):
        return (
            "uncertain",
            f"UNCERTAIN by={entry.author}: {(entry.details or '')[:80]}",
        )
    if isinstance(entry, Proposal):
        return (
            "proposal",
            f"PROPOSAL by={entry.author} state={entry.state}: {entry.target}",
        )
    if isinstance(entry, SystemEvent):
        return _render_system_event(entry)
    return ("unknown", f"<{type(entry).__name__} by={entry.author}>")


def _render_system_event(ev: SystemEvent) -> tuple[str, str]:
    """Dispatch on event_type for a nicer one-line summary."""
    et = ev.event_type
    p = ev.payload or {}
    if et == "phase_start":
        return (
            f"system_event/{et}",
            f"PHASE START {p.get('phase', ev.content)} role={p.get('role', '?')}",
        )
    if et == "phase_end":
        ms = p.get("duration_ms", 0)
        return (
            f"system_event/{et}",
            f"PHASE END   {p.get('phase', '?')} "
            f"outcome={p.get('outcome', ev.content)} duration={ms}ms",
        )
    if et == "agent_run":
        return (
            f"system_event/{et}",
            f"AGENT RUN   {p.get('role', '?')} "
            f"turns={p.get('num_turns', '?')} "
            f"duration={p.get('duration_ms', 0)}ms",
        )
    if et == "phase_run":
        return (
            f"system_event/{et}",
            f"phase_run {ev.content} result={ev.phase_result}",
        )
    if et == "commit":
        return (
            f"system_event/{et}",
            f"commit {(ev.commit_sha or '')[:7]}: {ev.content}",
        )
    if et == "check_failure":
        return (
            f"system_event/{et}",
            f"check_failure {ev.check_name} verdict={ev.check_verdict}",
        )
    if et == "status_change":
        return (f"system_event/{et}", f"status_change {ev.content}")
    if et == "dep_merge_failed":
        return (f"system_event/{et}", f"dep_merge_failed {ev.content}")
    return (f"system_event/{et}", f"{et}: {ev.content}")


# ---- Log file scanning ----------------------------------------------------


def _parse_log_line(line: str) -> dict | None:
    """Parse a single JSONL log line. Return None on any error so a
    single corrupt line doesn't break the scan; log at WARNING so
    operators know the logs are lossy (fail-loud discipline)."""
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        _logger.warning("build_story: skipping unparseable log line: %s", exc)
        return None


def _iter_log_events_for_ticket(project_path: Path, ticket_id: str) -> list[StoryEvent]:
    log_dir = project_path / ".jig" / "logs"
    if not log_dir.is_dir():
        return []
    events: list[StoryEvent] = []
    for log_file in sorted(log_dir.glob("jig-*.jsonl")):
        try:
            with log_file.open() as f:
                for line in f:
                    rec = _parse_log_line(line)
                    if rec is None:
                        continue
                    if rec.get("ticket_id") != ticket_id:
                        continue
                    ts_raw = rec.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except ValueError:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    events.append(
                        StoryEvent(
                            ts=ts,
                            source=StorySource.log,
                            kind=rec.get("logger", "?"),
                            level=rec.get("level", "INFO"),
                            message=f"{rec.get('logger', '?')}: {rec.get('msg', '')}",
                            ticket_id=ticket_id,
                            phase=rec.get("phase"),
                            role=rec.get("role"),
                            raw=rec,
                        )
                    )
        except OSError as exc:
            _logger.warning("failed to read log file %s: %s", log_file, exc)
    return events


async def _iter_finding_events_for_ticket(
    project_path: Path, ticket_id: str
) -> list[StoryEvent]:
    """Emit one event per reviewer comment raised against ``ticket_id``
    and one per FindingAck row, interleaved by timestamp by the caller.

    Reviewer comments emit ``finding_raised`` events; acks emit
    ``finding_{addressed|resolved|reraised}`` according to the row's
    ``kind`` field. Each carries the row's ``created_at`` as timestamp.
    """
    from jig.finding_ids import compute_finding_ids, signature_of
    from jig.store.finding_acks import FindingAcksStore
    from jig.store.review_comments import ReviewCommentsStore

    rc_path = project_path / ".jig" / "store" / "review_comments.jsonl"
    acks_path = project_path / ".jig" / "store" / "finding_acks.jsonl"
    if not rc_path.is_file() and not acks_path.is_file():
        return []

    events: list[StoryEvent] = []

    if rc_path.is_file():
        rc_store = ReviewCommentsStore(rc_path)
        await rc_store.load()
        comments = await rc_store.for_ticket_chronological(ticket_id)
        ids = compute_finding_ids(comments)
        for c in comments:
            fid = ids.get(signature_of(c), "?")
            loc = c.file or "(diff-wide)"
            loc_full = f"{loc}:{c.line}" if c.line else loc
            events.append(
                StoryEvent(
                    ts=c.created_at,
                    source=StorySource.finding,
                    kind="finding_raised",
                    level="info",
                    message=(
                        f"[{fid}] {c.severity} at {loc_full} "
                        f"by {c.reviewer}: {c.prose[:200]}"
                    ),
                    ticket_id=ticket_id,
                    phase=None,
                    role=c.reviewer,
                    raw={"finding_id": fid, **c.model_dump(mode="json")},
                )
            )

    if acks_path.is_file():
        acks_store = FindingAcksStore(acks_path)
        await acks_store.load()
        acks = await acks_store.for_ticket(ticket_id)
        for ack in acks:
            events.append(
                StoryEvent(
                    ts=ack.created_at,
                    source=StorySource.finding,
                    kind=f"finding_{ack.kind}",
                    level="info",
                    message=(
                        f"[{ack.finding_id}] {ack.kind} cycle {ack.cycle} "
                        f"({ack.author}): {ack.prose[:200]}"
                    ),
                    ticket_id=ticket_id,
                    phase=None,
                    role=ack.author,
                    raw=ack.model_dump(mode="json"),
                )
            )

    return events


# ---- Public API -----------------------------------------------------------


async def build_story(
    ticket_id: str,
    *,
    project_path: Path,
    threads: ThreadStore,
    tickets: TicketStore | None = None,
    include_children: bool = False,
    since: datetime | None = None,
    _visited: set[str] | None = None,
) -> list[StoryEvent]:
    events: list[StoryEvent] = []
    if _visited is None:
        _visited = set()
    if ticket_id in _visited:
        _logger.warning("build_story: cycle detected at ticket %s; skipping", ticket_id)
        return events
    _visited.add(ticket_id)

    # 1. Thread entries for this ticket.
    entries = await threads.for_ticket(ticket_id)
    for entry in entries:
        kind_label, message = _render_thread_entry(entry)
        events.append(
            StoryEvent(
                ts=entry.created_at,
                source=StorySource.thread,
                kind=kind_label,
                level="info",
                message=message,
                ticket_id=ticket_id,
                phase=None,
                role=entry.author,
                raw=entry.model_dump(mode="json"),
            )
        )

    # 2. Log lines mentioning this ticket.
    events.extend(_iter_log_events_for_ticket(project_path, ticket_id))

    # 3. Reviewer findings + their ack history (fix-loop-context step 6).
    events.extend(await _iter_finding_events_for_ticket(project_path, ticket_id))

    # 4. Optionally include children.
    if include_children:
        if tickets is None:
            raise ValueError("include_children=True requires tickets= TicketStore")
        children = await tickets.find_by_parent(ticket_id)
        for child in children:
            child_events = await build_story(
                child.id,
                project_path=project_path,
                threads=threads,
                tickets=tickets,
                include_children=True,
                since=since,
                _visited=_visited,
            )
            events.extend(child_events)

    # 5. Filter by `since`.
    if since is not None:
        events = [e for e in events if e.ts >= since]

    events.sort(key=lambda e: e.ts)
    return events
