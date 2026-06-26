"""Mechanical (deterministic) enforcement — boundary + vocabulary checks.

These are the checks that are graph/rule queries, not LLM judgments: import
deny-lists per declared module boundary, and "one concept, one home" vocabulary
drift. Bones exposes the boundary-rule surface and a stub ``MechanicalReview``;
MVP fills in real findings.
"""

from __future__ import annotations

from jig.engines.enforcement.mechanical.boundary_rules import (
    build_deny_rule,
    build_relative_deny_rule,
    generate_boundary_rules,
)
from jig.engines.enforcement.mechanical.review import MechanicalReview

__all__ = [
    "MechanicalReview",
    "build_deny_rule",
    "build_relative_deny_rule",
    "generate_boundary_rules",
]
