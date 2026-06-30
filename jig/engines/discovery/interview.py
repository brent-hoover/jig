"""Discovery interview — the PO ask/answer conversation that authors the spec.

Bones: exposes the existing PO-interview entry points (today in
``jig/init_workflow.py``) at their new home. MVP extracts the conversation here
and replaces the v1-flat / v2-L0-L3 split with one Discovery interview at
architectural resolution; the interview writes ``project://spec/...``.
"""

from __future__ import annotations

from jig.init_workflow import (
    next_incomplete_level,
    run_po_conversation,
    run_po_l0_conversation,
    run_po_l1_conversation,
    run_po_l2_conversation,
    run_po_l3_conversation,
)

__all__ = [
    "next_incomplete_level",
    "run_po_conversation",
    "run_po_l0_conversation",
    "run_po_l1_conversation",
    "run_po_l2_conversation",
    "run_po_l3_conversation",
]
