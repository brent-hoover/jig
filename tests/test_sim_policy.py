"""Policy-driven turn machinery (Track H Final).

Tests the deterministic-given-seed contract of ``apply_policy`` +
``sample_response``, the per-persona distinct sampling distributions,
and integration with ``Scenario.policy_driven`` via ``Driver.run``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from jig.sim.driver import Driver
from jig.sim.persona import Persona, load_persona, persona_path
from jig.sim.policy import (
    PolicyTurn,
    apply_policy,
    sample_response,
)
from jig.sim.scenario import Scenario, load_scenario


def _seed_repo(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    for k, v in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", k, v], cwd=root, check=True, capture_output=True
        )
    (root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=root, check=True, capture_output=True
    )


# ---- determinism --------------------------------------------------------


def test_apply_policy_is_deterministic_given_seed():
    """Same (persona, prompt_kind, seed) always returns the same response."""
    p = load_persona(persona_path("scope-creeper"))
    a = apply_policy(p, "any prompt", prompt_kind="confirm_gate", scenario_seed=42)
    b = apply_policy(p, "any prompt", prompt_kind="confirm_gate", scenario_seed=42)
    assert a.response == b.response
    assert isinstance(a, PolicyTurn)


def test_apply_policy_seed_changes_response():
    """Different seeds usually change the sampled response (across enough seeds)."""
    p = load_persona(persona_path("scope-creeper"))
    samples = {
        apply_policy(
            p, "x", prompt_kind="confirm_gate", scenario_seed=s
        ).response
        for s in range(50)
    }
    # The persona has multiple template entries; over 50 seeds we
    # expect to land on more than one bucket.
    assert len(samples) > 1


def test_apply_policy_persona_changes_response():
    """Different personas yield different sampled responses for the same seed."""
    creeper = load_persona(persona_path("scope-creeper"))
    hostile = load_persona(persona_path("hostile"))
    a = apply_policy(creeper, "x", prompt_kind="confirm_gate", scenario_seed=7)
    b = apply_policy(hostile, "x", prompt_kind="confirm_gate", scenario_seed=7)
    # Personas have disjoint template banks so the responses must differ.
    assert a.response != b.response


# ---- sample_response fallback -------------------------------------------


def test_sample_response_falls_back_to_gate_policy_text():
    """When a persona has no templates for a kind, fall back to gate-policy text."""
    p = Persona(
        id="pristine",
        description="x",
        gate_confirmation_policy="confirm_eagerly",
    )
    out = sample_response(p, "confirm_gate", scenario_seed=0)
    # The eager-confirm fallback is the canonical "ship it" response.
    assert "ship" in out.lower() or "yes" in out.lower()


def test_sample_response_uses_template_when_available():
    p = Persona(
        id="custom",
        description="x",
        gate_confirmation_policy="confirm_eagerly",
        response_templates={"confirm_gate": ["ship it now", "go go go"]},
    )
    out = sample_response(p, "confirm_gate", scenario_seed=0)
    assert out in {"ship it now", "go go go"}


# ---- per-persona sampling distinctness ----------------------------------


def test_methodical_vs_hostile_distributions_differ():
    """Across many seeds, methodical and hostile sample distinct populations."""
    methodical = load_persona(persona_path("methodical"))
    hostile = load_persona(persona_path("hostile"))
    methodical_samples = {
        sample_response(methodical, "confirm_gate", scenario_seed=s)
        for s in range(20)
    }
    hostile_samples = {
        sample_response(hostile, "confirm_gate", scenario_seed=s)
        for s in range(20)
    }
    # The two persona populations must not overlap (methodical falls
    # back to its gate-policy text; hostile samples its template bank).
    assert methodical_samples.isdisjoint(hostile_samples)


# ---- Scenario.policy_driven integration ---------------------------------


def test_scenario_policy_driven_field_default():
    scn = Scenario(
        id="x",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
    )
    assert scn.policy_driven is False
    assert scn.scenario_seed == 0


def test_scenario_loads_policy_driven_yaml(tmp_path: Path):
    src = tmp_path / "x.scenario.yaml"
    src.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "id": "x",
                "description": "x",
                "persona": "methodical",
                "estimated_cost_usd_max": 0.0,
                "policy_driven": True,
                "scenario_seed": 7,
                "steps": [],
                "final_assertions": [],
            }
        )
    )
    scn = load_scenario(src)
    assert scn.policy_driven is True
    assert scn.scenario_seed == 7


async def test_driver_records_policy_turns_when_policy_driven(tmp_path: Path):
    """Driver populates report.policy_turns for a policy-driven scenario."""
    _seed_repo(tmp_path)
    scn = Scenario(
        id="policy-x",
        description="x",
        persona="scope-creeper",
        estimated_cost_usd_max=0.0,
        policy_driven=True,
        scenario_seed=11,
        steps=[],
        final_assertions=[],
    )
    driver = Driver()
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed
    # Empty steps → empty policy_turns; the no-op shape is the load-bearing
    # contract here (policy_turns is opt-in per scenario).
    assert report.policy_turns == []


async def test_driver_skips_policy_for_scripted_scenarios(tmp_path: Path):
    """Scripted scenarios do not load a persona or sample turns."""
    _seed_repo(tmp_path)
    scn = Scenario(
        id="scripted-x",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[],
        final_assertions=[],
    )
    driver = Driver()
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed
    assert report.policy_turns == []


async def test_driver_policy_turns_log_per_step(tmp_path: Path):
    """One policy turn lands per scenario step when policy_driven=true."""
    from jig.sim.scenario import ScenarioStep, StepKind

    _seed_repo(tmp_path)
    scn = Scenario(
        id="policy-y",
        description="x",
        persona="hostile",
        estimated_cost_usd_max=0.0,
        policy_driven=True,
        scenario_seed=3,
        steps=[
            ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS, params={}),
            ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS, params={}),
        ],
        final_assertions=[],
    )
    driver = Driver()
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed
    assert len(report.policy_turns) == 2
    assert all(t.persona_id == "hostile" for t in report.policy_turns)
    assert all(t.scenario_seed == 3 for t in report.policy_turns)


async def test_driver_policy_turns_reproducible_across_runs(tmp_path: Path):
    """Two identical runs of a policy-driven scenario produce identical turns."""
    from jig.sim.scenario import ScenarioStep, StepKind

    def _make_scn() -> Scenario:
        return Scenario(
            id="policy-z",
            description="x",
            persona="scope-creeper",
            estimated_cost_usd_max=0.0,
            policy_driven=True,
            scenario_seed=99,
            steps=[ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS, params={})],
            final_assertions=[],
        )

    _seed_repo(tmp_path)
    a = await Driver().run(_make_scn(), project_root=tmp_path)

    tmp_b = tmp_path.parent / "policy-rep-b"
    tmp_b.mkdir()
    _seed_repo(tmp_b)
    b = await Driver().run(_make_scn(), project_root=tmp_b)

    responses_a = [t.response for t in a.policy_turns]
    responses_b = [t.response for t in b.policy_turns]
    assert responses_a == responses_b
