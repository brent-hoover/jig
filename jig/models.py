"""Domain models for Jig."""

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

from jig.capabilities import CapabilityDeclaration


class MergeStrategy(str, Enum):
    DIRECT = "direct"
    SQUASH = "squash"
    PR = "pr"
    FEATURE_BRANCH = "feature_branch"


class RoleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    phase_prompt: str = ""
    response_prompt: str = ""
    allowed_tools: list[str] = []
    # Context references per doc 07. ``default_context`` is optional —
    # failure to resolve logs a warning and the agent proceeds.
    # ``required_context`` is mandatory — any URI that fails to resolve
    # at spawn causes spawn failure. Validation at load (Phase 2F)
    # additionally checks that ``project://`` and ``role://`` URIs in
    # ``required_context`` point at extant files.
    default_context: list[str] = []
    required_context: list[str] = []
    allowed_mcps: list[str] = []
    # Strict MCP-tool registration mode (Phase init): when True, the
    # MCP factory in ``jig/mcp_server.py`` only registers tools whose
    # short name appears in ``allowed_tools``. The default (False)
    # preserves legacy behavior where a base set (create_ticket,
    # commit_progress, thread_*, etc.) is registered for every role
    # regardless of allowed_tools — operational roles (dev/test/pm)
    # rely on that. Strict mode is enabled on the narrow init roles
    # (po, sa, spec-generator) so e.g. PO can't grab commit_progress
    # via ToolSearch and waste turns hunting for a git repo.
    strict_tools: bool = False
    # Phase 5 Task F (doc 16 §Capability policy). Tool + path + tool-param
    # constraints for agents running this role. ``None`` means "no
    # capability policy declared" — the compiler emits empty rules and
    # hook enforcement is effectively permissive. Phase overrides on
    # ``PhaseConfig.capability_overrides`` layer on top.
    capabilities: CapabilityDeclaration | None = None
    # Phase 6 (security review): explicit opt-in for the
    # ``add_dependency`` MCP tool. The handler runs the project's
    # package manager (uv/npm/...) from the orchestrator process
    # — outside the bwrap sandbox — so install/postinstall code
    # can execute with the orchestrator environment. Roles must
    # opt in deliberately rather than getting it implicitly because
    # the project sets ``package_manager``.
    allow_add_dependency: bool = False
    # Phase 6 (security review): roles default to single-ticket scope —
    # MCP tools that take a ``ticket_id`` arg must target the spawn's
    # own ticket. Coordinator-style roles (orchestrator, planner_pm)
    # set this True to read/update tickets across the project. See
    # SEC-I1 in v2-review-findings-security.md.
    cross_ticket_access: bool = False
    # Reviewer file-scoping (feature-work/reviewer-scoping). When
    # ``reads_glob`` is non-empty the role operates in "scoped" mode:
    # the orchestrator-side MCP tools ``reviewer_get_diff`` and
    # ``reviewer_read_file`` restrict diff and file content to paths
    # matching one of these globs AND not matching any
    # ``reads_exclude`` entry. Both fields use POSIX-style globs
    # (``src/**``, ``pyproject.toml``, ``**/test_*.py``) matched via
    # ``pathlib.PurePosixPath.match`` against project-relative paths.
    # Empty (the default) means no scoping — the role uses its
    # ``allowed_tools`` raw without per-path enforcement.
    reads_glob: list[str] = []
    reads_exclude: list[str] = []


# ---- Evaluator assignment (doc 10, Phase 5 Task C) --------------------------
#
# Evaluator assignment is a phase property per doc 10 §Evaluators. Five
# discriminated-union variants cover the spectrum from "fully automated
# acceptance" through "multi-party review." The resolver in
# ``jig/evaluator_resolver.py`` maps a spec + ticket context to a
# concrete actor identity or set of identities; the handoff guard in
# ``jig/thread_mcp.py`` enforces both (a) sender-membership in that
# set and (b) the structural rule that the completing actor cannot
# also be the evaluator.


class PreviousPhaseRoleEvaluator(BaseModel):
    """The actor who filled ``role`` in the ticket's most recent prior
    Handoff for that role. Useful for "the reviewer from code-review
    evaluates the subsequent fix" — the same human or agent keeps
    context."""

    type: Literal["previous_phase_role"]
    role: str


class SpecificRoleEvaluator(BaseModel):
    """Any actor bearing ``role`` evaluates. The natural-sequence
    default: next phase's role evaluates the prior handoff. Projects
    can also name any role explicitly (e.g., ``sa`` reviews a ``dev``
    phase)."""

    type: Literal["specific_role"]
    role: str


class AutomatedOnlyEvaluator(BaseModel):
    """Phase acceptance is fully determined by automated checks — no
    human judgment required. ``create-pr`` or ``run-integration-tests``
    style phases. The handoff is auto-accepted when all required
    checks pass (Task D wires the check-gating)."""

    type: Literal["automated_only"]


class SpecificHumanEvaluator(BaseModel):
    """Named human (by identity string) must evaluate. Approval-gate
    pattern — e.g., a named reviewer signs off on compliance or
    release-go phases."""

    type: Literal["specific_human"]
    user: str


class MultiEvaluator(BaseModel):
    """Multiple evaluators required. All must accept; any can reject
    (doc 10 §Evaluators). Nested specs are resolved independently and
    merged; ``multi`` cannot nest ``multi`` to keep the resolver
    non-recursive in practice."""

    type: Literal["multi"]
    # Require at least two nested evaluators — a one-element ``multi``
    # is indistinguishable from the underlying spec and usually means
    # the config was over-specified by mistake. Fail loud at load time
    # rather than silently degrading to single-evaluator semantics.
    evaluators: list[
        "PreviousPhaseRoleEvaluator | SpecificRoleEvaluator "
        "| AutomatedOnlyEvaluator | SpecificHumanEvaluator"
    ] = Field(min_length=2)


EvaluatorSpec = Annotated[
    Union[
        PreviousPhaseRoleEvaluator,
        SpecificRoleEvaluator,
        AutomatedOnlyEvaluator,
        SpecificHumanEvaluator,
        MultiEvaluator,
    ],
    Field(discriminator="type"),
]


class PhaseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str
    task_template: str = ""
    acceptance_criteria: str = ""
    # Check names that must pass for this phase to advance. Validated
    # at load (Phase 2F) against the project's check catalog; executed
    # in Phase 5. Empty for now in shipped defaults.
    automated_checks: list[str] = []
    # Phase 5 Task C: role / identity that accepts/rejects the Handoff
    # closing this phase, as one of the five doc-10 assignment types.
    # ``None`` means "no explicit evaluator" — the handoff handlers
    # fall back to the next phase's role (natural-sequence default).
    # The structural rule ``evaluator ≠ completing actor`` is enforced
    # by the handoff guard regardless of spec type.
    evaluator: EvaluatorSpec | None = None
    # Phase 4H: role allow-lists consulted by the thread MCP tools.
    # ``questions_to`` restricts where ``thread_ask`` may route a
    # blocking Question; ``escalation_targets`` restricts where
    # ``thread_escalate`` may point. Empty means "no phase-level
    # restriction" — thread tools fall back to their current warn-only
    # behavior. Phase 5 flips these to hard enforcement per doc 16.
    questions_to: list[str] = []
    escalation_targets: list[str] = []
    # Phase 5 Task F (doc 16 §Capability policy). Phase-level overrides
    # that union with the role template's base ``capabilities`` at
    # compile time. Merge rule: list fields union; denies stack. Phases
    # that need to narrow tool access add ``denied:`` entries rather
    # than removing allows, since the compiler doesn't support
    # removal-by-omission.
    capability_overrides: CapabilityDeclaration | None = None

    # Review-routing (feature-work/review-routing/plan.md §Step 2). Repo-relative
    # globs naming the paths this phase's role writes to. The fix-loop router
    # uses these to map a reviewer-comment file → owning phase. Empty default
    # keeps backward-compat: workflows without ``writes:`` fall through to the
    # unowned-finding fallback (most-recent dev), same as today.
    writes: list[str] = []

    # Review-routing — LLM-driven reviewer ids that fire when this phase
    # runs (e.g. ``reviewer-test-adequacy``, ``reviewer-pattern-conformance``).
    # Required for phases with ``role: review`` so the review federation is
    # explicitly declared per phase (rather than implicit "all reviewers at
    # every review phase").
    #
    # Scope: only the LLM-driven judgment reviewers belong here. Mechanical
    # reviewers (contract-compliance, cross-cutting-policy, spec-compliance,
    # intent-compliance, etc.) continue to be selected by cadence logic in
    # ``jig.reviewers.dispatch`` — they don't appear in this list.
    #
    # The "required on review-role phases" + "names must resolve to known
    # LLM reviewer ids" invariants are enforced at workflow LOAD time
    # (``load_workflow``), not at model construction, so PhaseConfig stays
    # permissive for tests and ad-hoc construction.
    reviewers: list[str] = []


class WorkflowConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    phases: list[PhaseConfig]
