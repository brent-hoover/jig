"""SA MCP tool handlers + arch path helpers (Tracks C1+C2, bones).

Mirrors the structure of ``tests/test_po_l3_mcp.py`` — the v2 SA finalize
follows the same one-shot finalize pattern as L3.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.sa_mcp import (
    SA_NEXT_PHASE,
    SA_TICKET_ID,
    handle_sa_finalize,
)
from jig.schemas.arch import Architecture, ContractsFile
from jig.spec_loader import (
    architecture_path,
    load_architecture,
    load_module_contracts,
    module_contracts_path,
    module_dir,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


# ---- fixtures -------------------------------------------------------------


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    arch_ticket = Ticket(
        id=SA_TICKET_ID,
        work_type=WorkType.BRIEF,
        title="SA — architecture",
        created_by="cli",
    )
    await tickets.create(arch_ticket)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
        "spec_dir": spec_dir,
    }


def _intent_dict(
    *,
    problem: str = "Persist normalized catalog records",
    simplest: str = "Single SQLite table keyed by (customer_id, sku)",
) -> dict:
    """A minimum-viable Intent matching the bones SA prompt's expectations."""
    return {
        "problem": problem,
        "simplest_solution": simplest,
        "complications_considered": {
            "scale": None,
            "concurrency": None,
            "failure_modes": None,
            "cross_cutting": None,
        },
    }


def _arch_dict(
    *,
    module_id: str = "catalog-ingest",
    capability_id: str = "shopify-connect",
) -> dict:
    """Minimum-viable Architecture: 1 data store, 1 module with intent."""
    return {
        "spec_version": 1,
        "data_stores": [
            {
                "id": "main-db",
                "kind": "sqlite",
                "rationale": "Bones project; single-process, file-backed.",
                "accessed_by": [module_id],
            }
        ],
        "modules": [
            {
                "id": module_id,
                "title": "Catalog Ingest",
                "summary": "Pulls from external sources and normalizes.",
                "implements_capabilities": [capability_id],
                "owns": ["products"],
                "tier_hint": "standard",
                "requires_tracer_bullet": False,
                "intent": _intent_dict(),
            }
        ],
    }


def _contracts_dict(
    *,
    module_id: str = "catalog-ingest",
    capability_id: str = "shopify-connect",
) -> dict:
    """Minimum-viable ContractsFile: 1 owned collection, 1 integration AC."""
    return {
        "spec_version": 1,
        "module": module_id,
        "owns": [
            {
                "collection": "products",
                "db": "main-db",
                "write_access": ["self"],
                "read_access": [],
            }
        ],
        "integration_ac": [
            {
                "capability": capability_id,
                "must": [
                    "Writes normalized product records to the products collection only",
                ],
            }
        ],
    }


# ---- path helpers ---------------------------------------------------------


def test_architecture_path_helper(tmp_path: Path):
    p = architecture_path(tmp_path)
    assert p == tmp_path / ".jig" / "spec" / "architecture.yaml"


def test_module_dir_helper(tmp_path: Path):
    p = module_dir(tmp_path, "catalog-ingest")
    assert p == tmp_path / ".jig" / "spec" / "modules" / "catalog-ingest"


def test_module_contracts_path_helper(tmp_path: Path):
    p = module_contracts_path(tmp_path, "catalog-ingest")
    assert p == (
        tmp_path / ".jig" / "spec" / "modules" / "catalog-ingest" / "contracts.yaml"
    )


# ---- loaders --------------------------------------------------------------


def test_load_architecture_missing_raises(tmp_path: Path):
    """Bones SA writes from scratch; absence is a hard error, not empty."""
    with pytest.raises(FileNotFoundError):
        load_architecture(tmp_path)


def test_load_architecture_round_trip(tmp_path: Path):
    arch_dict = _arch_dict()
    path = architecture_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(arch_dict))
    loaded = load_architecture(tmp_path)
    assert isinstance(loaded, Architecture)
    assert loaded.modules[0].id == "catalog-ingest"
    # Intent survives the round-trip — load_architecture validates it
    # against the schema rather than treating it as opaque.
    assert loaded.modules[0].intent.problem.startswith("Persist normalized")


def test_load_module_contracts_missing_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_module_contracts(tmp_path, "ghost-module")


def test_load_module_contracts_round_trip(tmp_path: Path):
    contracts_dict = _contracts_dict()
    path = module_contracts_path(tmp_path, "catalog-ingest")
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(contracts_dict))
    loaded = load_module_contracts(tmp_path, "catalog-ingest")
    assert isinstance(loaded, ContractsFile)
    assert loaded.module == "catalog-ingest"
    assert loaded.integration_ac[0].capability == "shopify-connect"


# ---- sa_finalize: happy path ---------------------------------------------


@pytest.mark.asyncio
async def test_sa_finalize_writes_architecture_yaml(wired):
    await handle_sa_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        architecture=_arch_dict(),
        module_contracts=_contracts_dict(),
        author="sa-v2",
    )
    arch_file = architecture_path(wired["project_path"])
    assert arch_file.is_file()
    data = yaml.safe_load(arch_file.read_text())
    assert data["spec_version"] == 1
    assert data["data_stores"][0]["kind"] == "sqlite"
    assert data["modules"][0]["id"] == "catalog-ingest"
    assert data["modules"][0]["intent"]["problem"]


@pytest.mark.asyncio
async def test_sa_finalize_writes_module_contracts_yaml(wired):
    await handle_sa_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        architecture=_arch_dict(),
        module_contracts=_contracts_dict(),
        author="sa-v2",
    )
    contracts_file = module_contracts_path(wired["project_path"], "catalog-ingest")
    assert contracts_file.is_file()
    data = yaml.safe_load(contracts_file.read_text())
    assert data["module"] == "catalog-ingest"
    assert data["owns"][0]["collection"] == "products"
    assert data["integration_ac"][0]["capability"] == "shopify-connect"
    assert data["integration_ac"][0]["must"]


@pytest.mark.asyncio
async def test_sa_finalize_resolves_ticket(wired):
    await handle_sa_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        architecture=_arch_dict(),
        module_contracts=_contracts_dict(),
        author="sa-v2",
    )
    t = await wired["tickets"].get(SA_TICKET_ID)
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_sa_finalize_emits_handoff_to_pm(wired):
    await handle_sa_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        architecture=_arch_dict(),
        module_contracts=_contracts_dict(),
        author="sa-v2",
    )
    entries = await wired["threads"].for_ticket(SA_TICKET_ID)
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    h = handoffs[0]
    # SA v2 hands off to PM next (Track F lands the planner).
    assert h.phase == SA_NEXT_PHASE == "pm"
    assert ".jig/spec/architecture.yaml" in h.outputs
    assert ".jig/spec/modules/catalog-ingest/contracts.yaml" in h.outputs


# ---- sa_finalize: validation rejections ----------------------------------


@pytest.mark.asyncio
async def test_sa_finalize_rejects_module_intent_missing(wired):
    """Module.intent is required by the v2 schema — bones can't skip it."""
    bad = _arch_dict()
    del bad["modules"][0]["intent"]
    with pytest.raises(ValueError, match="architecture does not validate"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=bad,
            module_contracts=_contracts_dict(),
            author="sa-v2",
        )
    # Neither artifact lands when the validator rejects the input.
    assert not architecture_path(wired["project_path"]).exists()
    assert not module_contracts_path(wired["project_path"], "catalog-ingest").exists()


@pytest.mark.asyncio
async def test_sa_finalize_rejects_orphan_contracts(wired):
    """contracts.module must reference a module id in architecture."""
    arch = _arch_dict(module_id="catalog-ingest")
    contracts = _contracts_dict(module_id="ghost-module")
    with pytest.raises(ValueError, match="does not match any module id"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=arch,
            module_contracts=contracts,
            author="sa-v2",
        )
    assert not architecture_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_sa_finalize_rejects_no_data_stores(wired):
    """Bones architecture must have at least one data store."""
    arch = _arch_dict()
    arch["data_stores"] = []
    with pytest.raises(ValueError, match="at least one data_store"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=arch,
            module_contracts=_contracts_dict(),
            author="sa-v2",
        )


@pytest.mark.asyncio
async def test_sa_finalize_rejects_no_modules(wired):
    """Module-link validation runs first — a no-modules arch always orphans
    the contracts. Either error is acceptable as long as no artifacts land
    and the message is actionable."""
    arch = _arch_dict()
    arch["modules"] = []
    with pytest.raises(ValueError):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=arch,
            module_contracts=_contracts_dict(),
            author="sa-v2",
        )
    assert not architecture_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_sa_finalize_rejects_no_owned_collection(wired):
    contracts = _contracts_dict()
    contracts["owns"] = []
    with pytest.raises(ValueError, match="at least one owned collection"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=_arch_dict(),
            module_contracts=contracts,
            author="sa-v2",
        )


@pytest.mark.asyncio
async def test_sa_finalize_rejects_no_integration_ac(wired):
    contracts = _contracts_dict()
    contracts["integration_ac"] = []
    with pytest.raises(ValueError, match="at least one\\s+integration_ac"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=_arch_dict(),
            module_contracts=contracts,
            author="sa-v2",
        )


@pytest.mark.asyncio
async def test_sa_finalize_rejects_invalid_module_id(wired):
    """Empty module id fails Pydantic min_length on Module before the bones
    minimums even run — proves the schema layer catches malformed input."""
    arch = _arch_dict()
    arch["modules"][0]["id"] = ""
    with pytest.raises(ValueError, match="architecture does not validate"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture=arch,
            module_contracts=_contracts_dict(),
            author="sa-v2",
        )


@pytest.mark.asyncio
async def test_sa_finalize_rejects_non_dict_inputs(wired):
    with pytest.raises(ValueError, match="architecture must be a dict"):
        await handle_sa_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            architecture="not-a-dict",
            module_contracts=_contracts_dict(),
            author="sa-v2",
        )


# ---- sa_finalize: idempotency --------------------------------------------


@pytest.mark.asyncio
async def test_sa_finalize_idempotent_overwrite(wired):
    """Re-running SA replaces both artifacts cleanly.

    Operator may revise the architecture and re-finalize; the artifacts
    must reflect the latest call. Mirrors the L3 finalize idempotency
    contract.
    """
    await handle_sa_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        architecture=_arch_dict(),
        module_contracts=_contracts_dict(),
        author="sa-v2",
    )
    # Reactivate so the second call's resolve-after-handoff doesn't no-op.
    await wired["tickets"].update(SA_TICKET_ID, status=TicketStatus.IN_PROGRESS)
    second_arch = _arch_dict()
    second_arch["modules"][0]["summary"] = "Refined: pulls + dedupes."
    await handle_sa_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        architecture=second_arch,
        module_contracts=_contracts_dict(),
        author="sa-v2",
    )
    data = yaml.safe_load(architecture_path(wired["project_path"]).read_text())
    assert data["modules"][0]["summary"] == "Refined: pulls + dedupes."
