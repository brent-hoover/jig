"""Mechanical boundary enforcement — the new home for the boundary-rule compiler.

Bones: re-exports ``jig.boundary_rules`` so the Enforcement package owns this
surface without a risky physical move. MVP relocates the implementation here and
turns ``jig/boundary_rules.py`` into the shim (then removes it), and adds the
per-commit import deny-list check that makes a boundary violation a
build-blocking ``Finding`` rather than a reviewer judgment.
"""

from __future__ import annotations

from jig.boundary_rules import (
    build_deny_rule,
    build_relative_deny_rule,
    generate_boundary_rules,
)

__all__ = [
    "build_deny_rule",
    "build_relative_deny_rule",
    "generate_boundary_rules",
]
