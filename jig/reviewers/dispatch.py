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

import asyncio
import logging
import re
from pathlib import Path
from typing import Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

from jig.reviewers.comment import ReviewerComment
from jig.ticket import Ticket, WorkType

if TYPE_CHECKING:  # pragma: no cover — typing-only
    from jig.code_metrics import ChangeMetrics
    from jig.orchestrator import Orchestrator

_logger = logging.getLogger(__name__)

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
# Phase 1 — tradeoff-compliance: flags tickets that re-add capability work
# deferred to a later layer in the tradeoff ledger. Mechanical, no LLM.
TRADEOFF_COMPLIANCE_REVIEWER_ID = "tradeoff-compliance"
# Phase 3 — contract-test-coverage: flags consumed API/event contracts with no
# integration_ac mention on either provider or consumer side. End-of-ticket only.
CONTRACT_TEST_COVERAGE_REVIEWER_ID = "contract-test-coverage"
# Phase 5 — tracer-preservation: flags tickets that touch nodes covered by a
# tracer bullet, reminding the agent to re-run the smoke. End-of-ticket only.
TRACER_PRESERVATION_REVIEWER_ID = "tracer-preservation"

# Track G Final — specialty reviewers auto-selected by ticket
# characteristics (labels, dev_tier, module tier_hint, AC text).
# Each is an LLM-driven judgment reviewer; the role configs live in
# ``jig/defaults/roles/reviewer_<name>.yaml``.
SECURITY_REVIEWER_ID = "reviewer-security"
PERFORMANCE_REVIEWER_ID = "reviewer-performance"
ARCHITECTURAL_REVIEWER_ID = "reviewer-architectural"

# Track G MVP follow-on — judgment reviewers (LLM-driven). Role configs
# live in ``jig/defaults/roles/reviewer_<name>.yaml``.
# Default-on for all tickets regardless of layer — they review code
# quality (error paths, patterns, test adequacy) without needing v2
# artifacts like architecture.yaml or contracts.yaml.
PATTERN_CONFORMANCE_REVIEWER_ID = "reviewer-pattern-conformance"
ERROR_HANDLING_REVIEWER_ID = "reviewer-error-handling"
TEST_ADEQUACY_REVIEWER_ID = "reviewer-test-adequacy"

# Single-pass generalist that covers all five specialist axes (pattern,
# error-handling, architectural, performance, security) in one review.
# Used in the smaller workflows (feature-s, bugfix, refactor, migration,
# perf) where spawning five specialists isn't worth the cost. The
# default workflow keeps its specialist federation.
GENERALIST_REVIEWER_ID = "reviewer-generalist"

# LLM-driven reviewer ids — the federation members that are *spawned as
# agents* via the orchestrator rather than executed in-process by a
# Python reviewer class. Block 3 (Important 1): ``dispatch_for_cadence``
# returns ``LlmReviewerPending`` records for these so the federation
# stops "selecting without dispatching", and ``dispatch_with_llm_spawn``
# is the federation-execution entry point.
#
# These ids match the ``role`` field on the corresponding YAML role
# configs (``jig/defaults/roles/reviewer_*.yaml``); the orchestrator
# loads each by walking its file-id (``reviewer_security``) and uses
# the role config to spawn the agent.
_LLM_REVIEWER_IDS: frozenset[str] = frozenset(
    {
        SECURITY_REVIEWER_ID,
        PERFORMANCE_REVIEWER_ID,
        ARCHITECTURAL_REVIEWER_ID,
        PATTERN_CONFORMANCE_REVIEWER_ID,
        ERROR_HANDLING_REVIEWER_ID,
        TEST_ADEQUACY_REVIEWER_ID,
        GENERALIST_REVIEWER_ID,
    }
)

# Map reviewer id → role-config file id (the ``load_role`` lookup
# ``handle_<role>_yaml`` shape). Defined as a function so the mapping
# stays in lockstep with the role-config filenames; new judgment
# reviewers add an entry here + drop a yaml under
# ``jig/defaults/roles/``.
_REVIEWER_ID_TO_ROLE_FILE: dict[str, str] = {
    SECURITY_REVIEWER_ID: "reviewer_security",
    PERFORMANCE_REVIEWER_ID: "reviewer_performance",
    ARCHITECTURAL_REVIEWER_ID: "reviewer_architectural",
    PATTERN_CONFORMANCE_REVIEWER_ID: "reviewer_pattern_conformance",
    ERROR_HANDLING_REVIEWER_ID: "reviewer_error_handling",
    TEST_ADEQUACY_REVIEWER_ID: "reviewer_test_adequacy",
    GENERALIST_REVIEWER_ID: "reviewer_generalist",
}


def known_llm_reviewer_ids() -> frozenset[str]:
    """Public accessor for the set of LLM-driven reviewer ids the federation
    knows about. Used by ``jig.persistence.load_workflow`` to validate that
    every entry in a phase's ``reviewers:`` resolves to a real reviewer.

    Does NOT include mechanical reviewers (contract-compliance,
    cross-cutting-policy, spec-compliance, intent-compliance, etc.) — those
    are still selected implicitly by cadence logic, not declared per-phase.
    """
    return frozenset(_REVIEWER_ID_TO_ROLE_FILE)


class LlmReviewerPending(BaseModel):
    """A federation reviewer queued for orchestrator spawn (Block 3).

    The mechanical reviewers run in-process and return comments
    synchronously through ``dispatch_for_cadence``. The judgment /
    specialty reviewers (security, performance, architectural,
    pattern-conformance, error-handling, test-adequacy) are LLM-driven
    role configs — they need to be spawned as agents via the
    orchestrator's normal dispatch path.

    ``dispatch_for_cadence`` returns one of these per LLM reviewer id
    instead of a comment list. ``dispatch_with_llm_spawn`` consumes
    these records: spawns each agent, waits for completion, then reads
    the ``ReviewCommentsStore`` for the comments the agents posted via
    the ``reviewer_post_comment`` MCP tool.
    """

    model_config = ConfigDict(extra="forbid")

    reviewer_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Reviewer id (e.g. ``reviewer-security``). Matches the "
            "``role`` field of the corresponding role-config YAML."
        ),
    )
    ticket_id: str = Field(
        ...,
        min_length=1,
        description="Ticket the reviewer was selected for.",
    )
    role_config_path: str = Field(
        ...,
        min_length=1,
        description=(
            "Role-config file id used by ``load_role`` — e.g. "
            "``reviewer_security`` for ``reviewer_security.yaml``."
        ),
    )
    project_root: str = Field(
        ...,
        min_length=1,
        description=(
            "Absolute project root path (stringified) — orchestrator "
            "spawn rooting + reviewer worktree resolution use this."
        ),
    )
    cadence: Literal["per_commit", "end_of_ticket"] = Field(
        default="end_of_ticket",
        description=(
            "Cadence under which the reviewer was selected. LLM "
            "reviewers only ever fire at end_of_ticket today; the field "
            "is here so the federation-execution path can carry the "
            "scope through to spawn-time analytics."
        ),
    )


# Labels that trigger specialty-reviewer auto-selection. Sets so
# membership tests stay O(1) for the common case (a ticket usually
# has 0–3 labels).
_SECURITY_LABELS: frozenset[str] = frozenset(
    {
        "touches-auth",
        "touches-pii",
        "touches-secrets",
        "touches-payments",
    }
)
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
    TRADEOFF_COMPLIANCE_REVIEWER_ID,
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
_BONES_DEFAULTS: list[str] = [
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    TRADEOFF_COMPLIANCE_REVIEWER_ID,
]

# MVP / final default set. ``contract-compliance`` is reused from the
# bones default — every ticket benefits from the diff/AC checks, not
# just bones tickets. Intent-compliance, cross-cutting-policy, and
# spec-compliance join from MVP onward.
_MVP_FINAL_DEFAULTS: list[str] = [
    BONES_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    TRADEOFF_COMPLIANCE_REVIEWER_ID,
    CONTRACT_TEST_COVERAGE_REVIEWER_ID,
    TRACER_PRESERVATION_REVIEWER_ID,
]

# Judgment reviewers — default-on for every ticket regardless of layer.
# These are LLM-driven but require no v2 artifacts (architecture.yaml,
# contracts.yaml) — they review the diff itself for error-handling gaps,
# pattern conformance, and test adequacy.
_JUDGMENT_DEFAULTS: list[str] = [
    ERROR_HANDLING_REVIEWER_ID,
    PATTERN_CONFORMANCE_REVIEWER_ID,
    TEST_ADEQUACY_REVIEWER_ID,
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
    - ``layer`` unset and empty reviewer_set → starts empty; judgment
      reviewers (below) are still appended.

    Specialty reviewer auto-selection (Track G Final) runs on top of
    whichever branch fired above, additive only:

    - Security: labels touch auth/PII/secrets/payments OR module is
      SA-tier in architecture.yaml.
    - Performance: ``perf-budget`` label OR linked integration AC
      contains perf-budget keywords.
    - Architectural: ``touches-contract`` label OR ``dev_tier == "sa"``
      OR ``contract_amendment`` populated.

    Judgment reviewers (``_JUDGMENT_DEFAULTS``) are appended
    unconditionally for every ticket regardless of layer or
    reviewer_set. They need no v2 artifacts and provide real code
    review (error handling, pattern conformance, test adequacy) on any
    diff.

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
    if ticket.visual_references and VISUAL_COMPLIANCE_REVIEWER_ID not in selected:
        selected.append(VISUAL_COMPLIANCE_REVIEWER_ID)

    # Track D Final — accessibility + responsive reviewers ride the
    # same gating signal as visual-compliance (non-empty
    # visual_references). Mechanical, bounded cost. Both no-op when
    # wireframes are missing — visual-compliance already emits the
    # critical there.
    if ticket.visual_references and ACCESSIBILITY_REVIEWER_ID not in selected:
        selected.append(ACCESSIBILITY_REVIEWER_ID)
    if ticket.visual_references and RESPONSIVE_REVIEWER_ID not in selected:
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

    # Phase 4.10 — graph-aware auto-selection. If the ticket touches
    # an exposed_api or emitted_event that has consumers in the graph,
    # ensure the architectural reviewer fires (it might already be
    # selected by the touches-contract label or dev_tier path above;
    # this adds it when those signals are absent but the graph shows
    # integration risk). No-ops when project_root is None (no graph
    # available) or when the architectural reviewer is already selected.
    if ARCHITECTURAL_REVIEWER_ID not in selected and project_root is not None:
        if _touches_consumed_interface(ticket, project_root):
            selected.append(ARCHITECTURAL_REVIEWER_ID)

    # Judgment reviewers are default-on for code tickets — they review
    # the diff for error-handling gaps, pattern conformance, and test
    # adequacy. Skip for non-code work types (planning, brief,
    # architecture, docs) that produce no diff to review.
    _NON_CODE_TYPES = {
        WorkType.PLANNING,
        WorkType.BRIEF,
        WorkType.ARCHITECTURE,
        WorkType.DOCS,
        WorkType.CANONICALIZE,
    }
    if ticket.work_type not in _NON_CODE_TYPES:
        for reviewer_id in _JUDGMENT_DEFAULTS:
            if reviewer_id not in selected:
                selected.append(reviewer_id)

    return selected


def _needs_security_review(ticket: Ticket, project_root: Path | None) -> bool:
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


def _needs_performance_review(ticket: Ticket, project_root: Path | None) -> bool:
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


def _touches_consumed_interface(ticket: Ticket, project_root: Path) -> bool:
    """True iff the ticket touches an api or event that has consumers.

    Builds the dependency graph (cheap, seconds) and checks whether
    any directly touched node of kind ``exposed_api`` or
    ``emitted_event`` has at least one consumer. A "consumed" interface
    changed by this ticket is exactly the scenario the architectural
    reviewer was designed to catch.
    """
    from jig.graph.derive import build_graph, ticket_impact

    try:
        graph = build_graph(project_root)
        impact = ticket_impact(graph, ticket.id, depth=0)
        interface_kinds = frozenset({"exposed_api", "emitted_event"})
        for node in impact.touched:
            if node.kind not in interface_kinds:
                continue
            if graph.consumers_of(node.id):
                return True
    except Exception:
        pass
    return False


def promote_dev_tier(ticket: Ticket, project_root: Path) -> str | None:
    """Return the promoted ``dev_tier`` based on graph crossed_boundaries.

    Rules (configurable thresholds):
      crossed_boundaries == 0 → None (no change; keep existing tier)
      1–2                     → ``"senior"`` at minimum
      3+                      → ``"sa"`` at minimum

    Returns ``None`` when the ticket's existing tier is already at or
    above the threshold (no demotion), when the graph can't be loaded,
    or when the ticket has no module touches.

    Callers typically do::

        promoted = promote_dev_tier(ticket, project_root)
        if promoted and (ticket.dev_tier is None or ...):
            ticket = ticket.model_copy(update={"dev_tier": promoted})
    """
    _TIER_ORDER = {"standard": 0, "senior": 1, "sa": 2}
    _THRESHOLD_SENIOR = 1
    _THRESHOLD_SA = 3

    from jig.graph.derive import build_graph, ticket_impact

    try:
        graph = build_graph(project_root)
        impact = ticket_impact(graph, ticket.id, depth=1)
    except Exception:
        return None

    boundaries = impact.crossed_boundaries
    if boundaries >= _THRESHOLD_SA:
        target_tier = "sa"
    elif boundaries >= _THRESHOLD_SENIOR:
        target_tier = "senior"
    else:
        return None

    current = ticket.dev_tier
    if current is None:
        return target_tier
    current_rank = _TIER_ORDER.get(current, 0)
    target_rank = _TIER_ORDER[target_tier]
    return target_tier if target_rank > current_rank else None


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
) -> dict[str, list[ReviewerComment] | LlmReviewerPending]:
    """Run the reviewers selected for ``ticket`` at ``cadence``.

    Returns ``{reviewer_id: [ReviewerComment, ...] | LlmReviewerPending}``.
    Mechanical reviewers (Python classes — contract-compliance,
    cross-cutting-policy, spec-compliance, intent-compliance,
    visual-compliance, accessibility, responsive-design) execute
    in-process and emit a list of comments. LLM-driven reviewers
    (security, performance, architectural, pattern-conformance,
    error-handling, test-adequacy) are queued for orchestrator spawn
    via an ``LlmReviewerPending`` record per id — the federation-
    execution entry point ``dispatch_with_llm_spawn`` consumes those
    pendings and runs them as agents.

    The map shape (rather than a flat list) lets callers drive
    per-reviewer disposition (auto-apply, lead-reviewer dedup,
    analytics attribution) without re-parsing ``reviewer`` on every
    comment.

    Cadence semantics per design §"Two-cadence review":

    - ``per_commit`` runs **only** the mechanical reviewers
      (contract-compliance, cross-cutting-policy, spec-compliance).
      These are the deterministic checks that fit in the
      single-digit-second per-commit budget. ``ticket.reviewer_set``
      does not gate this set — per-commit composition is fixed at
      the deterministic core regardless of the planner's authored
      end-of-ticket set, because the per-commit job is "catch the
      mistake before the dev compounds it" not "run the full
      federation". LLM reviewers are NEVER queued at per-commit
      cadence — single-digit-second latency budget rules them out.
    - ``end_of_ticket`` runs the full ``select_reviewers_for_ticket``
      result — mechanical + intent-compliance for MVP+. Intent-
      compliance runs against authored artifacts, not the worktree
      diff; the synthetic operator's existing intent-reviewer
      invocation path stays in place. ``dispatch_for_cadence``
      returns intent-compliance with an empty list at end-of-ticket
      so callers can see "this reviewer is selected" without forcing
      an artifact load here. LLM-driven specialty reviewers come back
      as ``LlmReviewerPending`` records.

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
            ticket,
            project_root=project_root,
        )

    out: dict[str, list[ReviewerComment] | LlmReviewerPending] = {}

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

    if TRADEOFF_COMPLIANCE_REVIEWER_ID in reviewer_ids:
        from jig.reviewers.tradeoff_compliance import TradeoffComplianceReviewer

        comments = await TradeoffComplianceReviewer().review(ticket, project_root)
        out[TRADEOFF_COMPLIANCE_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if (
        CONTRACT_TEST_COVERAGE_REVIEWER_ID in reviewer_ids
        and cadence == "end_of_ticket"
    ):
        from jig.reviewers.contract_test_coverage import ContractTestCoverageReviewer

        comments = await ContractTestCoverageReviewer().review(ticket, project_root)
        out[CONTRACT_TEST_COVERAGE_REVIEWER_ID] = _tag_cadence(comments, cadence)

    if TRACER_PRESERVATION_REVIEWER_ID in reviewer_ids and cadence == "end_of_ticket":
        from jig.reviewers.tracer_preservation import TracerPreservationReviewer

        comments = await TracerPreservationReviewer().review(ticket, project_root)
        out[TRACER_PRESERVATION_REVIEWER_ID] = _tag_cadence(comments, cadence)

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

    # Block 3 — Important 1: queue LLM-driven reviewers as
    # ``LlmReviewerPending`` records. ``dispatch_with_llm_spawn``
    # consumes these and spawns agents via the orchestrator. Per-commit
    # cadence never queues LLM reviewers — the single-digit-second
    # latency budget rules them out, and the mechanical subset above
    # has nothing to forward.
    if cadence == "end_of_ticket":
        for reviewer_id in reviewer_ids:
            if reviewer_id not in _LLM_REVIEWER_IDS:
                continue
            role_file = _REVIEWER_ID_TO_ROLE_FILE.get(reviewer_id)
            if role_file is None:
                # Defensive: an LLM id without a role-config map entry
                # is a bug — surface as a programming error rather than
                # silently skipping the spawn.
                raise RuntimeError(
                    f"reviewer id {reviewer_id!r} is in _LLM_REVIEWER_IDS "
                    f"but has no entry in _REVIEWER_ID_TO_ROLE_FILE"
                )
            out[reviewer_id] = LlmReviewerPending(
                reviewer_id=reviewer_id,
                ticket_id=ticket.id,
                role_config_path=role_file,
                project_root=str(project_root),
                cadence=cadence,
            )

    return out


async def dispatch_with_llm_spawn(
    ticket: Ticket,
    project_root: Path,
    orchestrator: "Orchestrator",
    *,
    worktree_path: Path | None = None,
    base_ref: str = "main",
    reviewers: list[str] | None = None,
    cycle: int = 0,
    phase_name: str | None = None,
) -> dict[str, list[ReviewerComment]]:
    """Federation-execution entry point (Block 3, Important 1).

    Pre-Block-3, ``dispatch_for_cadence`` *selected* the LLM-driven
    specialty + judgment reviewers but had no execution branches for
    them — the federation could report "reviewer-security is in the
    set" without ever spawning the agent. This function closes that
    gap: it runs the in-process mechanical reviewers AND spawns each
    LLM reviewer via the orchestrator, waits for the spawned agents to
    finish, then reads the comments they posted via
    ``reviewer_post_comment`` back from the ``ReviewCommentsStore``.

    ``orchestrator`` is the singleton orchestrator instance from
    ``jig.orchestrator``. The function uses its
    ``spawn_review_agent_for_id`` helper (added by Block 3) which loads
    the role config, builds an ``AgentSpawnContext``, and runs the
    agent through the same ``_run_agent_with_analytics`` path as a
    regular phase. Tests inject a mock orchestrator to exercise the
    spawn-and-wait flow without burning LLM tokens — the real spawn
    path is operator-driven.

    Cadence is implicitly ``end_of_ticket`` — the LLM-spawn path is
    end-of-ticket only by design (the single-digit-second per-commit
    budget rules out judgment reviewers). Per-commit callers use
    ``dispatch_for_cadence`` directly.

    ``reviewers`` is the review-routing per-phase reviewer list
    (feature-work/review-routing/plan.md §Step 5):

    - ``None`` (default): legacy behaviour — cadence selection picks
      the LLM reviewers. Used by the post-RESOLVE federation gate
      until step 6 populates the default workflow's review phase.
    - ``[]``: explicit "no LLM reviewers" — mechanical reviewers still
      run via cadence; useful for non-review phases that want
      mechanical-only behaviour.
    - ``[id, ...]``: dispatch exactly the listed LLM reviewers. Names
      not in ``known_llm_reviewer_ids()`` raise ``ValueError`` for
      defense-in-depth (workflow YAML validation already catches typos
      at load time, but in-process callers can still pass bad lists).
      Cadence selection is bypassed entirely — pendings are created
      directly from this list, so reviewers like ``reviewer-generalist``
      that cadence never selects are still spawned when the phase
      declares them explicitly.

    Returns ``{reviewer_id: [ReviewerComment, ...]}`` — the same shape
    bones-era callers expect, with mechanical results and LLM-spawned
    results merged. Pending records that produce no agent output (the
    common case for a clean ticket where the security reviewer has
    nothing to flag) come back as empty lists, mirroring the
    mechanical reviewers' "selected but found nothing" semantics.
    """
    # Lazy import to avoid a circular dep — review_comments.py imports
    # ``ReviewerComment``; pulling the store in at module load time
    # means dispatch.py can't be imported during reviewer construction.
    from jig.store.review_comments import ReviewCommentsStore

    if reviewers is not None:
        known = known_llm_reviewer_ids()
        unknown = [r for r in reviewers if r not in known]
        if unknown:
            raise ValueError(
                f"dispatch_with_llm_spawn: unknown LLM reviewer id(s) "
                f"{unknown!r}. Known: {sorted(known)!r}."
            )

    by_reviewer = await dispatch_for_cadence(
        ticket,
        project_root,
        "end_of_ticket",
        worktree_path=worktree_path,
        base_ref=base_ref,
    )

    out: dict[str, list[ReviewerComment]] = {}
    pendings: list[LlmReviewerPending] = []

    # Split mechanical results from LLM pendings. Mechanical results
    # pass straight through; pendings get spawned below.
    #
    # fix-loop-context: stamp ``cycle`` onto mechanical comments so
    # reraised detection and audit history align with the current
    # fix-loop iteration. The in-process reviewers don't know about
    # the cycle context — we override here from the dispatch-time
    # value the orchestrator threaded in.
    for reviewer_id, value in by_reviewer.items():
        if isinstance(value, LlmReviewerPending):
            pendings.append(value)
        else:
            out[reviewer_id] = [c.model_copy(update={"cycle": cycle}) for c in value]

    # When the phase declares an explicit reviewer list, bypass cadence
    # selection entirely and create pendings directly from that list.
    # None = cadence selection wins (legacy path for the post-RESOLVE gate).
    if reviewers is not None:
        pendings = [
            LlmReviewerPending(
                reviewer_id=r,
                ticket_id=ticket.id,
                role_config_path=_REVIEWER_ID_TO_ROLE_FILE[r],
                project_root=str(project_root),
            )
            for r in dict.fromkeys(reviewers)  # stable dedup
        ]

    # Objective code-quality signal for the change under review — computed
    # once and handed to every LLM reviewer's prompt. Signal only; a failure
    # degrades to None (no metrics section) without blocking the federation.
    # Computed *before* the no-pendings early return so unrouted-hit warnings
    # fire on every dispatch path (incl. ``reviewers=[]``), not only when at
    # least one LLM reviewer happens to be queued.
    code_metrics = None
    if worktree_path is not None:
        from jig.code_metrics import compute_change_metrics

        code_metrics = await compute_change_metrics(worktree_path, base_ref=base_ref)

    # "Never silent drop" (design §4): if a deterministic taxonomy hit's
    # owning_reviewer isn't in the spawned set, log a warning rather than let
    # the hit silently disappear from the review surface. Measurement-side
    # capture of these hits lives in sub-issue D (the AuditStore snapshot).
    spawned_reviewer_ids = {p.reviewer_id for p in pendings}
    if code_metrics is not None and code_metrics.taxonomy_hits:
        for hit in code_metrics.taxonomy_hits:
            if hit.reviewer not in spawned_reviewer_ids:
                _logger.warning(
                    "taxonomy hit [%s] %s:%d owned by %r had no selected reviewer "
                    "(spawned: %s) — no prompt-side surface this run",
                    hit.id,
                    hit.file,
                    hit.line,
                    hit.reviewer,
                    sorted(spawned_reviewer_ids),
                )

    # Sub-issue D — quality measurement + attribution: persist a per-cycle
    # ``QualitySnapshot`` over the federation's objective metrics. Fires
    # before the no-pendings early-return so a ``reviewers=[]`` dispatch
    # still contributes a row. Signal only — a recording failure is logged
    # and dropped, never escalated, so it can't block the federation.
    if code_metrics is not None:
        await _record_quality_snapshot(
            ticket=ticket,
            project_root=project_root,
            cycle=cycle,
            code_metrics=code_metrics,
            pendings=pendings,
            phase_name=phase_name,
        )

    # No LLM reviewers to spawn — the warning above already surfaced any
    # unrouted hits; nothing else to do.
    if not pendings:
        return out

    # Spawn each LLM reviewer through the orchestrator. The orchestrator's
    # spawn helper handles role-config loading, worktree rooting, MCP setup,
    # and analytics. We wait for each spawn to complete sequentially —
    # judgment reviewers are end-of-ticket only and the federation is small
    # (≤6 reviewers), so the parallelism win isn't worth the increased
    # operator-cost surface.
    store_path = project_root / ".jig" / "store" / "review_comments.jsonl"
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store = ReviewCommentsStore(store_path)
    await store.load()

    # Snapshot the pre-spawn comments so we can attribute newly-posted comments
    # to each reviewer run cleanly. Without the snapshot we'd double-count a
    # reviewer that ran in an earlier cycle on this same ticket.
    pre_existing_ids: set[str] = set()
    pre_comments = await store.for_ticket(ticket.id)
    for c in pre_comments:
        # The store assigns ids on insert; ReviewerComment itself has no id
        # field, so the only way to "remember" a row is by an in-memory tuple
        # of distinguishing fields. We snapshot ``(reviewer, type, prose)``
        # as the unique key — judgment reviewers don't post identical
        # comments twice within the same cycle by design.
        pre_existing_ids.add(_comment_signature(c))

    await asyncio.gather(
        *[
            orchestrator.spawn_review_agent_for_id(
                reviewer_id=p.reviewer_id,
                ticket=ticket,
                # Use the canonical hyphenated id — matches the snapshot
                # attribution side and lets ``load_role`` resolve project
                # overrides at ``.jig/roles/<hyphenated>.yaml`` (the path
                # ``save_role`` writes to). The underscored filename stem
                # in ``role_config_path`` hits the shipped default first
                # and silently bypasses project overrides.
                role_file=p.reviewer_id,
                project_root=project_root,
                worktree_path=worktree_path,
                cycle=cycle,
                code_metrics=code_metrics,
            )
            for p in pendings
        ]
    )
    await store.load()

    # Read every comment the spawned agents posted and attribute each to
    # the reviewer id that produced it. ``ReviewerComment.reviewer`` is
    # required, so the attribution is straightforward.
    final_comments = await store.for_ticket(ticket.id)
    for c in final_comments:
        if _comment_signature(c) in pre_existing_ids:
            continue
        if c.reviewer not in {p.reviewer_id for p in pendings}:
            continue
        out.setdefault(c.reviewer, []).append(c)

    # Ensure every pending reviewer has an entry in the result map so
    # callers see "this reviewer was selected and ran" even on an empty
    # finding list. Mirrors the mechanical reviewer semantics where
    # contract-compliance always shows up in ``out`` once selected.
    for pending in pendings:
        out.setdefault(pending.reviewer_id, [])

    return out


def _comment_signature(comment: ReviewerComment) -> str:
    """Stable per-comment signature for de-dup within one spawn round.

    Uses ``(reviewer, type, prose, cycle)`` as the discriminator. The
    ``cycle`` field is load-bearing: a reviewer that re-flags the same
    finding with identical prose across cycles must NOT be treated as
    a duplicate of the prior cycle's row — that would hide the re-flag
    from the orchestrator and prevent the reraised ack from being
    written. Within a single cycle, identical-prose retries are still
    de-duped (judgment reviewers don't post the same comment twice
    within one spawn).
    """
    return f"{comment.reviewer}::{comment.type}::{comment.prose}::{comment.cycle}"


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


async def _record_quality_snapshot(
    *,
    ticket: Ticket,
    project_root: Path,
    cycle: int,
    code_metrics: ChangeMetrics,
    pendings: list[LlmReviewerPending],
    phase_name: str | None = None,
) -> None:
    """Persist one ``QualitySnapshot`` row for this cycle. Signal-only:
    any failure is logged and dropped (never raised) so a measurement
    glitch can't block the federation."""

    from collections import Counter
    from hashlib import sha256

    from jig.persistence import resolve_role_path
    from jig.store.quality import QualitySnapshot, QualitySnapshotStore

    try:
        spawned_ids = tuple(sorted(p.reviewer_id for p in pendings))

        # Hash each spawned reviewer's role yaml (project override wins
        # over shipped default, matching ``load_role`` resolution). Missing
        # files contribute an empty string — never raise.
        role_versions: dict[str, str] = {}
        for p in pendings:
            # Use the hyphenated canonical id, not the underscored filename
            # stem. ``save_role`` writes project overrides to
            # ``.jig/roles/<hyphenated-id>.yaml`` (filename == ``config.role``),
            # while shipped files use underscored stems. Passing the
            # filename stem here misses every project override and silently
            # records the shipped-default hash — defeating cell attribution.
            # The hyphenated id hits project overrides directly and falls
            # back to the role-field walk for shipped defaults.
            role_path = resolve_role_path(project_root, p.reviewer_id)
            if role_path is not None:
                # Disk I/O off the event loop — small files, but the
                # codebase convention is "async by default for I/O".
                role_bytes = await asyncio.to_thread(role_path.read_bytes)
                role_versions[p.reviewer_id] = sha256(role_bytes).hexdigest()[:12]
            else:
                role_versions[p.reviewer_id] = ""

        counts = Counter(h.category for h in code_metrics.taxonomy_hits)
        work_type_val = getattr(ticket.work_type, "value", str(ticket.work_type))
        snap = QualitySnapshot(
            ticket_id=ticket.id,
            run_id=f"{ticket.id}.cycle{cycle}",
            phase=phase_name or "",
            max_cc=code_metrics.max_cc,
            ruff_findings=code_metrics.ruff_findings,
            loc_delta=code_metrics.loc_delta,
            taxonomy_hit_counts=dict(counts),
            cell={
                "workflow_name": getattr(ticket, "workflow", "") or "",
                "layer": getattr(ticket, "layer", "") or "",
                "work_type": work_type_val,
                "phase": phase_name or "",
            },
            spawned_reviewers=spawned_ids,
            role_versions=role_versions,
        )

        snap_path = project_root / ".jig" / "store" / "quality_snapshots.jsonl"
        snap_path.parent.mkdir(parents=True, exist_ok=True)
        snap_store = QualitySnapshotStore(snap_path)
        await snap_store.load()
        # Idempotency: dedupe on ``(run_id, phase, spawned_reviewers)``.
        # ``run_id`` alone (just ``ticket.id`` + ``cycle``) would silence
        # legitimate snapshots from a *different* phase at the same cycle.
        # Adding ``phase`` lets two same-cycle phases with the SAME
        # reviewer set coexist (a workflow with two ``review`` phases at
        # ``cycle=0``); adding ``spawned_reviewers`` keeps the dedupe
        # working even when ``phase_name`` isn't threaded through (older
        # call sites pass ``None``, defaulting to empty). A retry of the
        # *same* phase spawns the *same* set, so this key dedupes retries
        # without dropping per-phase metrics.
        existing = await snap_store.for_run(snap.run_id)
        if any(
            e.phase == snap.phase and e.spawned_reviewers == snap.spawned_reviewers
            for e in existing
        ):
            return
        await snap_store.append(snap)
    except Exception:  # noqa: BLE001 — signal-only contract
        _logger.warning(
            "quality snapshot recording failed for ticket %s; continuing",
            ticket.id,
            exc_info=True,
        )


__all__ = [
    "ACCESSIBILITY_REVIEWER_ID",
    "ARCHITECTURAL_REVIEWER_ID",
    "BONES_REVIEWER_ID",
    "CONTRACT_TEST_COVERAGE_REVIEWER_ID",
    "CROSS_CUTTING_REVIEWER_ID",
    "GENERALIST_REVIEWER_ID",
    "promote_dev_tier",
    "ERROR_HANDLING_REVIEWER_ID",
    "INTENT_REVIEWER_ID",
    "LlmReviewerPending",
    "PATTERN_CONFORMANCE_REVIEWER_ID",
    "PERFORMANCE_REVIEWER_ID",
    "RESPONSIVE_REVIEWER_ID",
    "SECURITY_REVIEWER_ID",
    "SPEC_COMPLIANCE_REVIEWER_ID",
    "TEST_ADEQUACY_REVIEWER_ID",
    "TRACER_PRESERVATION_REVIEWER_ID",
    "VISUAL_COMPLIANCE_REVIEWER_ID",
    "dispatch_for_cadence",
    "dispatch_with_llm_spawn",
    "known_llm_reviewer_ids",
    "select_reviewers_for_ticket",
    "should_run_for_bones",
]
