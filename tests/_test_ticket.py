"""Shared helpers for Ticket construction in tests.

The Ticket model requires a discoverable Acceptance Criteria section
in the description for work-type tickets (FEATURE, BUGFIX, REFACTOR,
SPIKE, PERF, MIGRATION). Most tests don't care about AC content — they
construct Tickets to exercise orchestrator/store/MCP behaviour. This
module exposes a single placeholder string that satisfies the model
invariant without forcing each fixture to spell out a full AC list.

Tests that *do* care about the AC content (the AC-validator's own
tests, plus anything exercising the reviewer-test-adequacy flow)
should pass an explicit ``description`` instead of using the
placeholder.
"""

from __future__ import annotations

TICKET_AC_PLACEHOLDER: str = (
    "## Acceptance criteria\n"
    "- Test fixture placeholder; replace if the test cares about AC content.\n"
)


__all__ = ["TICKET_AC_PLACEHOLDER"]
