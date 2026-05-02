"""Analytics event capture for jig.

Per ``docs/analytics/problem.md``: every operation worth observing
emits a structured event into an append-only stream. Capture
decisions can't be retrofitted, so the schema is locked from v1
forward — additive changes only; breaking changes require an
``event_version`` bump.

This package contains:

* ``events`` — typed event models, exported as the
  ``AnalyticsEvent`` discriminated union.
* ``store`` — ``AnalyticsStore`` wrapping the append-only
  ``.jig/store/events.jsonl`` persistence.
* ``emitter`` — ``EventEmitter``, the async write path used by
  the rest of jig to record events.

Privacy: events carry structured metadata only. Prompts,
responses, and tool-argument bodies live in other artifacts
(threads, MCP logs, contract files); events reference them by
URI rather than embedding them. No field on any event type
should hold prose, secrets, or PII; if a future event type
needs to surface free-form rationale, store it elsewhere and
reference it by id.

v1 implementation status: schema + persistence + async emit
are wired up here. Wiring emission throughout the rest of the
codebase (orchestrator, agent, MCP servers, TUI) is the next
implementation step — see ``events.py`` module docstring for
the full taxonomy and ``store.py`` for persistence shape.
"""

from jig.analytics.events import AnalyticsEvent, EVENT_VERSION
from jig.analytics.emitter import EventEmitter
from jig.analytics.store import AnalyticsStore

__all__ = [
    "AnalyticsEvent",
    "AnalyticsStore",
    "EventEmitter",
    "EVENT_VERSION",
]
