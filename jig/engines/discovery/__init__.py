"""Discovery engine — authors the spec from the operator interview.

Owns ``project://spec/...``. Bones declares the authority boundary and exposes
the PO-interview flow (``interview``); MVP extracts the conversation, unifies the
L0-L3 levels into one architectural-resolution interview, and absorbs
onboarding (``onboard.py``).
"""

from __future__ import annotations

from jig.engines.authoring import AuthoringEngine

ENGINE = AuthoringEngine(name="discovery", authority="spec")

__all__ = ["ENGINE"]
