"""Architecture engine — authors the architecture from the SA<->operator loop.

Owns ``project://arch/...``. Bones declares the authority boundary and exposes
the SA loop (``sa_loop``); MVP unifies the three SA roles into one size-adaptive
SAU that produces the full architecture artifact set (Phase 1) and a
tracer-bullet execution plan (Phase 2).
"""

from __future__ import annotations

from jig.engines.authoring import AuthoringEngine

ENGINE = AuthoringEngine(name="architecture", authority="arch")

__all__ = ["ENGINE"]
