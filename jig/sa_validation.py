"""SA authoring-quality validators (Track C MVP commits 3).

Two mechanical, no-LLM validators that the SA MVP authoring path
calls during the incremental discovery loop:

1. ``validate_behavioral_contract`` — per-contract authoring quality.
   Returns warning strings (not exceptions) so the
   ``module_set_behavioral_contract`` handler can surface them in its
   response and let the SA agent iterate before ``arch_finalize``.

2. ``validate_module_checklist`` — per-module checklist enforcement
   per ``docs/v2.0/sa-architecture/design.md`` §"The SA checklist". Returns
   the set of unmet categories. ``arch_finalize`` raises when this
   set is non-empty AND the module hasn't declared the missing
   categories in its ``n_a_categories`` exemption field.

Pattern mirrors ``jig.reviewers.intent_compliance``: validators
return data, callers decide whether to raise. Keeps the validators
testable in isolation and lets the same primitive support both the
"warn the agent inline" and "block the finalize" use cases.
"""
from __future__ import annotations

from jig.schemas.arch import BehavioralContract, ContractsFile, Module


__all__ = [
    "CHECKLIST_CATEGORIES",
    "validate_behavioral_contract",
    "validate_module_checklist",
]


# Minimum prose length for behavioral-contract precondition /
# postcondition. Below this almost certainly indicates boilerplate
# ("ok", "must work", "as expected") rather than a real constraint
# the reviewer can mechanically check. 30 characters is a soft floor
# — long enough to force "the function returns None" rather than
# "returns None", short enough not to reject genuinely terse cases.
_MIN_CLAUSE_LEN = 30


# The SA checklist categories per design.md §"The SA checklist".
# Each module must address each (or list it in n_a_categories). Order
# matters only for stable error messages — alphabetical is the
# sort target.
CHECKLIST_CATEGORIES: tuple[str, ...] = (
    "ownership",
    "external_dependencies",
    "integration_ac",
    "behavioral_contracts",
    "cross_cutting_compliance",
)


# ---- behavioral-contract authoring quality -------------------------------


def validate_behavioral_contract(contract: BehavioralContract) -> list[str]:
    """Return warning strings about authoring quality.

    Mechanical checks only — no LLM judgment. Each warning is a short
    operator-readable string the SA agent can act on:

    - **No constraint**: postcondition AND side_effect_required both
      missing → the contract isn't actually fencing anything, just a
      label.
    - **No anchor**: applies_to AND scope both None → the contract
      isn't tied to a specific capability/module/scope, so the
      reviewer can't know where to apply it.
    - **Thin precondition / postcondition**: a non-empty value below
      ~30 chars almost always indicates boilerplate; flag it so the
      agent can either expand or remove the field.

    Returns ``[]`` when the contract is well-authored.
    """
    warnings: list[str] = []

    if contract.postcondition is None and contract.side_effect_required is None:
        warnings.append(
            "behavioral contract has no postcondition and no "
            "side_effect_required — it isn't constraining anything; "
            "either add one or remove the contract."
        )

    if contract.applies_to is None and contract.scope is None:
        warnings.append(
            "behavioral contract has neither applies_to nor scope — "
            "the reviewer can't tell where to enforce it; add at least "
            "one anchor."
        )

    for field_name in ("precondition", "postcondition"):
        value = getattr(contract, field_name)
        if value is not None and len(value.strip()) < _MIN_CLAUSE_LEN:
            warnings.append(
                f"behavioral contract {field_name!r} is shorter than "
                f"{_MIN_CLAUSE_LEN} characters — likely boilerplate; "
                "expand or drop the field."
            )

    return warnings


# ---- per-module SA checklist ---------------------------------------------


def _has_ownership(cf: ContractsFile | None) -> bool:
    return cf is not None and bool(cf.owns)


def _has_external_dependencies(cf: ContractsFile | None) -> bool:
    return cf is not None and bool(cf.external_dependencies)


def _has_integration_ac(cf: ContractsFile | None) -> bool:
    return cf is not None and bool(cf.integration_ac)


def _has_behavioral_contracts(cf: ContractsFile | None) -> bool:
    return cf is not None and bool(cf.behavioral_contracts)


def _has_cross_cutting_compliance(cf: ContractsFile | None) -> bool:
    """Bones-MVP heuristic: any integration_ac counts as compliance.

    Per design.md §"Contract consumption", cross-cutting policies
    auto-generate integration AC on every relevant capability. The
    full check (does each module follow each architecture-level
    cross_cutting_policy?) requires resolving the policy registry +
    field-level PII tagging, which is the field-tagging design from
    the resolved-decisions section — that lands later.

    For MVP we treat "module has at least one integration_ac entry"
    as compliance acknowledgement. The intent is that the SA at
    least walked cross-cutting concerns enough to express them on
    a capability AC.
    """
    return cf is not None and bool(cf.integration_ac)


_CHECKERS = {
    "ownership": _has_ownership,
    "external_dependencies": _has_external_dependencies,
    "integration_ac": _has_integration_ac,
    "behavioral_contracts": _has_behavioral_contracts,
    "cross_cutting_compliance": _has_cross_cutting_compliance,
}


def validate_module_checklist(
    module: Module, contracts: ContractsFile | None
) -> set[str]:
    """Return the set of checklist categories ``module`` hasn't addressed.

    A category is considered "addressed" when its mechanical check
    (above) returns True OR the category appears in
    ``module.n_a_categories``. The latter is the SA's explicit
    declaration that the category legitimately doesn't apply (e.g.
    a CRUD-only module that has no behavioral invariants worth
    stating).

    Returns ``set()`` when the module is fully addressed.
    """
    n_a = set(module.n_a_categories)
    missing: set[str] = set()
    for category, check in _CHECKERS.items():
        if category in n_a:
            continue
        if not check(contracts):
            missing.add(category)
    return missing
