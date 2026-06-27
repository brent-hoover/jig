"""The 5 architecture invariants as pure functions over the Model.

These are the payload of the Living-Invariant model (see ``architecture/model.md``
§"The invariants"). Each returns the violations it finds — an empty list means
the invariant holds.

The three deterministic invariants (``coverage``, ``containment``, ``vocabulary``)
are implemented as pure graph/set queries over the ``Model`` aggregate (#215).
``conformance`` and ``ownership`` remain stubs — they land in Final (conformance
needs code analysis; ownership pairs with the cascade work).

Pure: no I/O imports. ``Code`` is a placeholder for the code aggregate
``conformance`` will range over; typed ``Any`` until Final.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict

from jig.model.entities import Model, TraceKind

# ``conformance`` ranges over code structure (Final). Placeholder until then.
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
    by >=1 Suite — no orphans in either direction (model.md invariant 1)."""
    realized_caps = {t.src for t in model.traces if t.kind is TraceKind.REALIZED_BY}
    realizing_contracts = {
        t.dst for t in model.traces if t.kind is TraceKind.REALIZED_BY
    }
    covered_journeys = {t.src for t in model.traces if t.kind is TraceKind.COVERED_BY}
    covering_suites = {t.dst for t in model.traces if t.kind is TraceKind.COVERED_BY}

    findings: list[Finding] = []
    for cap in model.capabilities:
        if cap not in realized_caps:
            findings.append(
                Finding(
                    invariant="coverage",
                    message="capability realized by no contract",
                    subject=cap,
                )
            )
    for journey in model.journeys:
        if journey not in covered_journeys:
            findings.append(
                Finding(
                    invariant="coverage",
                    message="journey covered by no suite",
                    subject=journey,
                )
            )
    for contract in model.contracts:
        if contract not in realizing_contracts:
            findings.append(
                Finding(
                    invariant="coverage",
                    message="contract realizes no capability",
                    subject=contract,
                )
            )
    for suite in model.suites:
        if suite not in covering_suites:
            findings.append(
                Finding(
                    invariant="coverage",
                    message="suite covers no journey",
                    subject=suite,
                )
            )
    return findings


def conformance(model: Model, code: Code) -> list[Finding]:
    """The code honors its Contracts; declared Behaviors pass. Stub — needs code
    analysis (Final)."""
    return []


def containment(model: Model, code: Code = None) -> list[Finding]:
    """No Boundary reaches into another except through a declared Dependency on a
    declared Contract (model.md invariant 3).

    The pure model check: every declared ``Dependency`` must target a real
    boundary and a contract that boundary actually exposes. (Checking *actual*
    code reaches against the declared dependencies needs code analysis and lands
    with Reconciliation / Final; ``code`` is accepted but unused here.)
    """
    boundary_by_id = {b.id: b for b in model.boundaries}
    findings: list[Finding] = []
    for dep in model.dependencies:
        provider = boundary_by_id.get(dep.provider)
        if provider is None:
            findings.append(
                Finding(
                    invariant="containment",
                    message=f"dependency on undeclared boundary {dep.provider!r}",
                    subject=dep.consumer,
                )
            )
        elif dep.contract not in provider.contracts:
            findings.append(
                Finding(
                    invariant="containment",
                    message=(
                        f"dependency on contract {dep.contract!r} not exposed by "
                        f"boundary {dep.provider!r}"
                    ),
                    subject=dep.consumer,
                )
            )
    return findings


def vocabulary(model: Model) -> list[Finding]:
    """Every concept has exactly one canonical home — count concepts with >1 home
    (model.md invariant 4, "one concept, one home")."""
    homes: dict[str, set[str]] = defaultdict(set)
    for entry in model.ontology:
        homes[entry.term.strip().lower()].add(entry.home)

    findings: list[Finding] = []
    for term, term_homes in homes.items():
        if len(term_homes) > 1:
            findings.append(
                Finding(
                    invariant="vocabulary",
                    message=f"concept has {len(term_homes)} homes: {sorted(term_homes)}",
                    subject=term,
                )
            )
    return findings


def ownership(model: Model) -> list[Finding]:
    """Every Boundary has exactly one owning role (model.md invariant 5). Stub —
    lands in Final with the cascade work."""
    return []
