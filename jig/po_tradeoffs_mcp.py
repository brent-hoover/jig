"""MCP handlers for PO tradeoff-ledger authoring.

The PO calls these during L0/L3/planning to record deliberate scope decisions.
The tradeoff-compliance reviewer reads `.jig/spec/tradeoffs.yaml` to flag
tickets that appear to re-add deferred work.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jig.schemas.tradeoffs import Tradeoff
from jig.tradeoff_store import add_tradeoff, load_ledger


async def handle_add_tradeoff(
    *,
    project_path: Path,
    id: str,
    decision_summary: str,
    deferred: list[str],
    deferred_to: str,
    rationale: str,
    capability_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Record or update a tradeoff in the ledger.

    If a tradeoff with this ``id`` already exists it is replaced.
    """
    tradeoff = Tradeoff(
        id=id,
        decision_summary=decision_summary,
        deferred=deferred,
        deferred_to=deferred_to,  # type: ignore[arg-type]
        rationale=rationale,
        capability_ids=capability_ids or [],
    )
    ledger = add_tradeoff(project_path, tradeoff)
    return {
        "ok": True,
        "id": tradeoff.id,
        "total_tradeoffs": len(ledger.tradeoffs),
    }


async def handle_list_tradeoffs(
    *,
    project_path: Path,
    capability_id: str | None = None,
) -> dict[str, Any]:
    """Return all tradeoffs, optionally filtered by capability_id."""
    ledger = load_ledger(project_path)
    tradeoffs = (
        ledger.for_capability(capability_id)
        if capability_id
        else ledger.tradeoffs
    )
    return {
        "tradeoffs": [t.model_dump() for t in tradeoffs],
        "count": len(tradeoffs),
    }
