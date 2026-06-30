"""Runtime bones — backwards-compat after the module→package conversion.

``jig/runtime.py`` became ``jig/runtime/`` package; the existing names must
still import from ``jig.runtime`` unchanged (15 importers depend on this).
"""

from __future__ import annotations


def test_spawn_types_still_import_from_jig_runtime() -> None:
    from jig.runtime import AgentSpawnContext, SpawnReason
    from jig.runtime.spawn_context import (
        AgentSpawnContext as CtxFromSubmodule,
    )

    assert AgentSpawnContext is CtxFromSubmodule
    assert SpawnReason.__name__ == "SpawnReason"
