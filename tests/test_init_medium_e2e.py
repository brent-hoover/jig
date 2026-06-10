"""Step 5: end-to-end medium auto-init through ``run_init``.

The sim ``.scenario.yaml`` harness hand-drives each finalize step and never
exercises ``run_init``'s auto-cascade, so this proves the medium L0→L3→sa_mvp
pipeline composes by driving the REAL ``run_init`` dispatch loop with fake
agents that invoke the REAL finalize handlers — no manual ``/init --proceed``,
no hand-written ``suites.yaml`` / ``architecture.yaml``.

Asserts the full success-criterion artifact chain lands:
``docs/brief.md`` → ``discovery.md`` → ``suites.yaml`` → per-suite
``spec.structured.yaml`` → per-module ``contracts.yaml``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import patch

import yaml

from jig.atomic import atomic_write_text
from jig.po_l0_mcp import handle_l0_finalize
from jig.po_l1_mcp import handle_discovery_finalize
from jig.po_l2_mcp import handle_l2_finalize
from jig.po_l3_mcp import handle_l3_finalize
from jig.runtime import AgentSpawnContext
from jig.sa_incremental_mcp import handle_arch_finalize, handle_arch_set_module
from jig.schemas.arch import ContractsFile
from jig.spec_loader import module_contracts_path
from jig.init_workflow import run_init

Handler = Callable[[AgentSpawnContext], Awaitable[None]]

# Capability id threaded through L1 roster → L2 suite → L3 capabilities → SA
# module so handle_l2_finalize's capability-coverage check passes.
_CAP = "shopify-connect"
_MODULE_ID = "catalog-ingest"


class _FakeAgent:
    """Dispatch table keyed on ``(role, ticket_id)`` — each handler runs the
    real finalize handler the agent would have driven, then exits.

    Unlike a permissive fake, an unhandled ``(role, ticket_id)`` raises: in this
    e2e every spawn is expected, and a no-op on an unexpected one would leave
    the loop's state unchanged and hang forever (no marker posted → same resume
    state next pass). Failing loud turns a routing regression into a clear
    error. ``calls`` records the spawn sequence for diagnosis + ordering asserts.
    """

    def __init__(self) -> None:
        self._handlers: dict[tuple[str, str], Handler] = {}
        self.calls: list[tuple[str, str]] = []

    def handle(self, *, role: str, ticket_id: str):
        def deco(fn: Handler) -> Handler:
            self._handlers[(role, ticket_id)] = fn
            return fn

        return deco

    async def run(self, ctx: AgentSpawnContext, emitter=None) -> None:
        key = (ctx.role, ctx.ticket.id)
        if key in self.calls:
            # Each level is spawned exactly once in the happy-path cascade. A
            # repeat means the prior spawn's finalize didn't post its marker, so
            # resume state never advanced — fail here instead of looping forever.
            raise AssertionError(
                f"duplicate agent spawn {key!r} — the previous spawn's finalize "
                "did not advance resume state (would otherwise hang this test)"
            )
        self.calls.append(key)
        fn = self._handlers.get(key)
        if fn is None:
            raise AssertionError(
                f"unexpected agent spawn {key!r}; handled: "
                f"{sorted(self._handlers)!r} — a routing regression would "
                "otherwise hang this test"
            )
        await fn(ctx)


async def test_medium_jig_init_auto_cascades_to_sa_mvp(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    agent = _FakeAgent()
    sa_role_seen = {"value": None}

    @agent.handle(role="po-l0", ticket_id="project")
    async def _l0(ctx: AgentSpawnContext) -> None:
        await handle_l0_finalize(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            name="medproj",
            pitch="a medium project exercising the L0–L3 auto-cascade",
            problem="prove the medium init pipeline composes end-to-end",
            audience="jig developers validating the medium init topology",
            non_goals=[
                {
                    "id": "no-edge-cases",
                    "text": "no edge cases",
                    "rationale": "happy-path e2e",
                }
            ],
            author="po-l0",
        )

    @agent.handle(role="po-l1", ticket_id="discovery")
    async def _l1(ctx: AgentSpawnContext) -> None:
        await handle_discovery_finalize(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            project_name="medproj",
            intro="Discovery walk for the medium auto-init e2e.",
            personas=[{"id": "merchant", "description": "integrates jig"}],
            journeys=[
                {
                    "id": "j-merchant-onboarding",
                    "persona_id": "merchant",
                    "title": "Merchant onboarding",
                    "narrative": "Merchant connects their store.",
                    "capability_ids": [_CAP],
                }
            ],
            capability_roster=[
                {
                    "id": _CAP,
                    "description": "Connect store via OAuth",
                    "journey_ids": ["j-merchant-onboarding"],
                }
            ],
            author="po-l1",
        )

    @agent.handle(role="po-l2", ticket_id="suites")
    async def _l2(ctx: AgentSpawnContext) -> None:
        await handle_l2_finalize(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            suites=[
                {
                    "id": "catalog",
                    "title": "Catalog",
                    "summary": "ingestion of catalog data",
                    "capabilities": [_CAP],
                }
            ],
            crosscutting_non_goals=[],
            author="po-l2",
        )

    @agent.handle(role="po-l3", ticket_id="suite-catalog")
    async def _l3(ctx: AgentSpawnContext) -> None:
        await handle_l3_finalize(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=ctx.worktree_path,
            suite_id="catalog",
            intro="Catalog ingestion suite.",
            capabilities=[
                {
                    "id": _CAP,
                    "title": "Connect store via OAuth",
                    "state": "planned",
                    "summary": "Operator authorizes the store; we pull the catalog.",
                    "behaviors": [
                        {
                            "id": "pull-catalog",
                            "description": "After OAuth we pull the catalog",
                            "acceptance_criteria": [
                                "Catalog rows appear in the products collection"
                            ],
                        }
                    ],
                }
            ],
            non_goals=[],
            author="po-l3",
        )

    @agent.handle(role="sa_mvp", ticket_id="architecture")
    async def _sa(ctx: AgentSpawnContext) -> None:
        sa_role_seen["value"] = ctx.role
        proj = ctx.worktree_path
        await handle_arch_set_module(
            project_path=proj,
            module={
                "id": _MODULE_ID,
                "title": "Catalog ingest",
                "summary": "Pulls catalog data and normalizes into products.",
                "implements_capabilities": [_CAP],
                "owns": ["products"],
                "tier_hint": "standard",
                "requires_tracer_bullet": True,
                # CRUD-only ingest: the remaining checklist categories are
                # legitimately N/A so arch_finalize's checklist passes.
                "n_a_categories": [
                    "external_dependencies",
                    "behavioral_contracts",
                    "cross_cutting_compliance",
                ],
                "intent": {
                    "problem": "Own the products collection for ingest.",
                    "simplest_solution": "One module: ingest + owned collection.",
                    "complications_considered": {
                        "scale": None,
                        "concurrency": None,
                        "failure_modes": None,
                        "cross_cutting": None,
                    },
                },
            },
        )
        contracts = ContractsFile.model_validate(
            {
                "spec_version": 1,
                "module": _MODULE_ID,
                "owns": [
                    {
                        "collection": "products",
                        "db": "catalog",
                        "write_access": ["self"],
                        "read_access": [],
                    }
                ],
                "external_dependencies": [],
                "exposes": [],
                "emits": [],
                "integration_ac": [
                    {
                        "capability": _CAP,
                        "must": ["Shopify catalog flows into the products collection"],
                    }
                ],
                "behavioral_contracts": [],
                "data_contracts": [],
                "open_questions": [],
                "change_log": [],
            }
        )
        atomic_write_text(
            module_contracts_path(proj, _MODULE_ID),
            yaml.safe_dump(contracts.model_dump(mode="json"), sort_keys=False),
        )
        await handle_arch_finalize(
            tickets=ctx.tickets,
            threads=ctx.threads,
            bus=ctx.bus,
            project_path=proj,
            summary="medium arch finalized",
            author="sa-mvp",
        )

    # Drive the REAL run_init auto-cascade for a medium project. --profile pins
    # medium up front; no manual /init --proceed, no hand-written artifacts.
    with patch("jig.init_workflow.run_agent", new=agent.run):
        await run_init(name="medproj", force=False, profile_name="medium")

    project = tmp_path / "medproj"
    # The full success-criterion artifact chain landed via the auto-cascade.
    assert (project / "docs" / "brief.md").is_file()
    assert (project / ".jig" / "spec" / "discovery.md").is_file()
    assert (project / ".jig" / "spec" / "suites.yaml").is_file()
    assert (
        project / ".jig" / "spec" / "suites" / "catalog" / "spec.structured.yaml"
    ).is_file()
    assert module_contracts_path(project, _MODULE_ID).is_file()
    # The SA ran as the module-producing role (not the v1 flat sa).
    assert sa_role_seen["value"] == "sa_mvp"
    # The auto-cascade visited each level in order, then the SA — once each.
    assert agent.calls == [
        ("po-l0", "project"),
        ("po-l1", "discovery"),
        ("po-l2", "suites"),
        ("po-l3", "suite-catalog"),
        ("sa_mvp", "architecture"),
    ]
