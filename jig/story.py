"""Per-ticket narrative story assembly.

Merges thread entries and structured log lines for a single ticket
into a time-ordered list of ``StoryEvent`` records. The CLI
(``jig story``) and a future TUI view consume this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from jig.store.threads import ThreadStore


class StorySource(str, Enum):
    thread = "thread"
    log = "log"


@dataclass(frozen=True)
class StoryEvent:
    """A single event on the ticket's narrative timeline.

    * ``ts`` — timezone-aware UTC timestamp.
    * ``source`` — where the event came from.
    * ``kind`` — for thread events, the entry kind (``handoff``,
      ``question``, etc.) or for SystemEvents the ``event_type``.
      For log events, the logger name (``jig.orchestrator``, etc.).
    * ``level`` — log level for log events; ``"info"`` for thread.
    * ``message`` — pre-formatted, ready to print.
    * ``raw`` — original record (thread entry dict or parsed log line)
      for callers that want more than the pre-formatted message.
    """

    ts: datetime
    source: StorySource
    kind: str
    level: str
    message: str
    ticket_id: str
    phase: str | None
    role: str | None
    raw: dict


async def build_story(
    ticket_id: str,
    *,
    project_path: Path,
    threads: ThreadStore,
    include_children: bool = False,
    since: datetime | None = None,
) -> list[StoryEvent]:
    """Return a time-ordered list of events for a ticket.

    Merges:
      * thread entries for ``ticket_id``
      * log lines under ``project_path/.jig/logs/*.jsonl`` whose
        ``ticket_id`` field matches
      * (if ``include_children``) the same for child tickets

    Sorted ascending by ``ts``.
    """
    # Task 12 fills in the body. Returning [] until then keeps the
    # skeleton test green.
    return []
