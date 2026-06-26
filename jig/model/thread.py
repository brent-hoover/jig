"""Thread domain types, exposed under the model layer.

Bones phase: a re-export seam over ``jig/thread.py`` so consumers can begin
importing thread entries from ``jig.model``. The definitions still live in
``jig/thread.py`` for compat; the real move (and shim removal) happens in
MVP/Final.
"""

from __future__ import annotations

from jig.thread import (
    Answer,
    Decision,
    DeferredItem,
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
    entry_content,
    parse_thread_entry,
)

__all__ = [
    "Answer",
    "Decision",
    "DeferredItem",
    "Escalation",
    "Handoff",
    "Note",
    "Objection",
    "Proposal",
    "Question",
    "Resolution",
    "SystemEvent",
    "ThreadEntry",
    "Uncertain",
    "Waiver",
    "entry_content",
    "parse_thread_entry",
]
