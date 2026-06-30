"""Enforcement engine — checks + reviewer federation as a library Build invokes.

The headless seam is the ``Review`` contract: Build calls
``Review(diff, invariant_context) -> list[Finding]`` instead of reaching into
``jig/reviewers/`` directly. Two halves live behind it: ``mechanical`` (the
deterministic boundary + vocabulary checks) and ``reviewers`` (the LLM-judgment
federation). Bones stands up the contract + the package boundary; MVP migrates
``check_runner`` in, wires Build to the contract, and makes the review loop
headless.

The root re-exports only the contract surface (dependency-light); the mechanical
and reviewer surfaces are reached via their submodules.
"""

from __future__ import annotations

from jig.engines.enforcement.contract import InvariantContext, Review
from jig.engines.enforcement.mechanical.review import MechanicalReview

__all__ = [
    "InvariantContext",
    "MechanicalReview",
    "Review",
]
