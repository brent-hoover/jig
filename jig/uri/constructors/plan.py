"""Constructors for ``project://plan/...`` URIs (PM output)."""
from __future__ import annotations

from typing import Literal

from jig.uri.constructors._segments import seg

LayerName = Literal["bones", "mvp", "final"]


def plan_build_uri() -> str:
    """``project://plan/build`` — the top-level build plan."""
    return "project://plan/build"


def plan_epic_uri(epic_id: str) -> str:
    """``project://plan/build/epics/<id>`` — one epic in the build plan."""
    return f"project://plan/build/epics/{seg(epic_id, 'epic_id')}"


def plan_layer_uri(epic_id: str, layer: LayerName) -> str:
    """``project://plan/build/epics/<id>/layers/<bones|mvp|final>``."""
    if layer not in ("bones", "mvp", "final"):
        raise ValueError(
            f"layer must be one of bones|mvp|final, got {layer!r}"
        )
    return (
        f"project://plan/build/epics/{seg(epic_id, 'epic_id')}"
        f"/layers/{layer}"
    )


def plan_ticket_uri(ticket_id: str) -> str:
    """``project://plan/tickets/<id>`` — a planner-tracked ticket."""
    return f"project://plan/tickets/{seg(ticket_id, 'ticket_id')}"


def plan_deferred_uri(deferred_id: str) -> str:
    """``project://plan/deferred/<id>`` — a deferred-queue entry."""
    return f"project://plan/deferred/{seg(deferred_id, 'deferred_id')}"
