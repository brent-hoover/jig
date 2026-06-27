"""The Living-Invariant entity model — a pure aggregate the invariants query.

These are the model.md entities reduced to exactly what the deterministic
invariants need, with **trace edges as first-class data** ("every trace edge
stored as data moves an intent-check from the LLM column to the deterministic
column" — model.md). Coverage/containment/vocabulary are pure graph queries over
a ``Model``; nothing here does I/O. Wiring real project artifacts into a
``Model`` is a downstream concern (Enforcement / Reconciliation MVP).

Scope: the fields the three deterministic invariants range over. The full
entity richness (Persona, Behavior, user stories, contract types) lands as the
invariants that need it do.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class TraceKind(str, Enum):
    """The trace edges linking intent to structure (model.md §Trace)."""

    REALIZED_BY = "realized_by"  # Capability -> Contract
    COVERED_BY = "covered_by"  # Journey -> Suite


class Trace(BaseModel):
    """One trace edge: ``src`` (capability/journey) ``kind`` ``dst``
    (contract/suite)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    src: str
    dst: str
    kind: TraceKind


class Boundary(BaseModel):
    """A unit with an inside and an outside; exposes ``contracts`` across its
    edge and has exactly one ``owner`` (the ownership invariant — Final)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    owner: str | None = None
    contracts: tuple[str, ...] = ()


class Dependency(BaseModel):
    """An *allowed* edge: ``consumer`` boundary may consume ``contract`` of
    ``provider`` boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    consumer: str
    provider: str
    contract: str


class OntologyHome(BaseModel):
    """A place a concept (``term``) is defined. A term with >1 distinct home is
    the "one concept, one home" violation (vocabulary invariant)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    term: str
    home: str


class Model(BaseModel):
    """The Living-Invariant aggregate. Immutable, pure, I/O-free; the invariants
    range over it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    capabilities: tuple[str, ...] = ()
    contracts: tuple[str, ...] = ()
    journeys: tuple[str, ...] = ()
    suites: tuple[str, ...] = ()
    boundaries: tuple[Boundary, ...] = ()
    dependencies: tuple[Dependency, ...] = ()
    traces: tuple[Trace, ...] = ()
    ontology: tuple[OntologyHome, ...] = ()
