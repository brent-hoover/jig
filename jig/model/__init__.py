"""CORE Model — the Living-Invariant entities and invariants, pure and I/O-free.

This package is the foundation layer: domain entities plus the 5 invariant
functions. Everything else (Substrate, Runtime, Build, …) depends on it.

The package root re-exports only the genuinely-pure surface (``OntologyTerm``,
``Finding``, the invariants). Ticket/Thread live behind submodule seams
(``jig.model.ticket``, ``jig.model.thread``) so importing the model root does
not pull in the store layer.
"""

from __future__ import annotations

from jig.model.invariants import (
    Finding,
    conformance,
    containment,
    coverage,
    ownership,
    vocabulary,
)
from jig.model.ontology import OntologyTerm

__all__ = [
    "Finding",
    "OntologyTerm",
    "conformance",
    "containment",
    "coverage",
    "ownership",
    "vocabulary",
]
