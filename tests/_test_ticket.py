"""Shared placeholders for Ticket / Epic AC-required fixtures in tests.

Two model invariants require an Acceptance Criteria surface:

- ``jig.ticket.Ticket`` requires a discoverable ``## Acceptance criteria``
  section in ``description`` for work-type tickets (FEATURE, BUGFIX,
  REFACTOR, SPIKE, PERF, MIGRATION).
- ``jig.schemas.plan.Epic`` requires a non-empty
  ``acceptance_criteria: list[str]``.

Most tests don't care about AC content — they construct Tickets or
Epics to exercise orchestrator/store/MCP/Coordinator behaviour. This
module exposes shared placeholders so the wording lives in one place
(no drift across 30+ fixture sites if the placeholder text ever needs
to change).

Tests that *do* care about AC content (the AC-validator's own tests,
anything exercising the reviewer-test-adequacy flow, the Coordinator
render integration test) should pass explicit values instead of
using these placeholders.
"""

from __future__ import annotations

EPIC_AC_PLACEHOLDER_BULLET: str = (
    "Test fixture placeholder; replace if the test cares about AC content."
)
"""Single placeholder bullet for ``Epic.acceptance_criteria`` test fixtures."""

EPIC_AC_PLACEHOLDER: list[str] = [EPIC_AC_PLACEHOLDER_BULLET]
"""List form for direct assignment to ``Epic.acceptance_criteria``."""

TICKET_AC_PLACEHOLDER: str = f"## Acceptance criteria\n- {EPIC_AC_PLACEHOLDER_BULLET}\n"
"""Full ``description`` value for Ticket fixtures. Keeps the bullet
wording in lockstep with ``EPIC_AC_PLACEHOLDER_BULLET``."""


__all__ = [
    "EPIC_AC_PLACEHOLDER",
    "EPIC_AC_PLACEHOLDER_BULLET",
    "TICKET_AC_PLACEHOLDER",
]
