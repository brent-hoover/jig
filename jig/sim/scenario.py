"""Scenario YAML schema for the synthetic operator (Track H1 + H MVP).

A scenario is a fully-scripted walk through the v2 lifecycle. Each step
names a ``StepKind`` (the v2-helper to invoke) plus its params; the
driver dispatches on ``kind`` and runs the step. Per-step assertions
fire after each step; ``final_assertions`` fire after every step
completes.

Track H MVP added ``coverage_tags`` (validated against the canonical
taxonomy in ``jig.sim.coverage``) + ``tier`` for CI integration.
Policy-driven turns + ``retry_count`` are Final.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jig.sim.assertions import ScenarioAssertionUnion
from jig.sim.coverage import CANONICAL_TAGS

__all__ = [
    "Scenario",
    "ScenarioStep",
    "StepKind",
    "load_scenario",
]


class StepKind(str, Enum):
    """Step kinds the bones driver knows how to dispatch.

    Each value maps to one branch in ``driver._dispatch_step``. Adding
    a new kind requires (1) the enum entry, (2) a dispatch branch,
    (3) a test in test_sim_driver covering the step.
    """

    # PO bones — invoke the v2 PO helpers.
    INVOKE_L0_FINALIZE = "invoke_l0_finalize"
    INVOKE_L3_FINALIZE = "invoke_l3_finalize"

    # MVP-tier L1 Discovery PO — invoke the discovery_finalize handler
    # with a complete payload. Bones scenarios skip L1 (operator
    # hand-writes suites.yaml directly); MVP-tier scenarios swap in
    # this step to exercise the L1 PO authoring path.
    INVOKE_L1_FINALIZE = "invoke_l1_finalize"

    # MVP-tier L2 Suite Organizer — invoke l2_finalize with a complete
    # SuitesIndex payload. Bones scenarios use ``write_suites_yaml``
    # (operator hand-write); MVP-tier scenarios swap in this step to
    # exercise the L2 PO authoring path.
    INVOKE_L2_FINALIZE = "invoke_l2_finalize"

    # Manual writes for stages bones doesn't run via agents (per
    # ``docs/v2.0/implementation/v2-plan.md`` Bones scope: L1/L2 + SA
    # artifacts hand-written by the operator).
    WRITE_SUITES_YAML = "write_suites_yaml"
    WRITE_ARCHITECTURE = "write_architecture"
    WRITE_MODULE_CONTRACTS = "write_module_contracts"
    WRITE_BUILD_PLAN = "write_build_plan"

    # MVP-tier SA: drive a sequence of incremental upsert calls plus a
    # final ``arch_finalize`` against ``jig.sa_incremental_mcp``. Bones
    # scenarios use ``write_architecture`` + ``write_module_contracts``
    # (operator hand-write); MVP scenarios swap in this step to exercise
    # the SA discovery-loop authoring path without a real LLM. The
    # scripted-call sequence lives in scenario YAML so the test author
    # controls the exact ordering an operator would hand-walk through.
    INVOKE_SA_INCREMENTAL = "invoke_sa_incremental"

    # MVP-tier SA risk register + spike workflow (Track C MVP follow-on).
    # Author one risk via ``arch_set_risk``, propose a spike via
    # ``arch_propose_spike``, complete the spike via ``arch_complete_spike``
    # with a configurable outcome (mitigated / accepted /
    # confirmed_impossible). The ``confirmed_impossible`` branch
    # exercises the cascade-proposal write + Handoff post — see the
    # bones-with-cascade scenario.
    INVOKE_RISK_AND_SPIKE = "invoke_risk_and_spike"

    # MVP-tier Planner: invoke the v2 plan_finalize handler. Bones
    # scenarios use ``write_build_plan`` (operator hand-write); MVP
    # scenarios swap in this step to exercise the agent path.
    INVOKE_PLAN_FINALIZE = "invoke_plan_finalize"

    # PM bones — Coordinator dispatch (no Planner agent yet).
    MATERIALIZE_TICKETS = "materialize_tickets"

    # PM MVP — one cycle of the cycle-aware Coordinator. Refreshes
    # layer statuses from the live ticket store, then materializes the
    # next ready layer per the plan's ``OrderingRule``. Bones scenarios
    # use ``materialize_tickets`` (one-shot bones layer); MVP scenarios
    # exercise multi-layer dispatch with this step.
    INVOKE_COORDINATOR_CYCLE = "invoke_coordinator_cycle"

    # PM MVP — Coordinator DEFERRED queue. The operator (synthetic or
    # real) defers a ticket via ``coord.defer_ticket(...)``; the row
    # lands in ``.jig/plan/deferred-queue.jsonl`` and the ticket gets
    # ``deferred_at`` stamped. Triage is operator-only at MVP (mechanical
    # heuristic in ``coord.triage_deferred``); this step exposes the
    # defer half so a scenario can exercise the queue path end-to-end.
    DEFER_TICKET = "defer_ticket"
    TRIAGE_DEFERRED = "triage_deferred"

    # Dev — mocked in mock mode (writes a small commit satisfying the
    # bones reviewer's checks); spawns a real agent in real mode.
    MOCK_DEV_COMMIT = "mock_dev_commit"

    # Reviewer — invoke ContractComplianceReviewer.review.
    RUN_REVIEWER = "run_reviewer"

    # Track E MVP — exercise the dev-env provisioning path without a real
    # Postgres. The handler injects an in-memory SQL recorder, calls
    # ``provision_for_agent`` with the configured ticket id, and verifies
    # CREATE SCHEMA + (optionally) DROP SCHEMA fired. Scenario YAML
    # carries the ticket id + an optional ``cleanup`` flag (default
    # True) so the assertion suite can gate on the recorder's calls.
    INVOKE_DEV_PROVISIONING = "invoke_dev_provisioning"

    # Track E Final — exercise the per_agent_ephemeral SQLite path. The
    # handler builds a one-service manifest with strategy=
    # per_agent_ephemeral + kind=sqlite, runs ``provision_for_agent``
    # rooted at the project tmp dir, verifies the URL came back +
    # the file landed under .jig/dev/ephemeral/, then runs
    # ``cleanup_for_agent`` and verifies the file is gone (drop policy).
    # Scenario YAML carries the ticket id + service id + namespace
    # template; the handler stamps the URL on the driver context so a
    # follow-up assertion can introspect.
    INVOKE_DEV_EPHEMERAL = "invoke_dev_ephemeral"

    # Track E Final — exercise the vcr-style fixture replay path. The
    # handler pre-populates a cassette via FixtureStore.record, then
    # builds a FixtureMiddleware in REPLAY_ONLY mode and calls
    # ``record_or_replay`` with the matching method/url/body — the
    # assertion suite gates on the response matching what was recorded
    # (no real-API call ever fires). Scenario YAML carries the
    # service_id + recorded request/response pair.
    INVOKE_FIXTURE_REPLAY = "invoke_fixture_replay"

    # Track D MVP — exercise the VD vd_finalize path. The handler
    # synthesizes a complete VD payload (FrontendSpec + 1-2
    # wireframes) and calls handle_vd_finalize directly. Scenario YAML
    # carries the frontend dict + wireframes list so a UI-flavored
    # scenario can land the VD artifacts before the planner / dev /
    # visual_compliance reviewer steps fire.
    INVOKE_VD_FINALIZE = "invoke_vd_finalize"

    # Track I Final — exercise the quartermaster feedback loop.
    # Calls record_feedback against the project's quartermaster
    # store with operator-supplied (briefing_id, useful, noisy
    # patterns) values; lets a scenario assert that the calibration
    # actually shifted by reading it back via get_pattern_calibration.
    # Mock-mode safe (no LLM); real-mode runs are out of scope.
    INVOKE_QUARTERMASTER_FEEDBACK = "invoke_quartermaster_feedback"

    # Track G Final — verify dispatch selected the requested specialty
    # reviewer for an in-store ticket. Mock-mode only: doesn't actually
    # spawn the LLM agent, just confirms that
    # ``select_reviewers_for_ticket`` returned the expected id given
    # the ticket's labels / dev_tier / contract_amendment / module
    # tier. The selection bookkeeping lands on the driver context so
    # an artifact_written / per-step assertion can introspect it.
    INVOKE_SPECIALTY_REVIEWER = "invoke_specialty_reviewer"

    # Block 3 (Important 1+3) — drive ``dispatch_with_llm_spawn``
    # against a mocked orchestrator. The handler stubs the spawn step
    # to inject a canned reviewer comment via a configured payload so
    # the scenario can assert the comment actually lands in the
    # ReviewCommentsStore (not just that the reviewer was selected).
    # This is the "federation actually executes" path — pins the gap
    # the v2-review flagged: ``bones-with-specialty-reviewers`` only
    # exercised selection, not execution. Mock-mode only — real-mode
    # LLM execution is operator-driven and out of scope for the
    # synthetic operator.
    INVOKE_FEDERATION_EXECUTION = "invoke_federation_execution"

    # Track G Final — apply the severity-tier disposition policy to
    # a synthesized comment list. The handler builds N comments at the
    # requested severity, runs ``apply_severity_disposition`` against
    # the live ticket store + Coordinator, and stamps the disposition
    # result on the driver context so scenario assertions can check
    # the deferred-queue / FAILED-status / Handoff side-effects.
    INVOKE_SEVERITY_DISPOSITION = "invoke_severity_disposition"

    # Track C Final — exercise the cascade-failure-mode-mitigation
    # handlers. ``cascade_id`` is resolved off the most recent
    # cascade proposal on disk so the scenario YAML doesn't have to
    # know the exact timestamp the writer stamped on the artifact.
    INVOKE_CASCADE_REJECT = "invoke_cascade_reject"
    INVOKE_CASCADE_STAGE = "invoke_cascade_stage"

    # Track C Final — exercise the cascade_risk_low Coordinator
    # override. Sets the SA-suggested flag on the named module(s)
    # then reads ``next_layer_ready`` to verify the promotion fires.
    # Mock-mode safe (no LLM).
    INVOKE_CASCADE_RISK_LOW = "invoke_cascade_risk_low"

    # Track F Final — exercise the mid-work tier promotion path. The
    # handler injects N PerCommitCheckFailed events for the named
    # ticket on the same contract URI (so the auto-escalation checker
    # reports a tripped signal), then drives ``promote_ticket_tier``
    # against the live ticket store + analytics emitter so the
    # ticket's dev_tier ratchets up by one rung. Scenario YAML carries
    # the ticket id + (optional) target tier; the handler verifies the
    # ladder direction and stamps the resolved promotion onto the
    # driver context.
    INVOKE_TIER_PROMOTION = "invoke_tier_promotion"

    # Track F Final — exercise the calibration-record path. The
    # handler synthesizes one CalibrationSample (without spinning a
    # real agent) and persists it so a scenario can verify the
    # calibration envelope shifts as samples accumulate. Scenario YAML
    # carries the ticket id + size + observed turn / cost / duration
    # values.
    INVOKE_CALIBRATION_RECORD = "invoke_calibration_record"

    # Track D Final — exercise the vision-based screenshot diff path
    # using the StubVisionProvider. Scenario YAML carries the
    # ticket_id + screen_id + a canned VisionDiffResult so the
    # reviewer's full-flow comment generation runs deterministically
    # without a real vision LLM. Scenario can also opt to omit the
    # screenshot to exercise the missing-screenshot branch.
    INVOKE_VISION_DIFF = "invoke_vision_diff"

    # Track D Final — exercise the AccessibilityReviewer end-to-end
    # against an authored wireframe. Scenario YAML names the
    # ticket_id + (optionally) a wireframe-html override so the
    # scenario can swap in a deliberately-broken HTML to assert on a
    # specific WCAG rule. Stamps the comment count + rule ids onto
    # the driver context.
    INVOKE_ACCESSIBILITY_REVIEW = "invoke_accessibility_review"

    # Track D Final — exercise the ResponsiveDesignReviewer end-to-end.
    # Same shape as the a11y handler — ticket_id + optional html
    # override so a scenario can drive each rule independently.
    INVOKE_RESPONSIVE_REVIEW = "invoke_responsive_review"

    # Track B Final — exercise the L1 discovery resume-from-state path.
    # Synthesizes a stale state YAML on disk (per the params) and
    # invokes ``handle_discovery_resume`` with the requested
    # reconcile_mode. The handler stamps the resulting divergence list +
    # actions on the driver context so scenario assertions can introspect
    # the reconciliation outcome without re-running the resume.
    INVOKE_DISCOVERY_RESUME = "invoke_discovery_resume"

    # Track B Final — exercise the project-ontology operator-edit path.
    # Subkinds: ``edit`` calls ``handle_ontology_edit_term``; ``remove``
    # calls ``handle_ontology_remove_term``; ``find_references`` runs
    # the scanner. Scenario YAML drives the term + new definition /
    # replacement; the handler stamps the result on the driver context.
    INVOKE_ONTOLOGY_EDIT = "invoke_ontology_edit"

    # Block 2 — verify the fixture-mode env var built for a ticket
    # matches the expected mode. ``params.work_type`` (spike | feature)
    # drives the Ticket synthesised for ``build_fixture_env``; the
    # handler stamps ``ctx.last_fixture_env`` so a follow-up
    # ``env_var_set`` assertion can introspect.
    INVOKE_FIXTURE_ENV = "invoke_fixture_env"

    # Block 2 — exercise operator_supplied provisioning end-to-end.
    # Builds a one-service manifest in-memory with strategy=
    # operator_supplied, runs ``provision_agent_namespace``, asserts
    # the operator-authored connection string template surfaces verbatim
    # in the env-var map, and verifies cleanup is a no-op. Stamps
    # ``ctx.last_operator_supplied_url`` for assertion introspection.
    INVOKE_OPERATOR_SUPPLIED = "invoke_operator_supplied_provisioning"


# Required-param keys per step kind. Catches the most common typos
# in YAML at load time without forcing a full per-kind model split
# (TD-2 targets the discriminated-union shape; the per-kind body is
# left to the driver until each branch's param shape stabilises).
_REQUIRED_PARAM_KEYS: dict[str, frozenset[str]] = {
    "write_module_contracts": frozenset({"module_id", "contracts"}),
    "write_architecture": frozenset({"architecture"}),
    "write_suites_yaml": frozenset({"suites_index"}),
    "write_build_plan": frozenset({"plan"}),
    "invoke_l3_finalize": frozenset({"suite_id"}),
    "defer_ticket": frozenset({"ticket_id"}),
}


class ScenarioStep(BaseModel):
    """One step in a bones scenario.

    ``params`` is a free-form dict because the 30+ step kinds each
    have different shapes; a full discriminated-union split lives in
    a follow-up. For now ``_REQUIRED_PARAM_KEYS`` catches the most
    common authoring typos at load time (TD-2).
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    kind: StepKind
    params: dict[str, Any] = Field(default_factory=dict)
    assertions: list[ScenarioAssertionUnion] = Field(default_factory=list)

    @model_validator(mode="after")
    def _enforce_known_required_params(self) -> "ScenarioStep":
        kind_value = (
            self.kind.value
            if isinstance(self.kind, StepKind)
            else str(self.kind)
        )
        required = _REQUIRED_PARAM_KEYS.get(kind_value)
        if required is None:
            return self
        missing = sorted(required - set(self.params))
        if missing:
            raise ValueError(
                f"ScenarioStep(kind={kind_value!r}) missing required "
                f"param keys: {missing}. Got: {sorted(self.params)}."
            )
        return self


class Scenario(BaseModel):
    """A bones+MVP-scope scenario.

    The loader rejects extra fields so forward design-doc additions
    (``retry_count``, ``policy``) require an explicit schema change
    rather than silently landing via untyped YAML.

    ``coverage_tags`` are validated against the canonical taxonomy in
    ``jig.sim.coverage`` so a typo in a YAML fails at load time
    rather than producing a one-off tag nobody else claims.

    ``tier`` defaults to ``smoke`` (the MVP scenarios all run in
    <1s in mock mode). Operators tag scenarios ``full`` or
    ``nightly`` as the library expands; ``jig sim run-tier`` filters
    on this field.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    persona: str = Field(..., min_length=1)
    estimated_cost_usd_max: float = Field(..., ge=0.0)
    steps: list[ScenarioStep] = Field(default_factory=list)
    final_assertions: list[ScenarioAssertionUnion] = Field(default_factory=list)
    coverage_tags: list[str] = Field(default_factory=list)
    tier: Literal["smoke", "full", "nightly"] = "smoke"
    # Track H Final — policy-driven turns. When true, the driver routes
    # any step's response generation through ``jig.sim.policy.apply_policy``
    # rather than reading literal scripted text. ``scenario_seed`` is the
    # RNG seed for reproducibility — the same (persona, seed) pair always
    # yields the same sampled responses.
    policy_driven: bool = False
    scenario_seed: int = 0

    @field_validator("coverage_tags")
    @classmethod
    def _coverage_tags_must_be_canonical(cls, v: list[str]) -> list[str]:
        """Reject tags not in ``jig.sim.coverage.CANONICAL_TAGS``.

        Catching this at load time means a typo (e.g. ``po-l9``)
        fails the scenario test rather than silently producing an
        un-aggregatable tag.
        """
        unknown = [t for t in v if t not in CANONICAL_TAGS]
        if unknown:
            raise ValueError(
                f"coverage_tags contain unknown tag(s) {unknown!r}; "
                f"add to jig.sim.coverage.CANONICAL_TAGS or fix the typo"
            )
        return v


def load_scenario(path: Path) -> Scenario:
    """Load and validate a scenario YAML.

    Raises ``pydantic.ValidationError`` on schema mismatch (unknown
    persona id is *not* validated here; the driver catches that at
    persona-load time so the failure points at the missing persona
    file rather than the scenario).
    """
    data = yaml.safe_load(path.read_text()) or {}
    return Scenario.model_validate(data)
