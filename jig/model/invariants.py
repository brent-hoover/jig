"""The 5 architecture invariants as pure functions over the Model.

These are the payload of the Living-Invariant model (see ``architecture/model.md``
§"The invariants"). Each returns the violations it finds — an empty list means
the invariant holds.

Bones phase: the bodies are stubs returning no findings. The MVP fills in real
graph queries over the Model's trace edges (coverage, containment, vocabulary
are deterministic; conformance and ownership land in Final). The signatures are
the contract Build and Enforcement depend on, so they are pinned now.

Pure: no I/O imports. ``Model`` and ``Code`` are placeholders for the entity
aggregates that later epics build; typed as ``Any`` until then.
"""

from __future__ import annotations

from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict

# Placeholder aliases — replaced by real entity aggregates in later epics.
Model: TypeAlias = Any
Code: TypeAlias = Any


class Finding(BaseModel):
    """One invariant violation.

    ``invariant`` names which check raised it; ``subject`` identifies the
    entity at fault (a capability id, ontology term, boundary name, …) when
    known.
    """

    model_config = ConfigDict(extra="forbid")

    invariant: str
    message: str
    subject: str | None = None


def coverage(model: Model) -> list[Finding]:
    """Every Capability is realized by >=1 Contract and every Journey covered
    by >=1 Suite — no orphans in either direction."""
    return []


def conformance(model: Model, code: Code) -> list[Finding]:
    """The code honors its Contracts; declared Behaviors pass."""
    return []


def containment(model: Model, code: Code) -> list[Finding]:
    """No Boundary reaches into another except through a declared Dependency on
    a declared Contract."""
    return []


def vocabulary(model: Model) -> list[Finding]:
    """Every term used exists in the Ontology and every concept has exactly one
    canonical home (count concepts with >1 home)."""
    return []


def ownership(model: Model) -> list[Finding]:
    """Every Boundary has exactly one owning role."""
    return []
