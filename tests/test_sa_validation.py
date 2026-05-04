"""Behavioral-contract authoring quality + module checklist (Track C MVP commit 3).

Both validators are mechanical (no LLM): per
``docs/sa-architecture/design.md`` §"Behavioral contracts" and
§"The SA checklist". They return data (warnings list / missing-set);
callers decide whether to raise or surface inline.
"""
from __future__ import annotations

import pytest

from jig.intent import Intent
from jig.sa_validation import (
    CHECKLIST_CATEGORIES,
    validate_behavioral_contract,
    validate_module_checklist,
)
from jig.schemas.arch import (
    BehavioralContract,
    ContractsFile,
    ExternalDependency,
    IntegrationAcceptance,
    Module,
    OwnedCollection,
)


def _intent() -> Intent:
    return Intent(
        problem="something concrete this artifact is meant to solve",
        simplest_solution="the simplest dumb thing that would address it",
    )


# ---- validate_behavioral_contract ----------------------------------------


def test_validate_bc_clean_contract_returns_no_warnings() -> None:
    """A well-authored contract earns the empty list."""
    bc = BehavioralContract(
        id="ingest-batch-atomicity",
        applies_to={"module": "catalog-ingest", "capability": "normalize-skus"},
        precondition=(
            "batch_id refers to an in-progress row in ingestion_runs."
        ),
        postcondition=(
            "Either every product persists and batch.status is completed, "
            "or none persist and batch.status is failed."
        ),
        intent=_intent(),
    )
    assert validate_behavioral_contract(bc) == []


def test_validate_bc_warns_on_no_constraint() -> None:
    """Postcondition + side_effect_required both missing = no constraint."""
    bc = BehavioralContract(
        id="vague-thing",
        scope="cross_cutting",
        precondition="some long enough precondition prose to clear the floor",
        intent=_intent(),
    )
    warnings = validate_behavioral_contract(bc)
    assert any("not constraining anything" in w or "no postcondition" in w for w in warnings)


def test_validate_bc_warns_on_no_anchor() -> None:
    """applies_to AND scope both None = no anchor."""
    bc = BehavioralContract(
        id="floating-thing",
        postcondition=(
            "After the call returns, the products collection has "
            "exactly the input rows."
        ),
        intent=_intent(),
    )
    warnings = validate_behavioral_contract(bc)
    assert any("anchor" in w or "applies_to" in w for w in warnings)


def test_validate_bc_warns_on_thin_precondition() -> None:
    """A < 30-char precondition is almost certainly boilerplate."""
    bc = BehavioralContract(
        id="thin-pre",
        applies_to={"module": "x"},
        precondition="ok",  # too short
        postcondition=(
            "After the call returns the audit row exists with the "
            "right tenant and timestamp."
        ),
        intent=_intent(),
    )
    warnings = validate_behavioral_contract(bc)
    assert any("'precondition'" in w for w in warnings)


def test_validate_bc_warns_on_thin_postcondition() -> None:
    bc = BehavioralContract(
        id="thin-post",
        applies_to={"module": "x"},
        precondition="precondition long enough to satisfy the floor",
        postcondition="all good",  # too short
        intent=_intent(),
    )
    warnings = validate_behavioral_contract(bc)
    assert any("'postcondition'" in w for w in warnings)


def test_validate_bc_side_effect_required_satisfies_constraint_check() -> None:
    """side_effect_required is an alternative to postcondition."""
    bc = BehavioralContract(
        id="audit-side-effect",
        scope="cross_cutting",
        side_effect_required=(
            "Every tenant-initiated state change appends to audit_log "
            "with (tenant_id, action, timestamp)."
        ),
        intent=_intent(),
    )
    warnings = validate_behavioral_contract(bc)
    # No "not constraining anything" warning when side_effect_required is set.
    assert not any("constraining" in w for w in warnings)


# ---- validate_module_checklist -------------------------------------------


def _module(module_id: str = "m", n_a: list[str] | None = None) -> Module:
    return Module(
        id=module_id,
        title="m",
        summary="some module that exists for the test",
        intent=_intent(),
        n_a_categories=list(n_a or []),
    )


def test_checklist_categories_match_design() -> None:
    """Spec the canonical category set so future drift is visible."""
    assert set(CHECKLIST_CATEGORIES) == {
        "ownership",
        "external_dependencies",
        "integration_ac",
        "behavioral_contracts",
        "cross_cutting_compliance",
    }


def test_checklist_no_contracts_returns_every_category_missing() -> None:
    m = _module()
    missing = validate_module_checklist(m, None)
    assert missing == set(CHECKLIST_CATEGORIES)


def test_checklist_n_a_categories_skips_check() -> None:
    """Explicit N/A acknowledges the category without authoring it."""
    m = _module(n_a=list(CHECKLIST_CATEGORIES))
    missing = validate_module_checklist(m, None)
    assert missing == set()


def test_checklist_ownership_addressed_by_owns_entry() -> None:
    m = _module(
        n_a=[
            "external_dependencies",
            "integration_ac",
            "behavioral_contracts",
            "cross_cutting_compliance",
        ]
    )
    cf = ContractsFile(
        module="m",
        owns=[OwnedCollection(collection="things", db="main-db")],
    )
    assert validate_module_checklist(m, cf) == set()


def test_checklist_external_dependencies_addressed_by_entry() -> None:
    m = _module(
        n_a=[
            "ownership",
            "integration_ac",
            "behavioral_contracts",
            "cross_cutting_compliance",
        ]
    )
    cf = ContractsFile(
        module="m",
        external_dependencies=[ExternalDependency(id="shopify", kind="external_http")],
    )
    assert validate_module_checklist(m, cf) == set()


def test_checklist_integration_ac_addressed_by_entry() -> None:
    m = _module(
        n_a=[
            "ownership",
            "external_dependencies",
            "behavioral_contracts",
            "cross_cutting_compliance",
        ]
    )
    cf = ContractsFile(
        module="m",
        integration_ac=[
            IntegrationAcceptance(capability="cap", must=["does X"])
        ],
    )
    assert validate_module_checklist(m, cf) == set()


def test_checklist_behavioral_contracts_addressed_by_entry() -> None:
    m = _module(
        n_a=[
            "ownership",
            "external_dependencies",
            "integration_ac",
            "cross_cutting_compliance",
        ]
    )
    cf = ContractsFile(
        module="m",
        behavioral_contracts=[
            BehavioralContract(
                id="x",
                applies_to={"module": "m"},
                postcondition=(
                    "After the call the row exists in the right table."
                ),
                intent=_intent(),
            )
        ],
    )
    assert validate_module_checklist(m, cf) == set()


def test_checklist_partial_authoring_reports_just_the_gaps() -> None:
    """Mix-and-match: some categories authored, some N/A, some missing."""
    m = _module(n_a=["external_dependencies", "behavioral_contracts"])
    cf = ContractsFile(
        module="m",
        owns=[OwnedCollection(collection="things", db="main-db")],
    )
    missing = validate_module_checklist(m, cf)
    # ownership authored; external_dependencies + behavioral_contracts N/A
    # Remaining: integration_ac + cross_cutting_compliance.
    assert missing == {"integration_ac", "cross_cutting_compliance"}


# ---- arch_finalize behavior change (commit 3) ---------------------------


@pytest.mark.asyncio
async def test_arch_finalize_raises_on_unmet_checklist(tmp_path):
    """Finalize must raise when any module has unmet, non-N/A categories."""
    from jig.sa_incremental_mcp import (
        handle_arch_finalize,
        handle_arch_set_module,
        handle_module_set_owned_collection,
    )
    from jig.sa_mcp import SA_TICKET_ID
    from jig.store.bus import MessageBus
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket, WorkType

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
            title="SA",
            created_by="cli",
        )
    )
    # Author a module with NO n_a_categories — every category will be missing.
    await handle_arch_set_module(
        project_path=tmp_path,
        module={
            "id": "thin-module",
            "title": "Thin module",
            "summary": "Has a contracts file but no integration AC at all.",
            "implements_capabilities": [],
            "owns": [],
            "intent": _intent().model_dump(),
        },
    )
    # Author owned collection so contracts.yaml exists on disk.
    await handle_module_set_owned_collection(
        project_path=tmp_path,
        module_id="thin-module",
        owned_collection={
            "collection": "things",
            "db": "main-db",
            "write_access": ["self"],
        },
    )
    with pytest.raises(ValueError, match="unmet checklist categories"):
        await handle_arch_finalize(
            tickets=tickets,
            threads=threads,
            bus=bus,
            project_path=tmp_path,
            summary="thin",
            author="sa-mvp",
        )


@pytest.mark.asyncio
async def test_arch_finalize_posts_bc_warnings_as_note(tmp_path):
    """Behavioral-contract warnings end up on the architecture ticket as a Note."""
    from jig.sa_incremental_mcp import (
        handle_arch_finalize,
        handle_arch_set_module,
        handle_module_set_behavioral_contract,
        handle_module_set_integration_ac,
        handle_module_set_owned_collection,
    )
    from jig.sa_mcp import SA_TICKET_ID
    from jig.store.bus import MessageBus
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    from jig.ticket import Ticket, WorkType

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
            title="SA",
            created_by="cli",
        )
    )
    await handle_arch_set_module(
        project_path=tmp_path,
        module={
            "id": "m",
            "title": "M",
            "summary": "module under test",
            "implements_capabilities": ["cap"],
            "owns": [],
            # Don't N/A behavioral_contracts — we author one (with a flaw)
            # so we exercise the warning path.
            "n_a_categories": ["external_dependencies"],
            "intent": _intent().model_dump(),
        },
    )
    await handle_module_set_owned_collection(
        project_path=tmp_path,
        module_id="m",
        owned_collection={
            "collection": "x",
            "db": "main-db",
            "write_access": ["self"],
        },
    )
    await handle_module_set_integration_ac(
        project_path=tmp_path,
        module_id="m",
        integration_ac={"capability": "cap", "must": ["does the thing"]},
    )
    # Author a deliberately-flawed BC: no anchor, no postcondition.
    await handle_module_set_behavioral_contract(
        project_path=tmp_path,
        module_id="m",
        behavioral_contract={
            "id": "flawed-bc",
            "intent": _intent().model_dump(),
        },
    )
    await handle_arch_finalize(
        tickets=tickets,
        threads=threads,
        bus=bus,
        project_path=tmp_path,
        summary="bc-warnings test",
        author="sa-mvp",
    )
    entries = await threads.for_ticket(SA_TICKET_ID)
    notes = [e for e in entries if e.kind == "note"]
    assert notes, "expected a Note carrying the BC authoring warnings"
    text = notes[0].text
    assert "flawed-bc" in text
    assert "anchor" in text or "constraining" in text
