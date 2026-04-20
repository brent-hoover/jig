"""Domain models for Jig."""

from enum import Enum

from pydantic import BaseModel


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


class PhaseConfig(BaseModel):
    name: str
    role: str
    task_template: str = ""
    acceptance_criteria: str = ""
    # Check names that must pass for this phase to advance. Validated
    # at load (Phase 2F) against the project's check catalog; executed
    # in Phase 5. Empty for now in shipped defaults.
    automated_checks: list[str] = []
    # Phase 4F: role that accepts/rejects the Handoff entry closing
    # this phase. Empty means "no explicit evaluator" — the handoff
    # handlers then fall back to the next phase's role, then to
    # warn-but-allow. Full capability-policy enforcement lands
    # Phase 5 (doc 16).
    evaluator: str = ""


class WorkflowConfig(BaseModel):
    name: str
    phases: list[PhaseConfig]
