"""Constructors for ``project://design/...`` URIs (VD output)."""

from __future__ import annotations

from jig.uri.constructors._segments import seg


def design_wireframe_uri(screen_id: str) -> str:
    """``project://design/wireframes/<screen-id>`` — one wireframe HTML."""
    return f"project://design/wireframes/{seg(screen_id, 'screen_id')}"


def design_system_tokens_uri() -> str:
    """``project://design/system/tokens`` — design tokens YAML."""
    return "project://design/system/tokens"


def design_system_components_uri() -> str:
    """``project://design/system/components`` — component library YAML."""
    return "project://design/system/components"


def design_system_brand_uri() -> str:
    """``project://design/system/brand`` — brand voice/tone/logo refs."""
    return "project://design/system/brand"


def design_frontend_uri() -> str:
    """``project://design/frontend`` — frontend stack declaration."""
    return "project://design/frontend"
