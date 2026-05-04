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

from jig.intent import Intent
from jig.schemas.arch import (
    Architecture,
    ContractsFile,
    IntegrationAcceptance,
    Module,
    OwnedCollection,
)
from jig.schemas.plan import (
    BuildPlan,
    Epic,
    EpicLayers,
    LayerStatus,
)
from jig.schemas.po import Suite, SuitesIndex
from jig.sim.assertions import (
    AnalyticsEventEmittedAssertion,
    ArtifactWrittenAssertion,
    CostUnderBudgetAssertion,
    ReviewerReturnedNoCriticalAssertion,
    TicketStatusAssertion,
)
from jig.sim.driver import (
    AssertionResult,
    Driver,
    ScenarioReport,
)
from jig.sim.scenario import Scenario, ScenarioStep, StepKind
from jig.spec_loader import (
    architecture_path,
    build_plan_path,
    module_contracts_path,
    suite_brief_path,
    suite_structured_path,
    suites_index_path,
)


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
            ArtifactWrittenAssertion(path=".jig/spec/project.md"),
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
    project_md = tmp_path / ".jig" / "spec" / "project.md"
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
                    ArtifactWrittenAssertion(path=".jig/spec/project.md"),
                ],
            ),
        ],
        final_assertions=[],
    )
    report = await Driver().run(scn, project_root=tmp_path)
    assert not report.passed
    assert any("project.md" in r.detail for r in report.failed_assertions())


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
                        path=".jig/spec/project.md",
                        contains="bones-demo",
                    ),
                    ArtifactWrittenAssertion(
                        path=".jig/spec/project.md",
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
            TicketStatusAssertion(
                ticket_id="tb-catalog-ingest", status="open"
            ),
            TicketStatusAssertion(
                ticket_id="tb-catalog-ingest", status="resolved"
            ),
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
    assert all(isinstance(r, AssertionResult) for r in report.step_outcomes[0].assertions)


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
    db_path = (
        tmp_path / ".jig" / "dev" / "ephemeral" / "scratch" / "agent_tb_eph.db"
    )
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
