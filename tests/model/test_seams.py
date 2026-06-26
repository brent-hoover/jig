"""CORE Model bones — Ticket/Thread re-export seams (Epic 1, task 5).

Bones phase keeps the definitions in their current locations and exposes them
under ``jig/model/`` so consumers can start importing from the model layer.
The real move (and shim removal) happens in MVP/Final.
"""

from __future__ import annotations

import subprocess
import sys

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


def test_model_root_does_not_expose_store_coupled_types() -> None:
    """The purity claim: Ticket/Thread stay behind submodule seams. Guard
    against a future ``__init__`` edit re-exporting them at the root."""
    import jig.model as model_root

    assert not hasattr(model_root, "Ticket")
    assert not hasattr(model_root, "ThreadEntry")


def test_importing_model_root_does_not_load_store_layer() -> None:
    """``import jig.model`` must not transitively pull in the store layer.

    Run in a clean subprocess — an in-process ``sys.modules`` diff would
    false-green because earlier tests already imported the store layer.
    """
    code = (
        "import jig.model, sys; "
        "print([m for m in sys.modules if m.startswith('jig.store')])"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "[]", (
        f"jig.model pulled in store modules: {out.stdout.strip()}"
    )
