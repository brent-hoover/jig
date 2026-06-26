"""Enforcement bones — mechanical + reviewer surfaces exposed at the new home
(Epic 5, tasks 3-4).

Bones establishes the enforcement package boundary by re-exporting the current
modules at their new home (the physical relocation + shim removal is MVP, which
also avoids the ``reviewers/__init__`` <-> ``dispatch`` import cycle for now).
The objects must be identical to the canonical ones.
"""

from __future__ import annotations


def test_boundary_rules_exposed_under_enforcement_mechanical() -> None:
    import jig.boundary_rules as canonical
    from jig.engines.enforcement.mechanical import boundary_rules as moved

    assert moved.generate_boundary_rules is canonical.generate_boundary_rules
    assert moved.build_deny_rule is canonical.build_deny_rule


def test_reviewer_dispatch_exposed_under_enforcement_reviewers() -> None:
    import jig.reviewers.dispatch as canonical
    from jig.engines.enforcement.reviewers import dispatch as moved

    assert moved.select_reviewers_for_ticket is canonical.select_reviewers_for_ticket
    assert moved.known_llm_reviewer_ids is canonical.known_llm_reviewer_ids
