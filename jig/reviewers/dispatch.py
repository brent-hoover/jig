"""Reviewer-set selection + two-cadence dispatch for ticket-scoped review.

Two entry points:

- ``select_reviewers_for_ticket`` — returns the reviewer ids that
  should run on a ticket (end-of-ticket cadence). Picks based on
  ``ticket.layer`` and ``ticket.reviewer_set``.
- ``dispatch_for_cadence`` — actually runs the reviewers at a given
  cadence (``per_commit`` or ``end_of_ticket``) and returns a map of
  ``reviewer_id`` → comments. Per-commit cadence runs only the
  mechanical subset; end-of-ticket runs the full default-on set.

Defaults policy (for ``select_reviewers_for_ticket``):

- ``reviewer_set`` non-empty → honour it as authored (the Planner PM
  owns the set on MVP+).
- Empty ``reviewer_set`` with ``layer == "bones"`` → contract-compliance
  + cross-cutting-policy (bones default-on; cross-cutting is universal
  per design).
- Empty ``reviewer_set`` with ``layer in {"mvp", "final"}`` → the four
  shipped mechanical reviewers (contract + intent + cross-cutting +
  spec). Judgment reviewers join in a later G MVP follow-on.
- Empty ``reviewer_set`` with ``layer`` unset → empty list (we can't
  tell what defaults apply without the layer).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from jig.reviewers.comment import ReviewerComment
from jig.ticket import Ticket

# Reviewer ids. The constants live here (rather than in the per-reviewer
# modules) so the dispatch table can reference them without a circular
# import — the reviewers import ``ReviewerComment`` from ``comment.py``;
# the dispatch imports the reviewers; the constants stay above both.
BONES_REVIEWER_ID = "contract-compliance"
INTENT_REVIEWER_ID = "intent-compliance"
CROSS_CUTTING_REVIEWER_ID = "cross-cutting-policy"
SPEC_COMPLIANCE_REVIEWER_ID = "spec-compliance"
# Track D MVP — visual_compliance gates UI tickets (those with
# non-empty visual_references). Tier: senior per design; MVP ships the
# basic mechanical version (no vision), Final adds screenshot diff.
VISUAL_COMPLIANCE_REVIEWER_ID = "visual-compliance"

# Mechanical reviewer ids — the per-commit-cadence-eligible set per
# design §"Two-cadence review". These are the deterministic checks
# (single-digit-second latency) that fit in the per-commit budget;
# judgment reviewers (and intent-compliance, which runs against
# authored artifacts rather than a worktree diff) are end-of-ticket
# only. The order here is the dispatch order — contract-compliance
# first matches the natural reading order of the comment stream.
_MECHANICAL_REVIEWER_IDS: list[str] = [
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    # Visual-compliance MVP is mechanical (no vision); the per-commit
    # cadence runs it on every commit so the dev catches missing /
    # broken / unreferenced wireframes before integration. The reviewer
    # itself no-ops when the ticket has no visual_references, so non-UI
    # tickets pay no cost.
    VISUAL_COMPLIANCE_REVIEWER_ID,
]

# Bones layer default-on set. Cross-cutting policies are universal rules
# (PII, secrets, no-direct-cross-module-db) per design §"Reviewer
# federation — selection logic" — they apply at every layer including
# bones, so cross-cutting joins contract-compliance in the bones
# default-on subset.
_BONES_DEFAULTS: list[str] = [BONES_REVIEWER_ID, CROSS_CUTTING_REVIEWER_ID]

# MVP / final default set. ``contract-compliance`` is reused from the
# bones default — every ticket benefits from the diff/AC checks, not
# just bones tickets. Intent-compliance, cross-cutting-policy, and
# spec-compliance join from MVP onward.
_MVP_FINAL_DEFAULTS: list[str] = [
    BONES_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
]


def select_reviewers_for_ticket(ticket: Ticket) -> list[str]:
    """Return the reviewer ids to run on ``ticket``.

    Branches:

    - ``reviewer_set`` non-empty → honour it as authored (the Planner
      PM owns the set on MVP+). Visual-compliance is appended when the
      ticket has non-empty ``visual_references`` even if the planner
      didn't add it explicitly — the gating signal is the references
      list itself, not an opt-in flag.
    - ``layer == "bones"`` with empty reviewer_set → default-on
      contract-compliance only (bones budget).
    - ``layer in ("mvp", "final")`` with empty reviewer_set → the
      MVP/final mechanical defaults, plus visual-compliance when the
      ticket implements UI.
    - ``layer`` unset and empty reviewer_set → empty list. The
      Coordinator materializes tickets with ``layer="bones"``; a
      missing layer means we can't tell what defaults apply.
    """
    selected: list[str]
    if ticket.reviewer_set:
        selected = list(ticket.reviewer_set)
    elif ticket.layer == "bones":
        selected = list(_BONES_DEFAULTS)
    elif ticket.layer in ("mvp", "final"):
        selected = list(_MVP_FINAL_DEFAULTS)
    else:
        return []

    # Visual-compliance is gated on the presence of visual_references —
    # not on a planner opt-in. UI tickets get the reviewer regardless of
    # how the reviewer_set was authored. Append rather than replace so
    # planner-authored sets still get their other reviewers.
    if (
        ticket.visual_references
        and VISUAL_COMPLIANCE_REVIEWER_ID not in selected
    ):
        selected.append(VISUAL_COMPLIANCE_REVIEWER_ID)
    return selected


# Backward-compatible alias. The bones-era name keeps working for the
# small handful of external callers; new code should use
# ``select_reviewers_for_ticket``.
should_run_for_bones = select_reviewers_for_ticket


async def dispatch_for_cadence(
    ticket: Ticket,
    project_root: Path,
    cadence: Literal["per_commit", "end_of_ticket"],
    *,
    worktree_path: Path | None = None,
    base_ref: str = "main",
) -> dict[str, list[ReviewerComment]]:
    """Run the reviewers selected for ``ticket`` at ``cadence``.

    Returns ``{reviewer_id: [ReviewerComment, ...]}``. The map shape
    (rather than a flat list) lets callers drive per-reviewer
    disposition (auto-apply, lead-reviewer dedup, analytics
    attribution) without re-parsing ``reviewer`` on every comment.

    Cadence semantics per design §"Two-cadence review":

    - ``per_commit`` runs **only** the mechanical reviewers
      (contract-compliance, cross-cutting-policy, spec-compliance).
      These are the deterministic checks that fit in the
      single-digit-second per-commit budget. ``ticket.reviewer_set``
      does not gate this set — per-commit composition is fixed at
      the deterministic core regardless of the planner's authored
      end-of-ticket set, because the per-commit job is "catch the
      mistake before the dev compounds it" not "run the full
      federation".
    - ``end_of_ticket`` runs the full ``select_reviewers_for_ticket``
      result — mechanical + intent-compliance for MVP+. Intent-
      compliance runs against authored artifacts, not the worktree
      diff; the synthetic operator's existing intent-reviewer
      invocation path stays in place. ``dispatch_for_cadence``
      returns intent-compliance with an empty list at end-of-ticket
      so callers can see "this reviewer is selected" without forcing
      an artifact load here.

    Comments emitted at ``per_commit`` cadence carry ``cadence=
    "per_commit"``; end-of-ticket comments carry the field default
    (``end_of_ticket``).

    For MVP scope, the dispatch is **explicitly invoked** — the
    orchestrator's per-commit hook integration is a separate Track G
    MVP follow-on. The synthetic operator and tests can call this
    directly today.
    """
    # Local imports to avoid a circular dependency at module load
    # time: reviewer modules import ``ReviewerComment`` from
    # ``comment.py``; pulling them in here at function-call time
    # keeps ``dispatch.py`` importable without dragging the whole
    # reviewer surface in.
    from jig.reviewers.contract_compliance import ContractComplianceReviewer
    from jig.reviewers.cross_cutting_policy import CrossCuttingPolicyReviewer
    from jig.reviewers.spec_compliance import SpecComplianceReviewer

    if cadence == "per_commit":
        # Per-commit cadence runs the mechanical subset regardless
        # of the ticket's reviewer_set. The reviewer_set governs
        # end-of-ticket composition; per-commit is fixed at the
        # deterministic core because that's the only set the latency
        # budget allows.
        reviewer_ids = list(_MECHANICAL_REVIEWER_IDS)
    else:
        reviewer_ids = select_reviewers_for_ticket(ticket)

    out: dict[str, list[ReviewerComment]] = {}

    if BONES_REVIEWER_ID in reviewer_ids:
        comments = await ContractComplianceReviewer().review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )
        out[BONES_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if CROSS_CUTTING_REVIEWER_ID in reviewer_ids:
        comments = await CrossCuttingPolicyReviewer().review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )
        out[CROSS_CUTTING_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if SPEC_COMPLIANCE_REVIEWER_ID in reviewer_ids:
        comments = await SpecComplianceReviewer().review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )
        out[SPEC_COMPLIANCE_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if VISUAL_COMPLIANCE_REVIEWER_ID in reviewer_ids:
        # Local import: visual_compliance imports from
        # jig.spec_loader, jig.wireframes — pulling them in lazily
        # keeps dispatch.py importable without dragging the VD
        # surface in for non-UI projects.
        from jig.reviewers.visual_compliance import VisualComplianceReviewer

        comments = await VisualComplianceReviewer().review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )
        out[VISUAL_COMPLIANCE_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if INTENT_REVIEWER_ID in reviewer_ids and cadence == "end_of_ticket":
        # Intent reviewer runs against authored artifacts (Modules,
        # Contracts, etc.), not against a worktree diff. It's
        # end-of-ticket only and the artifact load happens in the
        # synthetic operator's existing intent-reviewer path. We
        # surface the empty list here so callers can see the
        # reviewer was selected without us running it incorrectly
        # against a diff it can't read.
        out[INTENT_REVIEWER_ID] = []

    return out


def _tag_cadence(
    comments: list[ReviewerComment],
    cadence: Literal["per_commit", "end_of_ticket"],
) -> list[ReviewerComment]:
    """Stamp ``cadence`` onto each comment.

    Reviewer.review() returns comments with the default cadence
    (``end_of_ticket``); per-commit dispatch overrides via
    ``model_copy``. Done in the dispatch layer rather than threaded
    through every reviewer signature so the per-reviewer code stays
    cadence-unaware.
    """
    if cadence == "end_of_ticket":
        # Already the default; avoid the copy churn.
        return comments
    return [c.model_copy(update={"cadence": cadence}) for c in comments]


__all__ = [
    "BONES_REVIEWER_ID",
    "CROSS_CUTTING_REVIEWER_ID",
    "INTENT_REVIEWER_ID",
    "SPEC_COMPLIANCE_REVIEWER_ID",
    "VISUAL_COMPLIANCE_REVIEWER_ID",
    "dispatch_for_cadence",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
