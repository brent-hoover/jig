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

Specialty reviewer auto-selection (Track G Final, per design
§"Reviewer federation — selection logic"):

- ``reviewer-security`` joins when the ticket has any of the labels
  ``touches-auth`` / ``touches-pii`` / ``touches-secrets`` /
  ``touches-payments`` OR the ticket's module has tier_hint=SA in
  ``architecture.yaml``.
- ``reviewer-performance`` joins when the ticket has the
  ``perf-budget`` label OR the linked integration AC carries
  perf-budget keywords (``latency`` / ``throughput`` / ``p99`` /
  ``under N ms`` / ``requests per``).
- ``reviewer-architectural`` joins when the ticket has the
  ``touches-contract`` label OR ``dev_tier == "sa"`` OR
  ``contract_amendment`` is populated.

These auto-selections fire on top of any planner-authored
``reviewer_set`` (additive, never subtractive) so a planner can't
accidentally suppress security/perf/arch coverage by omission.
"""
from __future__ import annotations

import re
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
# Track D Final — accessibility (WCAG AA mechanical) + responsive-
# design enforcement. Both default-on for Final-layer tickets that
# carry a non-empty visual_references list. Mechanical: no LLM.
ACCESSIBILITY_REVIEWER_ID = "accessibility"
RESPONSIVE_REVIEWER_ID = "responsive-design"

# Track G Final — specialty reviewers auto-selected by ticket
# characteristics (labels, dev_tier, module tier_hint, AC text).
# Each is an LLM-driven judgment reviewer; the role configs live in
# ``jig/defaults/roles/reviewer_<name>.yaml``.
SECURITY_REVIEWER_ID = "reviewer-security"
PERFORMANCE_REVIEWER_ID = "reviewer-performance"
ARCHITECTURAL_REVIEWER_ID = "reviewer-architectural"

# Labels that trigger specialty-reviewer auto-selection. Sets so
# membership tests stay O(1) for the common case (a ticket usually
# has 0–3 labels).
_SECURITY_LABELS: frozenset[str] = frozenset({
    "touches-auth",
    "touches-pii",
    "touches-secrets",
    "touches-payments",
})
_PERFORMANCE_LABELS: frozenset[str] = frozenset({"perf-budget"})
_ARCHITECTURAL_LABELS: frozenset[str] = frozenset({"touches-contract"})

# Perf-budget AC keyword regex. Matches the design's enumerated
# triggers: latency, throughput, p99, under-N-ms phrasing, or
# requests-per phrasing. Case-insensitive; whole-word boundaries on
# the named keywords so "delaying" doesn't match "lay" etc.
_PERF_AC_RE: re.Pattern[str] = re.compile(
    r"\b(?:latency|throughput|p99|p95|under\s+\d+\s*ms|requests?\s+per)\b",
    re.IGNORECASE,
)

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
    # Track D Final — accessibility + responsive reviewers are
    # mechanical too, so they ride per-commit alongside visual-
    # compliance at no extra LLM cost. No-ops on non-UI tickets.
    ACCESSIBILITY_REVIEWER_ID,
    RESPONSIVE_REVIEWER_ID,
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


def select_reviewers_for_ticket(
    ticket: Ticket,
    *,
    project_root: Path | None = None,
) -> list[str]:
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

    Specialty reviewer auto-selection (Track G Final) runs on top of
    whichever branch fired above, additive only:

    - Security: labels touch auth/PII/secrets/payments OR module is
      SA-tier in architecture.yaml.
    - Performance: ``perf-budget`` label OR linked integration AC
      contains perf-budget keywords.
    - Architectural: ``touches-contract`` label OR ``dev_tier == "sa"``
      OR ``contract_amendment`` populated.

    ``project_root`` is optional — when omitted the dispatch falls back
    to ticket-only signals (labels + dev_tier + contract_amendment).
    Passing the project root unlocks the architecture.yaml + contracts
    lookups (module tier_hint + AC text scan).
    """
    selected: list[str]
    if ticket.reviewer_set:
        selected = list(ticket.reviewer_set)
    elif ticket.layer == "bones":
        selected = list(_BONES_DEFAULTS)
    elif ticket.layer in ("mvp", "final"):
        selected = list(_MVP_FINAL_DEFAULTS)
    else:
        # Layer unset → don't apply MVP/final defaults, but still let
        # specialty reviewers fire (a SA-tier amendment ticket should
        # always be reviewed even if its layer is missing).
        selected = []

    # Visual-compliance is gated on the presence of visual_references —
    # not on a planner opt-in. UI tickets get the reviewer regardless of
    # how the reviewer_set was authored. Append rather than replace so
    # planner-authored sets still get their other reviewers.
    if (
        ticket.visual_references
        and VISUAL_COMPLIANCE_REVIEWER_ID not in selected
    ):
        selected.append(VISUAL_COMPLIANCE_REVIEWER_ID)

    # Track D Final — accessibility + responsive reviewers ride the
    # same gating signal as visual-compliance (non-empty
    # visual_references). Mechanical, bounded cost. Both no-op when
    # wireframes are missing — visual-compliance already emits the
    # critical there.
    if (
        ticket.visual_references
        and ACCESSIBILITY_REVIEWER_ID not in selected
    ):
        selected.append(ACCESSIBILITY_REVIEWER_ID)
    if (
        ticket.visual_references
        and RESPONSIVE_REVIEWER_ID not in selected
    ):
        selected.append(RESPONSIVE_REVIEWER_ID)

    # Specialty reviewer auto-selection (Track G Final). Each helper
    # returns True iff the reviewer should fire; we append in
    # selection order so the markdown rendering reads predictably.
    if SECURITY_REVIEWER_ID not in selected and _needs_security_review(
        ticket, project_root
    ):
        selected.append(SECURITY_REVIEWER_ID)
    if PERFORMANCE_REVIEWER_ID not in selected and _needs_performance_review(
        ticket, project_root
    ):
        selected.append(PERFORMANCE_REVIEWER_ID)
    if ARCHITECTURAL_REVIEWER_ID not in selected and _needs_architectural_review(
        ticket
    ):
        selected.append(ARCHITECTURAL_REVIEWER_ID)

    # Specialty reviewers fired on a layer-unset ticket should not
    # promote the ticket to "no reviewers when none asked for one";
    # the empty-list branch above already returned []. But if any
    # specialty reviewer was added we now have a meaningful list, so
    # return it. If still empty (no defaults, no specialties), preserve
    # the bones-era contract: return [] so callers see "nothing to run".
    return selected


def _needs_security_review(
    ticket: Ticket, project_root: Path | None
) -> bool:
    """True iff ``ticket`` should pull in the security reviewer.

    Triggers: any of the security labels OR the ticket's module is
    SA-tier in architecture.yaml. The architecture lookup is
    short-circuited when ``project_root`` is None.
    """
    if any(label in _SECURITY_LABELS for label in ticket.labels):
        return True
    if project_root is not None and ticket.module_id is not None:
        if _module_tier_hint(project_root, ticket.module_id) == "sa":
            return True
    return False


def _needs_performance_review(
    ticket: Ticket, project_root: Path | None
) -> bool:
    """True iff ``ticket`` should pull in the performance reviewer.

    Triggers: ``perf-budget`` label OR linked integration AC contains
    perf-budget keywords. The AC scan is short-circuited when
    ``project_root`` is None or the ticket has no module_id.
    """
    if any(label in _PERFORMANCE_LABELS for label in ticket.labels):
        return True
    if project_root is None or ticket.module_id is None:
        return False
    return _integration_ac_mentions_perf(
        project_root, ticket.module_id, ticket.capability_ids
    )


def _needs_architectural_review(ticket: Ticket) -> bool:
    """True iff ``ticket`` should pull in the architectural reviewer.

    Triggers: ``touches-contract`` label OR ``dev_tier == "sa"`` OR
    ``contract_amendment`` populated. No project_root needed — every
    signal lives on the ticket itself.
    """
    if any(label in _ARCHITECTURAL_LABELS for label in ticket.labels):
        return True
    if ticket.dev_tier == "sa":
        return True
    if ticket.contract_amendment:
        return True
    return False


def _module_tier_hint(project_root: Path, module_id: str) -> str | None:
    """Return ``architecture.yaml``'s tier_hint for ``module_id``, or None.

    Lazy / forgiving: returns None when the architecture file is
    missing, malformed, or the module isn't declared. Specialty
    selection should fail open (not over-trigger) when the
    architecture lookup is uncertain.
    """
    # Local import to keep dispatch.py importable in contexts that
    # don't load the full schema surface.
    from jig.spec_loader import load_architecture

    try:
        arch = load_architecture(project_root)
    except (FileNotFoundError, ValueError):
        return None
    for module in arch.modules:
        if module.id == module_id:
            return module.tier_hint.value
    return None


def _integration_ac_mentions_perf(
    project_root: Path,
    module_id: str,
    capability_ids: list[str],
) -> bool:
    """True iff any integration-AC ``must`` text matches ``_PERF_AC_RE``.

    Scopes the scan to the ticket's claimed capabilities when set;
    falls back to scanning every AC when ``capability_ids`` is empty
    (a senior dev ticket without explicit scoping still benefits from
    the perf reviewer).
    """
    from jig.spec_loader import module_contracts_path
    from jig.schemas.arch import ContractsFile
    import yaml

    contracts_path = module_contracts_path(project_root, module_id)
    if not contracts_path.is_file():
        return False
    try:
        contracts = ContractsFile.model_validate(
            yaml.safe_load(contracts_path.read_text()) or {}
        )
    except (ValueError, yaml.YAMLError):
        return False

    cap_set = set(capability_ids)
    for ac in contracts.integration_ac:
        if cap_set and ac.capability not in cap_set:
            continue
        for must_text in ac.must:
            if _PERF_AC_RE.search(must_text):
                return True
    return False


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
        # Block 2 (Important 2): forward project_root so
        # ``select_reviewers_for_ticket`` can pull in architecture-
        # driven specialty reviewers (SA-tier module security, perf-AC
        # scans). Pre-Block-2 dispatch dropped project_root here, so
        # those triggers were silently skipped through the dispatcher.
        reviewer_ids = select_reviewers_for_ticket(
            ticket, project_root=project_root,
        )

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

    if ACCESSIBILITY_REVIEWER_ID in reviewer_ids:
        from jig.reviewers.accessibility import AccessibilityReviewer

        comments = await AccessibilityReviewer().review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )
        out[ACCESSIBILITY_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if RESPONSIVE_REVIEWER_ID in reviewer_ids:
        from jig.reviewers.responsive import ResponsiveDesignReviewer

        comments = await ResponsiveDesignReviewer().review(
            ticket,
            project_root,
            worktree_path=worktree_path,
            base_ref=base_ref,
        )
        out[RESPONSIVE_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if INTENT_REVIEWER_ID in reviewer_ids and cadence == "end_of_ticket":
        # Intent reviewer runs against authored artifacts (Modules,
        # Contracts, etc.), not against a worktree diff. Final
        # scope wires the project-wide intent pass here: load every
        # authored v2 artifact off disk and run the per-artifact +
        # cross-artifact uniqueness checks. The pass is cheap and
        # deterministic so end-of-ticket cadence is the right home;
        # per-commit stays mechanical-only because the pass crosses
        # files and would amortize poorly across many commits.
        from jig.reviewers.intent_compliance import IntentComplianceReviewer

        reviewer = IntentComplianceReviewer()
        by_uri = await reviewer.review_project(project_root)
        flat: list[ReviewerComment] = []
        for comments in by_uri.values():
            flat.extend(comments)
        out[INTENT_REVIEWER_ID] = _tag_cadence(flat, cadence)

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
    "ACCESSIBILITY_REVIEWER_ID",
    "ARCHITECTURAL_REVIEWER_ID",
    "BONES_REVIEWER_ID",
    "CROSS_CUTTING_REVIEWER_ID",
    "INTENT_REVIEWER_ID",
    "PERFORMANCE_REVIEWER_ID",
    "RESPONSIVE_REVIEWER_ID",
    "SECURITY_REVIEWER_ID",
    "SPEC_COMPLIANCE_REVIEWER_ID",
    "VISUAL_COMPLIANCE_REVIEWER_ID",
    "dispatch_for_cadence",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
