"""Visual Design engine — authors the frontend/wireframe design.

Owns ``project://design/...``. Bones declares the authority boundary only — the
VD engine itself (light for Jig-the-TUI, full for general projects) is Final-phase
work; there is no existing VD flow to extract in Bones.
"""

from __future__ import annotations

from jig.engines.authoring import AuthoringEngine

ENGINE = AuthoringEngine(name="visual_design", authority="design")

__all__ = ["ENGINE"]
