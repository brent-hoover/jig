"""Check result records per doc 10.

Checks declared in ``.jig/checks.yaml`` (Phase 2E) execute through the
runners in Phase 5 Task A/B and persist their outcome here. One record
per check run — append-only, historical. The gating layer (Task D)
reads the latest batch for a ticket+phase to decide whether to advance
or bounce a handoff.

Record shape mirrors the checkpoint channel: StoreModel with
``ticket_id`` / ``phase`` / ``author`` / ``created_at`` plus the
check-specific verdict fields. ``author`` is the role (for scripted
runs it's ``"harness"``; for agent checks it's the check-agent role).
``commit_sha`` pins the worktree state the check saw so evaluator
views can surface "check result X was against commit Y".

Severity is copied from the check definition into the record so the
gating layer can decide without re-loading the catalog — and so
audits over time stay interpretable if the catalog changes under
them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import Field

from jig.checks import CheckSeverity
from jig.store.models import StoreModel


CheckType = Literal["scripted", "implementation_aware_agent", "black_box_agent"]

# ``pass`` / ``fail`` are the normal outcomes; ``timeout`` is a fail
# surfaced separately so callers can distinguish environmental issues
# from genuine rejections; ``error`` covers harness faults (command
# not found, runner exception) — treated as fail for gating but worth
# calling out in the record.
CheckVerdict = Literal["pass", "fail", "timeout", "error"]


class CheckResult(StoreModel):
    """One execution of a catalog check against a ticket's worktree."""

    ticket_id: str
    phase: str
    author: str = "harness"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    check_name: str
    check_type: CheckType
    verdict: CheckVerdict
    severity: CheckSeverity

    # stdout+stderr combined, trimmed by the runner. Free-form text;
    # the gating layer quotes the tail into the check_failure
    # SystemEvent so the evaluator sees what broke.
    output: str = ""

    started_at: datetime
    finished_at: datetime

    # Worktree commit at the time the check ran. Populated by the
    # runner from ``git rev-parse HEAD`` in the working dir. Empty
    # string means "no commit context" (fresh worktree / non-git dir).
    commit_sha: str = ""


__all__ = [
    "CheckResult",
    "CheckType",
    "CheckVerdict",
]
