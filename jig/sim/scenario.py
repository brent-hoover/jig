"""Scenario YAML schema for the synthetic operator (Track H1, bones).

A scenario is a fully-scripted walk through the v2 lifecycle. Each step
names a ``StepKind`` (the v2-helper to invoke) plus its params; the
driver dispatches on ``kind`` and runs the step. Per-step assertions
fire after each step; ``final_assertions`` fire after every step
completes.

Bones-subset of the design.md scenario format: scripted turns only (no
policy-driven), no ``coverage_tags`` (Track H9), no ``retry_count``
(Track H11), no ``tier`` (CI tiering is Track H11). The full scenario
schema is a superset and the bones loader rejects extra fields so
forward additions land via explicit schema bumps.
"""
from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jig.sim.assertions import ScenarioAssertionUnion

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
    # ``docs/implementation/v2-plan.md`` Bones scope: L1/L2 + SA
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

    # Dev — mocked in mock mode (writes a small commit satisfying the
    # bones reviewer's checks); spawns a real agent in real mode.
    MOCK_DEV_COMMIT = "mock_dev_commit"

    # Reviewer — invoke ContractComplianceReviewer.review.
    RUN_REVIEWER = "run_reviewer"


class ScenarioStep(BaseModel):
    """One step in a bones scenario.

    ``params`` is loosely typed (``dict[str, Any]``) because each step
    kind takes a different shape; the driver does its own validation
    when dispatching. Bones uses YAML for authoring so structured
    per-kind models would force more hops than they're worth.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=True)

    kind: StepKind
    params: dict[str, Any] = Field(default_factory=dict)
    assertions: list[ScenarioAssertionUnion] = Field(default_factory=list)


class Scenario(BaseModel):
    """A bones-scope scenario.

    The bones loader rejects extra fields so forward design-doc
    additions (``coverage_tags``, ``tier``, ``retry_count``,
    ``policy``) require an explicit schema change rather than silently
    landing via untyped YAML.
    """

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    persona: str = Field(..., min_length=1)
    estimated_cost_usd_max: float = Field(..., ge=0.0)
    steps: list[ScenarioStep] = Field(default_factory=list)
    final_assertions: list[ScenarioAssertionUnion] = Field(default_factory=list)


def load_scenario(path: Path) -> Scenario:
    """Load and validate a scenario YAML.

    Raises ``pydantic.ValidationError`` on schema mismatch (unknown
    persona id is *not* validated here; the driver catches that at
    persona-load time so the failure points at the missing persona
    file rather than the scenario).
    """
    data = yaml.safe_load(path.read_text()) or {}
    return Scenario.model_validate(data)
