"""Async write path for analytics events.

The ``EventEmitter`` is the single entry point used by the rest
of jig to record events. Two emit modes:

* ``await emit(event)`` — awaits persistence; use when the caller
  wants to know the event landed (rare; only correlation-critical
  flows).
* ``emit_nowait(event)`` — fires the persistence as a background
  task and returns immediately. The default for hot-path callers
  (agent.py, orchestrator.py, MCP servers) so analytics capture
  never blocks the operation it's observing.

Privacy: enforced at schema-design time, not here. The
``AnalyticsEvent`` discriminated union has no fields capable of
carrying prose, prompts, responses, or secrets. Adding such a
field requires a schema change and a code review — the place
where privacy lives. This emitter trusts its inputs.

If background tasks fail (disk full, write error), the failure
is logged but not propagated — analytics capture must not bring
down the orchestrator. Lost events are observable via the
log channel.
"""

from __future__ import annotations

import asyncio
import logging

from jig.analytics.events import AnalyticsEvent
from jig.analytics.store import AnalyticsStore

_logger = logging.getLogger(__name__)


class EventEmitter:
    """Async event emitter wrapping the persistent store.

    Background tasks created via ``emit_nowait`` are tracked so
    they can be awaited at shutdown — without that, in-flight
    writes get cancelled when the event loop closes and we lose
    the tail of the stream.
    """

    def __init__(self, store: AnalyticsStore) -> None:
        self._store = store
        self._pending: set[asyncio.Task[None]] = set()

    async def emit(self, event: AnalyticsEvent) -> str:
        """Persist an event synchronously. Returns the event id."""
        return await self._store.append(event)

    def emit_nowait(self, event: AnalyticsEvent) -> None:
        """Fire-and-forget persistence. Returns immediately.

        The caller doesn't get the event id back — if you need it,
        use ``emit`` instead. Most callers don't.
        """
        task = asyncio.create_task(self._emit_logged(event))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def _emit_logged(self, event: AnalyticsEvent) -> None:
        try:
            await self._store.append(event)
        except Exception:
            # Capture must not propagate — losing an event is
            # cheaper than crashing the orchestrator. The log
            # is the operator's only signal that we lost data.
            _logger.warning(
                "analytics event capture failed for kind=%s",
                event.kind,
                exc_info=True,
            )

    async def drain(self) -> None:
        """Await all in-flight nowait writes. Call at shutdown.

        Without this, ``asyncio.create_task`` writes get cancelled
        when the loop closes and the tail of the event stream is
        lost — exactly the period (shutdown / crash) most worth
        capturing.
        """
        if not self._pending:
            return
        await asyncio.gather(*list(self._pending), return_exceptions=True)
