"""CORE Model bones — Ticket/Thread re-export seams (Epic 1, task 5).

Bones phase keeps the definitions in their current locations and exposes them
under ``jig/model/`` so consumers can start importing from the model layer.
The real move (and shim removal) happens in MVP/Final.
"""

from __future__ import annotations

import jig.thread as thread_mod
import jig.ticket as ticket_mod
from jig.model import thread as model_thread
from jig.model import ticket as model_ticket


def test_model_ticket_reexports_current_ticket() -> None:
    assert model_ticket.Ticket is ticket_mod.Ticket
    assert model_ticket.TicketStatus is ticket_mod.TicketStatus


def test_model_thread_reexports_current_thread_types() -> None:
    assert model_thread.ThreadEntry is thread_mod.ThreadEntry
    assert model_thread.Question is thread_mod.Question
