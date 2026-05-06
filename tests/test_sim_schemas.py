"""Pydantic schemas for the synthetic operator (Track H1+H3+H4, bones).

Bones scope per ``docs/v2.0/implementation/v2-plan.md`` Track H row:
- H1 scenario schema (subset)
- H3 methodical persona schema
- H4 the 4-5 assertion kinds the bones scenario needs

The full scenario format from ``docs/v2.0/synthetic-operator/design.md`` is a
superset (policy-driven turns, coverage_tags, realism budget, etc.). The
bones subset captures only what the bones scenario actually needs;
extension fields land alongside the workflows that need them.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from jig.sim.assertions import (
    AnalyticsEventEmittedAssertion,
    ArtifactWrittenAssertion,
    AssertionKind,
    CostUnderBudgetAssertion,
    ReviewerReturnedNoCriticalAssertion,
    ScenarioAssertion,
    TicketStatusAssertion,
)
from jig.sim.persona import Persona, load_persona, persona_path
from jig.sim.scenario import (
    ScenarioStep,
    StepKind,
    load_scenario,
)


# ---- assertion union ----------------------------------------------------


def test_artifact_written_assertion_round_trips():
    a = ArtifactWrittenAssertion(
        path="docs/brief.md",
        contains="bones",
    )
    payload = a.model_dump(mode="json")
    # Discriminator must round-trip via the union wrapper.
    restored = ScenarioAssertion.validate_python(payload)
    assert isinstance(restored, ArtifactWrittenAssertion)
    assert restored.kind == AssertionKind.ARTIFACT_WRITTEN.value
    assert restored.path == "docs/brief.md"
    assert restored.contains == "bones"


def test_analytics_event_assertion_round_trips():
    a = AnalyticsEventEmittedAssertion(
        event_kind="ticket_state_changed",
        field_constraints={"to_state": "resolved"},
    )
    restored = ScenarioAssertion.validate_python(a.model_dump(mode="json"))
    assert isinstance(restored, AnalyticsEventEmittedAssertion)
    assert restored.event_kind == "ticket_state_changed"
    assert restored.field_constraints == {"to_state": "resolved"}


def test_ticket_status_assertion_round_trips():
    a = TicketStatusAssertion(ticket_id="tb-catalog-ingest", status="resolved")
    restored = ScenarioAssertion.validate_python(a.model_dump(mode="json"))
    assert isinstance(restored, TicketStatusAssertion)
    assert restored.ticket_id == "tb-catalog-ingest"
    assert restored.status == "resolved"


def test_reviewer_returned_no_critical_round_trips():
    a = ReviewerReturnedNoCriticalAssertion(reviewer_id="contract-compliance")
    restored = ScenarioAssertion.validate_python(a.model_dump(mode="json"))
    assert isinstance(restored, ReviewerReturnedNoCriticalAssertion)
    assert restored.reviewer_id == "contract-compliance"


def test_cost_under_budget_round_trips():
    a = CostUnderBudgetAssertion(usd=1.0)
    restored = ScenarioAssertion.validate_python(a.model_dump(mode="json"))
    assert isinstance(restored, CostUnderBudgetAssertion)
    assert restored.usd == 1.0


def test_assertion_rejects_unknown_kind():
    with pytest.raises(ValidationError):
        ScenarioAssertion.validate_python(
            {"kind": "made_up_kind", "path": ".jig/x"}
        )


def test_assertion_rejects_unknown_fields():
    """``extra='forbid'`` catches typos in operator-authored YAML."""
    with pytest.raises(ValidationError):
        ScenarioAssertion.validate_python(
            {
                "kind": AssertionKind.ARTIFACT_WRITTEN.value,
                "path": ".jig/x",
                "typo_field": "boom",
            }
        )


# ---- persona ------------------------------------------------------------


def test_persona_minimum_fields():
    p = Persona(
        id="methodical",
        description="Careful operator",
        gate_confirmation_policy="confirm_when_clear",
    )
    assert p.id == "methodical"
    # Sensible bones defaults; richer scoring lands with H7+ (other personas).
    assert p.override_probability == 0.0
    assert p.response_patterns == []


def test_persona_loads_from_yaml(tmp_path: Path):
    src = tmp_path / "methodical.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "id": "methodical",
                "description": "Careful operator",
                "gate_confirmation_policy": "confirm_when_clear",
                "response_patterns": [
                    "Gives a single-sentence pitch in domain language.",
                ],
                "override_probability": 0.05,
            }
        )
    )
    p = load_persona(src)
    assert p.id == "methodical"
    assert len(p.response_patterns) == 1


def test_persona_rejects_invalid_id():
    """Persona ids are kebab-case stable identifiers; empty rejected."""
    with pytest.raises(ValidationError):
        Persona(
            id="",
            description="x",
            gate_confirmation_policy="confirm_when_clear",
        )


def test_methodical_persona_yaml_ships_with_package():
    """The bones persona must be loadable from the package — driver imports it."""
    p = load_persona(persona_path("methodical"))
    assert p.id == "methodical"
    # Bones uses the methodical persona only — assert it's the one
    # designed for the conservative happy-path scenario.
    assert "methodical" in p.description.lower() or "careful" in p.description.lower()


def test_persona_path_unknown_id_raises():
    with pytest.raises(FileNotFoundError):
        persona_path("nonexistent-persona")


def test_fast_and_shippy_persona_yaml_ships_with_package():
    """fast-and-shippy persona — high gate acceptance, low override."""
    p = load_persona(persona_path("fast-and-shippy"))
    assert p.id == "fast-and-shippy"
    assert p.gate_confirmation_policy == "confirm_eagerly"
    # Behavioral profile contract: high gate acceptance, low override,
    # short rationale.
    assert p.gate_acceptance_probability >= 0.8
    assert p.override_probability <= 0.1
    assert p.prefers_short_rationale is True


def test_ambivalent_persona_yaml_ships_with_package():
    """ambivalent persona — high clarification need, vague answers."""
    p = load_persona(persona_path("ambivalent"))
    assert p.id == "ambivalent"
    assert p.gate_confirmation_policy == "confirm_passively"
    # Behavioral profile contract: high ambiguity + clarification rate,
    # very low decisiveness (low override).
    assert p.ambiguity_in_answers == "high"
    assert p.clarification_request_probability >= 0.5
    assert p.override_probability <= 0.05


def test_persona_new_fields_default_for_methodical():
    """Backward compat: methodical YAML (no new fields) loads cleanly.

    The MVP-added fields (gate_acceptance_probability,
    clarification_request_probability, prefers_short_rationale) all
    default to methodical-friendly values so legacy persona YAMLs
    don't need bumping.
    """
    p = load_persona(persona_path("methodical"))
    # methodical YAML doesn't set these — defaults apply.
    assert 0.0 <= p.gate_acceptance_probability <= 1.0
    assert 0.0 <= p.clarification_request_probability <= 1.0
    assert p.prefers_short_rationale is False
    # Track H Final additive fields default to zero so legacy YAMLs
    # validate unchanged.
    assert p.feature_addition_probability == 0.0
    assert p.contradictory_input_probability == 0.0
    assert p.tangent_question_probability == 0.0
    assert p.response_templates == {}


def test_scope_creeper_persona_yaml_ships_with_package():
    """scope-creeper persona — high feature-addition + override probability."""
    p = load_persona(persona_path("scope-creeper"))
    assert p.id == "scope-creeper"
    assert p.gate_confirmation_policy == "confirm_then_re_open"
    # Behavioral profile contract: pushes scope additions; willing to
    # override gates (re-opens after confirming).
    assert p.feature_addition_probability >= 0.5
    assert p.override_probability >= 0.2
    # Templates ship for the common gate kinds so policy-driven turns
    # have something to sample.
    assert "confirm_gate" in p.response_templates
    assert len(p.response_templates["confirm_gate"]) >= 2


def test_hostile_persona_yaml_ships_with_package():
    """hostile persona — high contradictory + tangent + low cooperation."""
    p = load_persona(persona_path("hostile"))
    assert p.id == "hostile"
    assert p.gate_confirmation_policy == "refuse_initially"
    # Behavioral profile contract: contradictory inputs, tangents,
    # low cooperation (refuses initially), low patience.
    assert p.contradictory_input_probability >= 0.5
    assert p.tangent_question_probability >= 0.3
    assert p.patience_for_clarification == "very_low"
    # Templates ship including the junk-input + tangent forms.
    assert "confirm_gate" in p.response_templates
    assert "give_pitch" in p.response_templates


def test_persona_new_behavior_fields_constraints():
    """New probability fields enforce [0.0, 1.0]."""
    with pytest.raises(ValidationError):
        Persona(
            id="bad",
            description="x",
            gate_confirmation_policy="confirm_when_clear",
            feature_addition_probability=1.5,
        )
    with pytest.raises(ValidationError):
        Persona(
            id="bad",
            description="x",
            gate_confirmation_policy="confirm_when_clear",
            contradictory_input_probability=-0.1,
        )


# ---- scenario steps -----------------------------------------------------


def test_scenario_step_invoke_l0_finalize():
    s = ScenarioStep(
        kind=StepKind.INVOKE_L0_FINALIZE,
        params={
            "name": "jig-search",
            "pitch": "x",
            "problem": "y",
            "audience": "z",
            "non_goals": [],
        },
    )
    assert s.kind == StepKind.INVOKE_L0_FINALIZE.value
    assert s.params["name"] == "jig-search"


def test_scenario_step_assertions_optional():
    s = ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS)
    assert s.assertions == []


# ---- top-level scenario -------------------------------------------------


def test_scenario_validates_minimal_shape(tmp_path: Path):
    src = tmp_path / "min.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "minimal",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "steps": [
                    {"kind": "materialize_tickets"},
                ],
                "final_assertions": [],
            }
        )
    )
    scn = load_scenario(src)
    assert scn.id == "minimal"
    assert scn.persona == "methodical"
    assert len(scn.steps) == 1


def test_scenario_round_trips_through_yaml(tmp_path: Path):
    src = tmp_path / "rt.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "rt",
                "description": "round trip",
                "persona": "methodical",
                "estimated_cost_usd_max": 1.0,
                "steps": [
                    {
                        "kind": "invoke_l0_finalize",
                        "params": {"name": "x", "pitch": "p"},
                        "assertions": [
                            {
                                "kind": "artifact_written",
                                "path": "docs/brief.md",
                            }
                        ],
                    },
                ],
                "final_assertions": [
                    {"kind": "cost_under_budget", "usd": 1.0},
                ],
            }
        )
    )
    scn = load_scenario(src)
    assert scn.steps[0].assertions[0].kind == AssertionKind.ARTIFACT_WRITTEN.value
    assert scn.final_assertions[0].kind == AssertionKind.COST_UNDER_BUDGET.value


def test_scenario_rejects_unknown_persona_at_load(tmp_path: Path):
    """Catch typos early — the driver reads ``persona`` to load the YAML."""
    src = tmp_path / "bad.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "bad",
                "description": "x",
                "persona": "",  # empty persona id
                "estimated_cost_usd_max": 0.0,
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    with pytest.raises(ValidationError):
        load_scenario(src)


def test_scenario_rejects_extra_fields(tmp_path: Path):
    src = tmp_path / "extra.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "extra",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "steps": [],
                "final_assertions": [],
                "made_up_field": "boom",
            }
        )
    )
    with pytest.raises(ValidationError):
        load_scenario(src)


def test_scenario_step_kinds_cover_bones_workflow():
    """Bones must support the 7-step lifecycle the scenario walks.

    Per the task description: L0 PO finalize → manual L1/L2 →
    L3 PO finalize → manual SA writes → manual build plan + Coordinator
    → mock dev commit → reviewer.
    """
    expected = {
        "invoke_l0_finalize",
        "write_suites_yaml",
        "invoke_l3_finalize",
        "write_architecture",
        "write_module_contracts",
        "write_build_plan",
        "materialize_tickets",
        "mock_dev_commit",
        "run_reviewer",
    }
    actual = {k.value for k in StepKind}
    missing = expected - actual
    assert not missing, f"Bones scenario step kinds missing: {missing}"
