"""Incremental SA upsert + finalize handlers (Track C MVP commit 2).

The MVP path lands alongside the bones one-shot ``handle_sa_finalize``;
both stay wired so the bones role keeps working while the new
``sa-mvp`` role uses the incremental tools. Each upsert handler is
keyed on the artifact's natural id field; re-setting with the same id
replaces the entry, new ids append. ``arch_finalize`` re-loads, posts
the Handoff to PM, and resolves the architecture ticket.

Validator wiring (behavioral-contract authoring warnings + SA
checklist enforcement) lands in commit 3 — this commit pins the
data-shape behavior of the upsert + finalize plumbing.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jig.sa_incremental_mcp import (
    handle_arch_finalize,
    handle_arch_set_cross_cutting_policy,
    handle_arch_set_data_store,
    handle_arch_set_module,
    handle_arch_set_open_question,
    handle_arch_set_shared_contract,
    handle_module_set_behavioral_contract,
    handle_module_set_data_contract,
    handle_module_set_external_dependency,
    handle_module_set_integration_ac,
    handle_module_set_open_question,
    handle_module_set_owned_collection,
    handle_sa_write_boundaries,
)
from jig.sa_mcp import SA_NEXT_PHASE, SA_TICKET_ID
from jig.spec_loader import (
    architecture_path,
    load_architecture,
    load_module_contracts,
    module_contracts_path,
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
    await tickets.create(
        Ticket(
            id=SA_TICKET_ID,
            work_type=WorkType.BRIEF,
            title="SA — architecture",
            created_by="cli",
        )
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


def _intent_dict(problem: str = "Persist normalized catalog records cleanly") -> dict:
    return {
        "problem": problem,
        "simplest_solution": "Single SQLite table keyed by primary key.",
        "complications_considered": {
            "scale": None,
            "concurrency": None,
            "failure_modes": None,
            "cross_cutting": None,
        },
    }


def _module_dict(module_id: str = "catalog-ingest") -> dict:
    return {
        "id": module_id,
        "title": "Catalog Ingest",
        "summary": "Pulls from external sources and normalizes.",
        "implements_capabilities": ["shopify-connect"],
        "owns": ["products"],
        "tier_hint": "standard",
        "requires_tracer_bullet": False,
        # The MVP checklist (commit 3) requires every category to be
        # addressed or explicitly N/A'd. The minimal-author helper
        # below exercises the happy path without being verbose; mark
        # the categories we don't author here as N/A so the finalize
        # gate doesn't false-flag the upsert/finalize plumbing tests.
        # Per-category enforcement gets its own dedicated tests.
        "n_a_categories": ["behavioral_contracts", "external_dependencies"],
        "intent": _intent_dict(),
    }


# ---- arch_set_module: happy path + idempotent + multi-module --------------


@pytest.mark.asyncio
async def test_arch_set_module_writes_architecture_yaml(wired):
    mid = await handle_arch_set_module(
        project_path=wired["project_path"],
        module=_module_dict(),
    )
    assert mid == "catalog-ingest"
    arch = load_architecture(wired["project_path"])
    assert len(arch.modules) == 1
    assert arch.modules[0].id == "catalog-ingest"


@pytest.mark.asyncio
async def test_arch_set_module_idempotent_replace(wired):
    """Re-setting the same id replaces (not duplicates) the entry."""
    await handle_arch_set_module(
        project_path=wired["project_path"], module=_module_dict()
    )
    revised = _module_dict()
    revised["title"] = "Catalog Ingest (revised)"
    await handle_arch_set_module(
        project_path=wired["project_path"], module=revised
    )
    arch = load_architecture(wired["project_path"])
    assert len(arch.modules) == 1
    assert arch.modules[0].title == "Catalog Ingest (revised)"


@pytest.mark.asyncio
async def test_arch_set_module_multi_module_accumulates(wired):
    """New ids append; the file accumulates multiple modules."""
    await handle_arch_set_module(
        project_path=wired["project_path"], module=_module_dict()
    )
    await handle_arch_set_module(
        project_path=wired["project_path"],
        module=_module_dict(module_id="categorization"),
    )
    arch = load_architecture(wired["project_path"])
    ids = [m.id for m in arch.modules]
    assert "catalog-ingest" in ids
    assert "categorization" in ids


@pytest.mark.asyncio
async def test_arch_set_module_rejects_invalid_payload(wired):
    """Schema validation surfaces as ValueError on the boundary."""
    bad = _module_dict()
    del bad["intent"]
    with pytest.raises(ValueError, match="module does not validate"):
        await handle_arch_set_module(
            project_path=wired["project_path"], module=bad
        )
    # No partial write on validation failure.
    assert not architecture_path(wired["project_path"]).exists()


# ---- arch_set_data_store --------------------------------------------------


@pytest.mark.asyncio
async def test_arch_set_data_store_happy_path(wired):
    sid = await handle_arch_set_data_store(
        project_path=wired["project_path"],
        data_store={
            "id": "main-db",
            "kind": "sqlite",
            "rationale": "bones",
            "accessed_by": ["catalog-ingest"],
        },
    )
    assert sid == "main-db"
    arch = load_architecture(wired["project_path"])
    assert arch.data_stores[0].kind == "sqlite"


@pytest.mark.asyncio
async def test_arch_set_data_store_idempotent_and_multi(wired):
    await handle_arch_set_data_store(
        project_path=wired["project_path"],
        data_store={"id": "main-db", "kind": "sqlite"},
    )
    await handle_arch_set_data_store(
        project_path=wired["project_path"],
        data_store={"id": "search-index", "kind": "opensearch"},
    )
    await handle_arch_set_data_store(
        project_path=wired["project_path"],
        data_store={"id": "main-db", "kind": "postgres"},
    )
    arch = load_architecture(wired["project_path"])
    assert len(arch.data_stores) == 2
    by_id = {ds.id: ds for ds in arch.data_stores}
    assert by_id["main-db"].kind == "postgres"


# ---- arch_set_shared_contract --------------------------------------------


@pytest.mark.asyncio
async def test_arch_set_shared_contract_happy_path(wired):
    sid = await handle_arch_set_shared_contract(
        project_path=wired["project_path"],
        shared_contract={
            "id": "product-shape",
            "type": "data",
            "description": "Normalized product record.",
        },
    )
    assert sid == "product-shape"


# ---- arch_set_cross_cutting_policy ---------------------------------------


@pytest.mark.asyncio
async def test_arch_set_cross_cutting_policy_happy_path(wired):
    pid = await handle_arch_set_cross_cutting_policy(
        project_path=wired["project_path"],
        policy={
            "id": "pii-encrypted-at-rest",
            "polarity": "positive",
            "rule": "All PII must be encrypted at rest.",
            "auto_generates_integration_ac": True,
        },
    )
    assert pid == "pii-encrypted-at-rest"
    arch = load_architecture(wired["project_path"])
    assert arch.cross_cutting_policies[0].auto_generates_integration_ac is True


# ---- arch_set_open_question ----------------------------------------------


@pytest.mark.asyncio
async def test_arch_set_open_question_happy_path(wired):
    qid = await handle_arch_set_open_question(
        project_path=wired["project_path"],
        open_question={
            "id": "q-event-bus",
            "text": "Pick an event bus: Kafka, NATS, or in-process pubsub?",
            "blocking": ["catalog-ingested-event"],
        },
    )
    assert qid == "q-event-bus"


# ---- module_set_owned_collection -----------------------------------------


@pytest.mark.asyncio
async def test_module_set_owned_collection_happy_path(wired):
    cid = await handle_module_set_owned_collection(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        owned_collection={
            "collection": "products",
            "db": "main-db",
            "write_access": ["self"],
            "read_access": ["categorization"],
        },
    )
    assert cid == "products"
    cf = load_module_contracts(wired["project_path"], "catalog-ingest")
    assert cf.module == "catalog-ingest"
    assert cf.owns[0].collection == "products"


@pytest.mark.asyncio
async def test_module_set_owned_collection_idempotent_on_collection_name(wired):
    """OwnedCollection has no id field — keyed by ``collection`` instead."""
    await handle_module_set_owned_collection(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        owned_collection={
            "collection": "products",
            "db": "main-db",
            "write_access": ["self"],
        },
    )
    await handle_module_set_owned_collection(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        owned_collection={
            "collection": "products",
            "db": "main-db",
            "write_access": ["self"],
            "read_access": ["search-api"],
        },
    )
    cf = load_module_contracts(wired["project_path"], "catalog-ingest")
    assert len(cf.owns) == 1
    assert cf.owns[0].read_access == ["search-api"]


@pytest.mark.asyncio
async def test_module_set_owned_collection_creates_module_dir(wired):
    """First upsert against a new module creates the parent dir."""
    target = module_contracts_path(wired["project_path"], "fresh-module")
    assert not target.parent.exists()
    await handle_module_set_owned_collection(
        project_path=wired["project_path"],
        module_id="fresh-module",
        owned_collection={
            "collection": "thing",
            "db": "main-db",
            "write_access": ["self"],
        },
    )
    assert target.parent.is_dir()


# ---- module_set_external_dependency --------------------------------------


@pytest.mark.asyncio
async def test_module_set_external_dependency_happy_path(wired):
    did = await handle_module_set_external_dependency(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        external_dependency={
            "id": "shopify-api",
            "kind": "external_http",
            "rate_limit": "2 req/sec per shop",
            "auth": "oauth2 token per shop",
        },
    )
    assert did == "shopify-api"


# ---- module_set_integration_ac (keyed by capability) --------------------


@pytest.mark.asyncio
async def test_module_set_integration_ac_keyed_by_capability(wired):
    """Re-setting the same capability replaces the MUST list."""
    await handle_module_set_integration_ac(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        integration_ac={
            "capability": "shopify-connect",
            "must": ["OAuth tokens encrypted at rest"],
        },
    )
    await handle_module_set_integration_ac(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        integration_ac={
            "capability": "shopify-connect",
            "must": [
                "OAuth tokens encrypted at rest",
                "Token refresh handled before expiry",
            ],
        },
    )
    cf = load_module_contracts(wired["project_path"], "catalog-ingest")
    assert len(cf.integration_ac) == 1
    assert len(cf.integration_ac[0].must) == 2


# ---- module_set_behavioral_contract --------------------------------------


@pytest.mark.asyncio
async def test_module_set_behavioral_contract_returns_id_and_warnings(wired):
    """Response shape is structured so the agent sees warnings inline."""
    result = await handle_module_set_behavioral_contract(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        behavioral_contract={
            "id": "ingest-batch-atomicity",
            "applies_to": {
                "capability": "normalize-skus",
                "module": "catalog-ingest",
            },
            "precondition": "batch_id refers to an in-progress ingestion run.",
            "postcondition": "Either all rows persist or none persist.",
            "intent": _intent_dict(),
        },
    )
    assert result["id"] == "ingest-batch-atomicity"
    # Commit 2: warnings list is empty (validator wiring lands in commit 3).
    assert isinstance(result["warnings"], list)


# ---- module_set_data_contract --------------------------------------------


@pytest.mark.asyncio
async def test_module_set_data_contract_happy_path(wired):
    did = await handle_module_set_data_contract(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        data_contract={
            "id": "product-record",
            "type": "data",
            "description": "Normalized product record persisted to the products collection.",
            "fields": {"id": "str", "title": "str", "price_cents": "int"},
            "intent": _intent_dict(),
        },
    )
    assert did == "product-record"


# ---- module_set_open_question --------------------------------------------


@pytest.mark.asyncio
async def test_module_set_open_question_happy_path(wired):
    qid = await handle_module_set_open_question(
        project_path=wired["project_path"],
        module_id="catalog-ingest",
        open_question={
            "id": "q-partial-failure",
            "text": "If 90% succeed and 10% fail, commit or rollback?",
        },
    )
    assert qid == "q-partial-failure"


# ---- arch_finalize: happy + edges ---------------------------------------


async def _author_minimal(project_path: Path) -> None:
    """Author one module + one owned collection so finalize has something to validate."""
    await handle_arch_set_module(
        project_path=project_path, module=_module_dict()
    )
    await handle_module_set_owned_collection(
        project_path=project_path,
        module_id="catalog-ingest",
        owned_collection={
            "collection": "products",
            "db": "main-db",
            "write_access": ["self"],
        },
    )
    await handle_module_set_integration_ac(
        project_path=project_path,
        module_id="catalog-ingest",
        integration_ac={
            "capability": "shopify-connect",
            "must": ["Catalog rows land in products collection"],
        },
    )


@pytest.mark.asyncio
async def test_arch_finalize_resolves_ticket(wired):
    await _author_minimal(wired["project_path"])
    await handle_arch_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        summary="MVP-SA finalize",
        author="sa-mvp",
    )
    t = await wired["tickets"].get(SA_TICKET_ID)
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_arch_finalize_emits_handoff_to_pm(wired):
    await _author_minimal(wired["project_path"])
    await handle_arch_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        summary="MVP-SA finalize",
        author="sa-mvp",
    )
    entries = await wired["threads"].for_ticket(SA_TICKET_ID)
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    assert handoffs[0].phase == SA_NEXT_PHASE
    assert ".jig/spec/architecture.yaml" in handoffs[0].outputs
    assert (
        ".jig/spec/modules/catalog-ingest/contracts.yaml"
        in handoffs[0].outputs
    )


@pytest.mark.asyncio
async def test_arch_finalize_lists_every_authored_module_in_handoff(wired):
    """Multi-module finalize cites every authored contracts.yaml."""
    await handle_arch_set_module(
        project_path=wired["project_path"], module=_module_dict()
    )
    await handle_arch_set_module(
        project_path=wired["project_path"],
        module=_module_dict(module_id="categorization"),
    )
    for mid in ("catalog-ingest", "categorization"):
        await handle_module_set_owned_collection(
            project_path=wired["project_path"],
            module_id=mid,
            owned_collection={
                "collection": f"{mid}-collection",
                "db": "main-db",
                "write_access": ["self"],
            },
        )
        await handle_module_set_integration_ac(
            project_path=wired["project_path"],
            module_id=mid,
            integration_ac={
                "capability": "shopify-connect",
                "must": ["Lands in collection"],
            },
        )
    await handle_arch_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        summary="multi-module finalize",
        author="sa-mvp",
    )
    entries = await wired["threads"].for_ticket(SA_TICKET_ID)
    handoff = next(e for e in entries if e.kind == "handoff")
    assert ".jig/spec/modules/catalog-ingest/contracts.yaml" in handoff.outputs
    assert ".jig/spec/modules/categorization/contracts.yaml" in handoff.outputs


@pytest.mark.asyncio
async def test_arch_finalize_raises_when_no_modules(wired):
    """Finalize with an empty architecture is a programmer error."""
    with pytest.raises(ValueError, match="no modules"):
        await handle_arch_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            summary="empty",
            author="sa-mvp",
        )


@pytest.mark.asyncio
async def test_arch_finalize_raises_on_orphan_contracts(wired):
    """A contracts.yaml referencing an unknown module breaks dispatch."""
    await handle_arch_set_module(
        project_path=wired["project_path"], module=_module_dict()
    )
    # Author contracts under a module id NOT present in architecture.yaml.
    await handle_module_set_owned_collection(
        project_path=wired["project_path"],
        module_id="ghost-module",
        owned_collection={
            "collection": "things",
            "db": "main-db",
            "write_access": ["self"],
        },
    )
    with pytest.raises(ValueError, match="don't appear in architecture"):
        await handle_arch_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            summary="orphan",
            author="sa-mvp",
        )


# ---- sa_write_boundaries --------------------------------------------------


@pytest.mark.asyncio
async def test_sa_write_boundaries_happy_path(wired):
    from jig.spec_loader import module_boundaries_path

    mid = await handle_sa_write_boundaries(
        project_path=wired["project_path"],
        boundaries={
            "module": "catalog-ingest",
            "internal": {"forbidden_modules": ["billing"]},
            "external": {"allowed": ["httpx"], "forbidden": ["requests"]},
        },
    )
    assert mid == "catalog-ingest"
    path = module_boundaries_path(wired["project_path"], "catalog-ingest")
    assert path.is_file()
    import yaml

    from jig.schemas.arch import BoundariesFile

    bf = BoundariesFile.model_validate(yaml.safe_load(path.read_text()))
    assert bf.internal.forbidden_modules == ["billing"]
    assert bf.external.forbidden == ["requests"]


@pytest.mark.asyncio
async def test_sa_write_boundaries_invalid_raises(wired):
    with pytest.raises(ValueError, match="boundaries does not validate"):
        await handle_sa_write_boundaries(
            project_path=wired["project_path"],
            boundaries={"module": "Catalog Ingest"},  # non-kebab module
        )


@pytest.mark.asyncio
async def test_sa_write_boundaries_rewrite_replaces(wired):
    from jig.spec_loader import module_boundaries_path

    for forbidden in (["billing"], ["auth"]):
        await handle_sa_write_boundaries(
            project_path=wired["project_path"],
            boundaries={
                "module": "catalog-ingest",
                "internal": {"forbidden_modules": forbidden},
            },
        )
    import yaml

    from jig.schemas.arch import BoundariesFile

    bf = BoundariesFile.model_validate(
        yaml.safe_load(
            module_boundaries_path(wired["project_path"], "catalog-ingest").read_text()
        )
    )
    assert bf.internal.forbidden_modules == ["auth"]  # last write wins


# ---- arch_finalize generates boundary rules (step 4) ----------------------


def _write_config(project_path: Path, name: str = "my-ats") -> None:
    import yaml

    (project_path / ".jig" / "config.yaml").write_text(
        yaml.safe_dump({"project": {"name": name, "id": name, "path": str(project_path)}})
    )


@pytest.mark.asyncio
async def test_arch_finalize_generates_boundary_rules(wired):
    project_path = wired["project_path"]
    await _author_minimal(project_path)
    _write_config(project_path)
    await handle_sa_write_boundaries(
        project_path=project_path,
        boundaries={
            "module": "catalog-ingest",
            "external": {"forbidden": ["requests"]},
        },
    )
    await handle_arch_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=project_path,
        summary="finalize with boundaries",
        author="sa-mvp",
    )
    rule_file = (
        project_path / ".jig" / "rules" / "semgrep" / "boundaries" / "catalog-ingest.yml"
    )
    assert rule_file.is_file()


@pytest.mark.asyncio
async def test_arch_finalize_raises_on_bad_boundary(wired):
    project_path = wired["project_path"]
    await _author_minimal(project_path)
    _write_config(project_path)
    await handle_sa_write_boundaries(
        project_path=project_path,
        boundaries={
            "module": "catalog-ingest",
            "internal": {"forbidden_modules": ["ghost"]},  # unknown module
        },
    )
    with pytest.raises(ValueError, match="unknown module"):
        await handle_arch_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=project_path,
            summary="finalize with bad boundary",
            author="sa-mvp",
        )
