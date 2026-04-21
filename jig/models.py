"""Domain models for Jig."""

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class MergeStrategy(str, Enum):
    DIRECT = "direct"
    SQUASH = "squash"
    PR = "pr"
    FEATURE_BRANCH = "feature_branch"


class RoleConfig(BaseModel):
    role: str
    phase_prompt: str
    response_prompt: str = ""
    allowed_tools: list[str] = []
    # NOTE: `can_message` was a stub with no enforcement and is removed
    # in Phase 1E. Cross-role messaging policy moves to capability policy
    # on role templates in Phase 5 (doc 16).
    #
    # Context references per doc 07. ``default_context`` is optional —
    # failure to resolve logs a warning and the agent proceeds.
    # ``required_context`` is mandatory — any URI that fails to resolve
    # at spawn causes spawn failure. Validation at load (Phase 2F)
    # additionally checks that ``project://`` and ``role://`` URIs in
    # ``required_context`` point at extant files.
    default_context: list[str] = []
    required_context: list[str] = []
    allowed_mcps: list[str] = []


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
    evaluators: list[
        "PreviousPhaseRoleEvaluator | SpecificRoleEvaluator "
        "| AutomatedOnlyEvaluator | SpecificHumanEvaluator"
    ]


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


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]
