"""The ``Review`` contract — Enforcement's headless-invocable seam.

Build invokes Enforcement through ``Review(diff, invariant_context) -> list[Finding]``
instead of calling ``jig/reviewers/`` directly. A ``Review`` is anything callable
with that shape: the deterministic mechanical checks (boundary, vocabulary) and —
later — the reviewer federation all satisfy it, and the eval harness can drive it
without the daemon. Findings are the canonical ``jig.model.Finding`` (Epic 1).

Kept dependency-light: imports only the model, so ``jig.engines.enforcement``
stays cheap and the contract is the only thing Build needs to know about.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from jig.model import Finding


@dataclass(frozen=True)
class InvariantContext:
    """What a ``Review`` needs to evaluate the invariants against a diff — the
    declared model + where the project lives.

    Bones: a light placeholder carrying the project root. MVP fills it with the
    declared Model (boundaries, ontology, trace edges) the checks evaluate
    against.
    """

    project_root: Path | None = None


@runtime_checkable
class Review(Protocol):
    """Enforce the invariants on a diff and return the violations found.

    Async: the implementations behind it are I/O-bound (the reviewer federation
    spawns agents; mechanical checks shell out to semgrep), and the orchestrator
    that invokes it runs an event loop. Pinning the seam async now avoids a
    contract-breaking change once Build depends on it.
    """

    async def __call__(
        self, diff: str, invariant_context: InvariantContext
    ) -> list[Finding]: ...
