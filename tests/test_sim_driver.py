"""Synthetic operator driver + per-run isolation (Track H2+H6, bones).

The driver loads a scenario, sets up a fresh ``.jig/`` workspace under
``tmp_path``, walks the scripted steps, captures outcomes, and evaluates
assertions. Bones executes in mock mode by default; the dev step is
replaced with a deterministic helper that produces a small commit
satisfying the contract-compliance reviewer's bones checks.

Per-run isolation: every ``Driver.run`` instantiates a fresh
``DriverContext`` rooted at the caller-supplied ``project_root`` (a
``tmp_path`` in tests). The driver sets ``JIG_SIMULATOR=true`` for the
duration of the run so analytics events get tagged with
``simulator: true`` (see ``jig.analytics.emitter`` — already wired).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml as _yaml

from jig.analytics.emitter import EventEmitter
from jig.analytics.store import AnalyticsStore
from jig.intent import Intent
from jig.intent import Intent as _Intent
from jig.pm.calibration import (
    CalibrationSample,
    CalibrationStore,
)
from jig.schemas.arch import (
    Architecture,
    BehavioralContract,
    CascadeContractDisposition,
    CascadeProposal,
    ContractsFile,
    DataContract,
    IntegrationAcceptance,
    Module,
    OwnedCollection,
    Risk,
    RiskImpact,
    RiskLikelihood,
    RiskStatus,
)
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
)
from jig.schemas.po import Ontology, OntologyTerm, Suite, SuitesIndex
from jig.sim.assertions import (
    AnalyticsEventEmittedAssertion,
    ArtifactWrittenAssertion,
    BuildPlanLayerStatusAssertion,
    CascadeProposalAssertion,
    ContractValidatedAssertion,
    CostUnderBudgetAssertion,
    DiscoveryStateConsistentAssertion,
    EnvelopeUpdatedAssertion,
    FixtureCassetteAssertion,
    OntologyTermAssertion,
    OrphanReportAssertion,
    ProvisioningSucceededAssertion,
    ReviewerReturnedNoCriticalAssertion,
    RiskStatusAssertion,
    TicketStatusAssertion,
    TierPromotionAssertion,
    WireframeAssertion,
)
from jig.sim.driver import (
    AssertionResult,
    Driver,
    DriverContext,
    ScenarioReport,
    _check_build_plan_layer_status,
    _check_cascade_proposal,
    _check_contract_validated,
    _check_discovery_state_consistent,
    _check_envelope_updated,
    _check_fixture_cassette,
    _check_ontology_term,
    _check_orphan_report,
    _check_provisioning_succeeded,
    _check_risk_status,
    _check_tier_promotion,
    _check_wireframe,
)
from jig.sim.scenario import Scenario, ScenarioStep, StepKind
from jig.spec_loader import (
    architecture_path,
    build_plan_path,
    module_contracts_path,
    save_architecture,
    save_module_contracts,
    save_ontology,
    save_wireframe,
    suite_brief_path,
    suite_structured_path,
    suites_index_path,
    write_build_plan,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore


# ---- helpers -----------------------------------------------------------


def _intent(p: str = "x") -> Intent:
    return Intent(problem=p, simplest_solution="y")


def _git_init(root: Path) -> None:
    """Initialize a fixture repo so the reviewer's worktree-diff path
    has something to diff against. Mirrors test_reviewers fixtures."""
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=root,
        check=True,
        capture_output=True,
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


def _l0_step() -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.INVOKE_L0_FINALIZE,
        params={
            "name": "bones-demo",
            "pitch": "demo project for bones spine",
            "problem": "we need a spine",
            "audience": "internal jig dev",
            "non_goals": [],
            "author": "po-l0",
        },
        assertions=[
            ArtifactWrittenAssertion(path="docs/brief.md"),
        ],
    )


def _suites_step() -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.WRITE_SUITES_YAML,
        params={
            "suites_index": SuitesIndex(
                suites=[
                    Suite(
                        id="catalog",
                        title="Catalog",
                        summary="x",
                        capabilities=["shopify-connect"],
                    ),
                ],
            ).model_dump(mode="json"),
        },
    )


def _l3_step() -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.INVOKE_L3_FINALIZE,
        params={
            "suite_id": "catalog",
            "intro": "Catalog ingestion suite",
            "capabilities": [
                {
                    "id": "shopify-connect",
                    "title": "Shopify connect",
                    "state": "planned",
                    "summary": "Pull catalog from Shopify",
                    "behaviors": [
                        {
                            "id": "begin-oauth",
                            "description": "Operator begins OAuth",
                            "acceptance_criteria": ["Operator clicks connect"],
                        },
                    ],
                }
            ],
            "author": "po-l3",
        },
    )


def _arch_step() -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.WRITE_ARCHITECTURE,
        params={
            "architecture": Architecture(
                modules=[
                    Module(
                        id="catalog-ingest",
                        title="Catalog ingest",
                        summary="x",
                        implements_capabilities=["shopify-connect"],
                        intent=_intent("ingest catalog"),
                    )
                ],
            ).model_dump(mode="json"),
        },
    )


def _contracts_step() -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.WRITE_MODULE_CONTRACTS,
        params={
            "module_id": "catalog-ingest",
            "contracts": ContractsFile(
                module="catalog-ingest",
                owns=[OwnedCollection(collection="products", db="catalog")],
                integration_ac=[
                    IntegrationAcceptance(
                        capability="shopify-connect",
                        must=["Shopify catalog flows into products collection"],
                    ),
                ],
            ).model_dump(mode="json"),
        },
    )


def _plan_step(ticket_id: str = "tb-catalog-ingest") -> ScenarioStep:
    plan = BuildPlan(
        project="bones-demo",
        epics=[
            Epic(
                id="catalog-ingest",
                title="Catalog ingest",
                suite="catalog",
                modules=["catalog-ingest"],
                layers=EpicLayers(bones=LayerStatus(tickets=[ticket_id])),
                intent=_intent("Spine"),
                acceptance_criteria=[
                    "Test fixture placeholder; replace if the test cares about AC content."
                ],
            )
        ],
    )
    return ScenarioStep(
        kind=StepKind.WRITE_BUILD_PLAN,
        params={"plan": plan.model_dump(mode="json")},
    )


def _materialize_step() -> ScenarioStep:
    return ScenarioStep(kind=StepKind.MATERIALIZE_TICKETS)


def _mock_dev_step(ticket_id: str = "tb-catalog-ingest") -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.MOCK_DEV_COMMIT,
        params={"ticket_id": ticket_id},
    )


def _reviewer_step(ticket_id: str = "tb-catalog-ingest") -> ScenarioStep:
    return ScenarioStep(
        kind=StepKind.RUN_REVIEWER,
        params={"ticket_id": ticket_id},
        assertions=[
            ReviewerReturnedNoCriticalAssertion(reviewer_id="contract-compliance"),
        ],
    )


def _bones_scenario(ticket_id: str = "tb-catalog-ingest") -> Scenario:
    return Scenario(
        id="bones-driver-test",
        description="driver test exercise",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            _l0_step(),
            _suites_step(),
            _l3_step(),
            _arch_step(),
            _contracts_step(),
            _plan_step(ticket_id),
            _materialize_step(),
            _mock_dev_step(ticket_id),
            _reviewer_step(ticket_id),
        ],
        final_assertions=[
            CostUnderBudgetAssertion(usd=1.0),
            TicketStatusAssertion(ticket_id=ticket_id, status="resolved"),
            AnalyticsEventEmittedAssertion(event_kind="ticket_state_changed"),
        ],
    )


# ---- per-run isolation -------------------------------------------------


@pytest.mark.asyncio
async def test_driver_creates_fresh_jig_state(tmp_path: Path):
    """Per-run isolation: the driver creates the .jig/ tree itself."""
    _git_init(tmp_path)
    scn = Scenario(
        id="empty",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[],
        final_assertions=[],
    )
    driver = Driver()
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed
    assert (tmp_path / ".jig").is_dir()


@pytest.mark.asyncio
async def test_driver_sets_simulator_env_var_during_run(tmp_path: Path):
    """JIG_SIMULATOR must be true during the run so analytics tag events.

    Driver flips the env var on entry and restores it on exit so other
    tests in the suite don't see leakage.
    """
    _git_init(tmp_path)
    saw_value: dict[str, str | None] = {}

    async def _peek_step_handler(_ctx: object, _step: object) -> None:
        saw_value["JIG_SIMULATOR"] = os.environ.get("JIG_SIMULATOR")

    driver = Driver()
    driver.register_step_handler("peek", _peek_step_handler)
    scn = Scenario(
        id="env",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[ScenarioStep.model_construct(kind="peek", params={}, assertions=[])],
        final_assertions=[],
    )
    pre = os.environ.get("JIG_SIMULATOR")
    await driver.run(scn, project_root=tmp_path)
    assert saw_value["JIG_SIMULATOR"] == "true"
    # Restored after run completes.
    assert os.environ.get("JIG_SIMULATOR") == pre


# ---- step dispatch -----------------------------------------------------


@pytest.mark.asyncio
async def test_driver_invokes_l0_finalize_and_writes_artifact(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="l0-only",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[_l0_step()],
        final_assertions=[],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    project_md = tmp_path / "docs" / "brief.md"
    assert project_md.is_file()
    assert "bones-demo" in project_md.read_text()


@pytest.mark.asyncio
async def test_driver_writes_suites_yaml_then_l3_loads_it(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="suites-and-l3",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[_l0_step(), _suites_step(), _l3_step()],
        final_assertions=[],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    assert suites_index_path(tmp_path).is_file()
    assert suite_brief_path(tmp_path, "catalog").is_file()
    assert suite_structured_path(tmp_path, "catalog").is_file()


@pytest.mark.asyncio
async def test_driver_writes_architecture_and_contracts(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="arch",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[_arch_step(), _contracts_step()],
        final_assertions=[],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    assert architecture_path(tmp_path).is_file()
    assert module_contracts_path(tmp_path, "catalog-ingest").is_file()


@pytest.mark.asyncio
async def test_driver_writes_build_plan_and_materializes(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="plan",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[_plan_step(), _materialize_step()],
        final_assertions=[
            TicketStatusAssertion(ticket_id="tb-catalog-ingest", status="open"),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    assert build_plan_path(tmp_path).is_file()


@pytest.mark.asyncio
async def test_mock_dev_commit_satisfies_bones_reviewer(tmp_path: Path):
    """Mock dev step must produce a diff that passes contract-compliance.

    The bones reviewer requires (a) non-empty diff and (b) at least one
    significant token from each integration AC's must list. The mock
    dev helper writes a file naming the AC keywords directly.
    """
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="mock-dev",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            _arch_step(),
            _contracts_step(),
            _plan_step(),
            _materialize_step(),
            _mock_dev_step(),
            _reviewer_step(),
        ],
        final_assertions=[
            ReviewerReturnedNoCriticalAssertion(reviewer_id="contract-compliance"),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


# ---- assertion evaluation ----------------------------------------------


@pytest.mark.asyncio
async def test_artifact_written_assertion_fails_when_path_missing(tmp_path: Path):
    _git_init(tmp_path)
    scn = Scenario(
        id="bad-artifact",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.MATERIALIZE_TICKETS,
                assertions=[
                    ArtifactWrittenAssertion(path="docs/brief.md"),
                ],
            ),
        ],
        final_assertions=[],
    )
    report = await Driver().run(scn, project_root=tmp_path)
    assert not report.passed
    assert any("brief.md" in r.detail for r in report.failed_assertions())


@pytest.mark.asyncio
async def test_artifact_written_with_contains_substring(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="contains",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            _l0_step(),
            ScenarioStep(
                kind=StepKind.MATERIALIZE_TICKETS,
                assertions=[
                    ArtifactWrittenAssertion(
                        path="docs/brief.md",
                        contains="bones-demo",
                    ),
                    ArtifactWrittenAssertion(
                        path="docs/brief.md",
                        contains="not-in-the-pitch",
                    ),
                ],
            ),
        ],
        final_assertions=[],
    )
    report = await driver.run(scn, project_root=tmp_path)
    failures = report.failed_assertions()
    assert len(failures) == 1
    assert "not-in-the-pitch" in failures[0].detail


@pytest.mark.asyncio
async def test_analytics_event_emitted_assertion(tmp_path: Path):
    """The driver pre-creates an analytics store + emitter so events flow.

    Per the orchestrator's analytics wiring: ticket *create* doesn't
    emit, only *transitions*. So we run the full bones lifecycle
    (which transitions OPEN → RESOLVED via the reviewer) and check
    that the transition was captured.
    """
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="analytics",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            _arch_step(),
            _contracts_step(),
            _plan_step(),
            _materialize_step(),
            _mock_dev_step(),
            _reviewer_step(),
        ],
        final_assertions=[
            AnalyticsEventEmittedAssertion(event_kind="ticket_state_changed"),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_analytics_events_are_simulator_tagged(tmp_path: Path):
    """Per-run isolation requires simulator events to carry the flag."""
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="tag",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            _arch_step(),
            _contracts_step(),
            _plan_step(),
            _materialize_step(),
            _mock_dev_step(),
            _reviewer_step(),
        ],
        final_assertions=[],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    # Every event captured during the run must have simulator=True.
    assert report.captured_events  # at least one event landed
    assert all(e.simulator for e in report.captured_events)


@pytest.mark.asyncio
async def test_ticket_status_assertion(tmp_path: Path):
    """Materialize creates the ticket as OPEN; without dev/review steps
    it stays OPEN, so an assertion expecting ``resolved`` fails."""
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="ts",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[_plan_step(), _materialize_step()],
        final_assertions=[
            TicketStatusAssertion(ticket_id="tb-catalog-ingest", status="open"),
            TicketStatusAssertion(ticket_id="tb-catalog-ingest", status="resolved"),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    failures = report.failed_assertions()
    assert len(failures) == 1
    assert "resolved" in failures[0].detail


@pytest.mark.asyncio
async def test_reviewer_returned_no_critical_assertion(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="rev",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            _arch_step(),
            _contracts_step(),
            _plan_step(),
            _materialize_step(),
            _mock_dev_step(),
            _reviewer_step(),
        ],
        final_assertions=[
            ReviewerReturnedNoCriticalAssertion(reviewer_id="contract-compliance"),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_cost_under_budget_assertion_passes_in_mock_mode(tmp_path: Path):
    _git_init(tmp_path)
    scn = Scenario(
        id="cost",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[],
        final_assertions=[
            CostUnderBudgetAssertion(usd=0.01),
        ],
    )
    report = await Driver().run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


# ---- report shape ------------------------------------------------------


@pytest.mark.asyncio
async def test_report_records_per_step_outcomes(tmp_path: Path):
    _git_init(tmp_path)
    driver = Driver()
    scn = Scenario(
        id="report",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[_l0_step()],
        final_assertions=[
            CostUnderBudgetAssertion(usd=1.0),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert isinstance(report, ScenarioReport)
    assert len(report.step_outcomes) == 1
    assert report.step_outcomes[0].kind == StepKind.INVOKE_L0_FINALIZE.value
    assert all(
        isinstance(r, AssertionResult) for r in report.step_outcomes[0].assertions
    )


@pytest.mark.asyncio
async def test_full_bones_lifecycle_passes_in_mock_mode(tmp_path: Path):
    """End-to-end bones lifecycle through the driver.

    Mirrors the bones-walking-skeleton scenario shape so any mock-mode
    regression surfaces here before it touches the real scenario file.
    """
    _git_init(tmp_path)
    driver = Driver()
    scn = _bones_scenario()
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


# ---------------------------------------------------------------------------
# Track E MVP — invoke_dev_provisioning step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_dev_provisioning_step_records_sql(tmp_path: Path):
    """Step records CREATE SCHEMA + DROP SCHEMA via the in-memory recorder.

    Verifies the dev manifest is auto-derived on first call and that
    the provisioning lifecycle (provision + cleanup) fires for the
    declared shared_namespaced Postgres data store.
    """
    from jig.schemas.arch import Architecture, DataStore, DevProvisioning
    from jig.spec_loader import save_architecture

    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(
                    strategy="shared_namespaced",
                    namespace_template="agent_{ticket_id}",
                ),
            ),
        ],
        modules=[
            Module(
                id="m",
                title="t",
                summary="s",
                intent=_intent("p"),
            )
        ],
    )
    save_architecture(tmp_path, arch)

    driver = Driver()
    scn = Scenario(
        id="dev-prov-only",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_DEV_PROVISIONING,
                params={"ticket_id": "tb-1", "cleanup": True, "success": True},
                assertions=[
                    ArtifactWrittenAssertion(
                        path=".jig/dev/manifest.yaml", contains="shared_namespaced"
                    ),
                ],
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_invoke_dev_provisioning_requires_ticket_id(tmp_path: Path):
    """Missing ticket_id surfaces as a step error."""
    from jig.schemas.arch import Architecture, DataStore, DevProvisioning
    from jig.spec_loader import save_architecture

    arch = Architecture(
        data_stores=[
            DataStore(
                id="main-db",
                kind="postgres",
                dev_provisioning=DevProvisioning(strategy="shared_namespaced"),
            ),
        ],
    )
    save_architecture(tmp_path, arch)

    driver = Driver()
    scn = Scenario(
        id="dev-prov-bad",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_DEV_PROVISIONING,
                params={},
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert not report.passed
    assert "ticket_id" in (report.step_outcomes[0].error or "")


# ---------------------------------------------------------------------------
# Track E Final — invoke_dev_ephemeral + invoke_fixture_replay step kinds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invoke_dev_ephemeral_provisions_and_cleans(tmp_path: Path):
    """Step provisions a SQLite ephemeral file; cleanup removes it.

    Verifies the file lands under .jig/dev/ephemeral/<service>/ + the
    URL flows back into ctx.last_ephemeral_url.
    """
    driver = Driver()
    scn = Scenario(
        id="dev-ephemeral-only",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_DEV_EPHEMERAL,
                params={
                    "ticket_id": "tb-eph",
                    "service_id": "scratch",
                    "namespace_template": "agent_{ticket_id}",
                    "cleanup": True,
                    "success": True,
                },
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()
    # The cleanup ran; the file should be gone.
    db_path = tmp_path / ".jig" / "dev" / "ephemeral" / "scratch" / "agent_tb_eph.db"
    assert not db_path.is_file()


@pytest.mark.asyncio
async def test_invoke_dev_ephemeral_requires_ticket_id(tmp_path: Path):
    driver = Driver()
    scn = Scenario(
        id="dev-ephemeral-bad",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_DEV_EPHEMERAL,
                params={},
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert not report.passed
    assert "ticket_id" in (report.step_outcomes[0].error or "")


@pytest.mark.asyncio
async def test_invoke_fixture_replay_records_and_replays(tmp_path: Path):
    """Step records a cassette + replays it through REPLAY_ONLY middleware."""
    driver = Driver()
    scn = Scenario(
        id="fixture-replay-only",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_FIXTURE_REPLAY,
                params={
                    "service_id": "shopify-api",
                    "method": "GET",
                    "url": "https://api.shopify.com/products",
                    "response": {"status": 200, "body": "ok"},
                },
                assertions=[
                    ArtifactWrittenAssertion(
                        path=".jig/dev/fixtures/shopify-api.jsonl",
                        contains="shopify-api",
                    ),
                ],
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_invoke_fixture_replay_requires_service_id(tmp_path: Path):
    driver = Driver()
    scn = Scenario(
        id="fixture-bad",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_FIXTURE_REPLAY,
                params={},
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert not report.passed
    assert "service_id" in (report.step_outcomes[0].error or "")


# ---- Track I Final: invoke_quartermaster_feedback step kind --------------


@pytest.mark.asyncio
async def test_invoke_quartermaster_feedback_records_row(tmp_path: Path):
    """Standalone driver test — one feedback step writes a row + updates
    the on-context calibration map."""
    driver = Driver()
    scn = Scenario(
        id="qm-feedback-only",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_QUARTERMASTER_FEEDBACK,
                params={
                    "briefing_id": "brief-driver-test",
                    "useful": False,
                    "not_useful_pattern_ids": ["module_repeated_escalations"],
                },
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    from jig.quartermaster import load_feedback

    rows = await load_feedback(tmp_path)
    assert len(rows) == 1
    assert rows[0].briefing_id == "brief-driver-test"
    assert rows[0].not_useful_pattern_ids == ["module_repeated_escalations"]


@pytest.mark.asyncio
async def test_invoke_quartermaster_feedback_requires_briefing_id(tmp_path: Path):
    """Missing briefing_id surfaces as a step error."""
    driver = Driver()
    scn = Scenario(
        id="qm-feedback-bad",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_QUARTERMASTER_FEEDBACK,
                params={"useful": False},
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert not report.passed
    assert "briefing_id" in (report.step_outcomes[0].error or "")


@pytest.mark.asyncio
async def test_invoke_quartermaster_feedback_requires_useful(tmp_path: Path):
    """Missing useful surfaces as a step error."""
    driver = Driver()
    scn = Scenario(
        id="qm-feedback-no-useful",
        description="x",
        persona="methodical",
        estimated_cost_usd_max=0.0,
        steps=[
            ScenarioStep(
                kind=StepKind.INVOKE_QUARTERMASTER_FEEDBACK,
                params={"briefing_id": "brief-1"},
            ),
        ],
    )
    report = await driver.run(scn, project_root=tmp_path)
    assert not report.passed
    assert "useful" in (report.step_outcomes[0].error or "")


# ---------------------------------------------------------------------------
# Block 4 — semantic-state assertion checks (issue #4)
# ---------------------------------------------------------------------------
#
# Each new assertion kind gets a happy-path + sad-path test. Tests
# directly exercise the ``_check_*`` helpers (rather than spinning a full
# scenario) so failure modes are pinpoint and the test suite stays fast.


async def _make_ctx(tmp_path: Path) -> DriverContext:
    """Build a minimal DriverContext for direct assertion-helper tests."""
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    bus = MessageBus(store_dir / "messages.jsonl")
    analytics = AnalyticsStore(store_dir / "analytics.jsonl")
    for s in (tickets, threads, bus, analytics):
        await s.load()
    return DriverContext(
        project_root=tmp_path,
        tickets=tickets,
        threads=threads,
        bus=bus,
        analytics=analytics,
        emitter=EventEmitter(analytics, simulator_mode=True),
    )


def _intent_obj() -> _Intent:
    return _Intent(problem="P", simplest_solution="S")


# ---- ContractValidatedAssertion -------------------------------------------


@pytest.mark.asyncio
async def test_contract_validated_passes_for_present_behavioral_contract(
    tmp_path: Path,
):
    ctx = await _make_ctx(tmp_path)
    save_module_contracts(
        tmp_path,
        "catalog-ingest",
        ContractsFile(
            module="catalog-ingest",
            behavioral_contracts=[
                BehavioralContract(
                    id="ingest-batch-atomicity",
                    invariant="forward-only",
                    intent=_intent_obj(),
                ),
            ],
        ),
    )
    result = _check_contract_validated(
        ctx,
        ContractValidatedAssertion(
            module_id="catalog-ingest",
            contract_id="ingest-batch-atomicity",
            contract_kind="behavioral",
        ),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_contract_validated_fails_for_missing_contract(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_module_contracts(
        tmp_path,
        "catalog-ingest",
        ContractsFile(module="catalog-ingest"),
    )
    result = _check_contract_validated(
        ctx,
        ContractValidatedAssertion(
            module_id="catalog-ingest",
            contract_id="missing-id",
            contract_kind="behavioral",
        ),
    )
    assert not result.passed
    assert "not found" in result.detail


@pytest.mark.asyncio
async def test_contract_validated_data_contract_round_trip(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_module_contracts(
        tmp_path,
        "catalog-ingest",
        ContractsFile(
            module="catalog-ingest",
            data_contracts=[
                DataContract(
                    id="product-row",
                    description="x",
                    # Block A.1: a data contract must declare a shape.
                    fields={"id": "str", "name": "str"},
                    intent=_intent_obj(),
                ),
            ],
        ),
    )
    result = _check_contract_validated(
        ctx,
        ContractValidatedAssertion(
            module_id="catalog-ingest",
            contract_id="product-row",
            contract_kind="data",
        ),
    )
    assert result.passed


# ---- WireframeAssertion ---------------------------------------------------


@pytest.mark.asyncio
async def test_wireframe_passes_for_existing_clean_html(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_wireframe(tmp_path, "dashboard", "<html><body>dashboard</body></html>")
    result = _check_wireframe(
        ctx, WireframeAssertion(screen_id="dashboard", contains="dashboard")
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_wireframe_fails_for_missing_screen(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = _check_wireframe(ctx, WireframeAssertion(screen_id="missing"))
    assert not result.passed


@pytest.mark.asyncio
async def test_wireframe_fails_when_lint_marker_present(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_wireframe(tmp_path, "dashboard", "<!-- LINT-FAIL: missing-aria -->\n<html/>")
    result = _check_wireframe(ctx, WireframeAssertion(screen_id="dashboard"))
    assert not result.passed
    assert "LINT-FAIL" in result.detail


# ---- BuildPlanLayerStatusAssertion ----------------------------------------


@pytest.mark.asyncio
async def test_build_plan_layer_status_passes_when_match(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    write_build_plan(
        tmp_path,
        BuildPlan(
            project="x",
            epics=[
                Epic(
                    id="catalog-ingest",
                    title="t",
                    suite="catalog",
                    layers=EpicLayers(
                        bones=LayerStatus(tickets=["tb-1"]),
                    ),
                    intent=_intent_obj(),
                    acceptance_criteria=[
                        "Test fixture placeholder; replace if the test cares about AC content."
                    ],
                )
            ],
        ),
    )
    result = _check_build_plan_layer_status(
        ctx,
        BuildPlanLayerStatusAssertion(
            epic_id="catalog-ingest",
            layer="bones",
            status="not_started",
        ),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_build_plan_layer_status_fails_for_missing_epic(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    write_build_plan(tmp_path, BuildPlan(project="x"))
    result = _check_build_plan_layer_status(
        ctx,
        BuildPlanLayerStatusAssertion(
            epic_id="missing", layer="bones", status="not_started"
        ),
    )
    assert not result.passed
    assert "missing" in result.detail


# ---- RiskStatusAssertion --------------------------------------------------


@pytest.mark.asyncio
async def test_risk_status_passes_when_match(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_architecture(
        tmp_path,
        Architecture(
            risks=[
                Risk(
                    id="r-shopify-delta",
                    text="x",
                    impact=RiskImpact.MEDIUM,
                    likelihood=RiskLikelihood.MEDIUM,
                    status=RiskStatus.SPIKE_PROPOSED,
                    dependent_contracts=[
                        "project://arch/modules/m/contracts#owns/x",
                    ],
                    intent=_intent_obj(),
                )
            ]
        ),
    )
    result = _check_risk_status(
        ctx,
        RiskStatusAssertion(risk_id="r-shopify-delta", status="spike_proposed"),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_risk_status_fails_for_unknown_risk(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_architecture(tmp_path, Architecture())
    result = _check_risk_status(
        ctx, RiskStatusAssertion(risk_id="r-missing", status="open")
    )
    assert not result.passed


# ---- CascadeProposalAssertion ---------------------------------------------


@pytest.mark.asyncio
async def test_cascade_proposal_passes_when_present(tmp_path: Path):
    from jig.spec_loader import cascade_proposal_path

    ctx = await _make_ctx(tmp_path)
    proposal = CascadeProposal(
        cascade_id="r-x-20260501T000000",
        risk_id="r-x",
        spike_ticket_id="spike-r-x",
        finding="x",
        contracts=[
            CascadeContractDisposition(
                uri="project://arch/modules/m/contracts#owns/products",
                proposed_disposition="invalidated",
            )
        ],
    )
    target = cascade_proposal_path(tmp_path, "r-x", "20260501T000000")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_yaml.safe_dump(proposal.model_dump(mode="json")))

    result = _check_cascade_proposal(
        ctx,
        CascadeProposalAssertion(
            risk_id="r-x",
            min_contracts=1,
            contains_disposition="invalidated",
        ),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_cascade_proposal_fails_when_missing(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = _check_cascade_proposal(ctx, CascadeProposalAssertion(risk_id="r-missing"))
    assert not result.passed


# ---- OntologyTermAssertion ------------------------------------------------


@pytest.mark.asyncio
async def test_ontology_term_passes_when_term_present(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_ontology(
        tmp_path,
        Ontology(
            terms=[
                OntologyTerm(
                    term="Cart",
                    definition="What the buyer fills with products.",
                ),
            ]
        ),
    )
    result = _check_ontology_term(
        ctx,
        OntologyTermAssertion(term="Cart", definition_contains="buyer"),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_ontology_term_fails_when_term_missing(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    save_ontology(tmp_path, Ontology(terms=[]))
    result = _check_ontology_term(ctx, OntologyTermAssertion(term="ghost"))
    assert not result.passed


# ---- DiscoveryStateConsistentAssertion ------------------------------------


@pytest.mark.asyncio
async def test_discovery_state_consistent_fails_when_state_missing(
    tmp_path: Path,
):
    """No discovery.state.yaml → assertion surfaces the absence."""
    ctx = await _make_ctx(tmp_path)
    result = _check_discovery_state_consistent(ctx, DiscoveryStateConsistentAssertion())
    assert not result.passed
    assert "discovery state" in result.detail


# ---- EnvelopeUpdatedAssertion ---------------------------------------------


@pytest.mark.asyncio
async def test_envelope_updated_passes_when_sample_count_meets(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    store = CalibrationStore(tmp_path)
    await store.append(
        CalibrationSample(
            ticket_id="t-x",
            size="m",
            dev_tier="standard",
            layer=None,
            observed_turns=10,
            observed_tool_calls=20,
            observed_duration_ms=60000,
            observed_cost_usd=0.5,
            completion_status="success",
        )
    )
    result = await _check_envelope_updated(
        ctx, EnvelopeUpdatedAssertion(size="m", min_sample_count=1)
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_envelope_updated_fails_when_too_few_samples(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = await _check_envelope_updated(
        ctx, EnvelopeUpdatedAssertion(size="m", min_sample_count=1)
    )
    assert not result.passed


# ---- OrphanReportAssertion ------------------------------------------------


@pytest.mark.asyncio
async def test_orphan_report_passes_when_entries_present(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    log = tmp_path / ".jig" / "dev" / "orphans.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text('{"namespace": "n1"}\n{"namespace": "n2"}\n')
    result = _check_orphan_report(ctx, OrphanReportAssertion(min_entries=2))
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_orphan_report_fails_when_log_missing(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = _check_orphan_report(ctx, OrphanReportAssertion())
    assert not result.passed


# ---- ProvisioningSucceededAssertion ---------------------------------------


@pytest.mark.asyncio
async def test_provisioning_succeeded_passes_via_env_vars(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    ctx.dev_provisioning_env_vars["main-db"] = (
        "postgresql://localhost:5432/jigdev?search_path=ns_t"
    )
    result = _check_provisioning_succeeded(
        ctx,
        ProvisioningSucceededAssertion(
            service_id="main-db", url_contains="search_path=ns_t"
        ),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_provisioning_succeeded_fails_when_service_absent(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = _check_provisioning_succeeded(
        ctx, ProvisioningSucceededAssertion(service_id="ghost")
    )
    assert not result.passed


# ---- FixtureCassetteAssertion ---------------------------------------------


@pytest.mark.asyncio
async def test_fixture_cassette_passes_when_signature_present(tmp_path: Path):
    from jig.dev_env.fixtures import FixtureCassette, FixtureStore

    ctx = await _make_ctx(tmp_path)
    store = FixtureStore(tmp_path)
    await store.record(
        FixtureCassette(
            service_id="shopify-api",
            request_signature="sig-1",
            request={"method": "GET", "url": "/products"},
            response={"status": 200, "body": "[]"},
        )
    )
    result = await _check_fixture_cassette(
        ctx,
        FixtureCassetteAssertion(service_id="shopify-api", request_signature="sig-1"),
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_fixture_cassette_fails_when_no_cassettes(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = await _check_fixture_cassette(
        ctx, FixtureCassetteAssertion(service_id="ghost-service")
    )
    assert not result.passed


# ---- TierPromotionAssertion -----------------------------------------------


@pytest.mark.asyncio
async def test_tier_promotion_passes_when_to_tier_matches(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    ctx.last_tier_promotion_from = "standard"
    ctx.last_tier_promotion_to = "senior"
    result = _check_tier_promotion(
        ctx, TierPromotionAssertion(from_tier="standard", to_tier="senior")
    )
    assert result.passed, result.detail


@pytest.mark.asyncio
async def test_tier_promotion_fails_when_no_promotion_recorded(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    result = _check_tier_promotion(ctx, TierPromotionAssertion(to_tier="senior"))
    assert not result.passed


@pytest.mark.asyncio
async def test_tier_promotion_fails_when_to_tier_mismatches(tmp_path: Path):
    ctx = await _make_ctx(tmp_path)
    ctx.last_tier_promotion_from = "standard"
    ctx.last_tier_promotion_to = "senior"
    result = _check_tier_promotion(ctx, TierPromotionAssertion(to_tier="sa"))
    assert not result.passed
