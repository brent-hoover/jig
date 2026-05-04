"""Incremental SA authoring MCP tool handlers (Track C MVP).

The bones SA path (``jig.sa_mcp``) exposes a single one-shot
``sa_finalize`` that takes a complete ``Architecture`` +
``ContractsFile`` payload. That works for a one-module tracer-bullet
project but doesn't support the design's discovery-loop per
``docs/sa-architecture/design.md`` §"SA workflow — discovery loop":
the SA walks modules + integration boundaries one by one, accumulating
contracts as the operator confirms each piece. The MVP path lands the
incremental authoring surface alongside the bones one-shot — the
bones role stays intact; the new ``sa-mvp`` role uses these tools.

Each upsert handler is idempotent (re-set with the same id replaces
the entry; new id appends), keyed on the artifact's natural id field.
That mirrors the L1 PO's ``discovery_add_*`` / ``discovery_set_*``
pattern (``po_l1_mcp.py``) so the agent's mental model is uniform:
"set this entry; the file accumulates."

Authoring tools (split across architecture.yaml and per-module
contracts.yaml):

architecture.yaml — project level
    arch_set_module
    arch_set_data_store
    arch_set_shared_contract
    arch_set_cross_cutting_policy
    arch_set_open_question

modules/<m>/contracts.yaml — per-module
    module_set_owned_collection
    module_set_external_dependency
    module_set_integration_ac (keyed by capability)
    module_set_behavioral_contract
    module_set_data_contract
    module_set_open_question

arch_finalize finalizes both: re-validates against the schemas,
runs the SA-checklist enforcer (Deliverable 3), posts the same
Handoff the bones path posts, resolves the ticket via the shared
``resolve_after_handoff``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError

from jig.handoff_resolve import resolve_after_handoff
from jig.sa_mcp import SA_NEXT_PHASE, SA_TICKET_ID
from jig.schemas.arch import (
    Architecture,
    BehavioralContract,
    ContractsFile,
    CrossCuttingPolicy,
    DataContract,
    DataStore,
    ExternalDependency,
    IntegrationAcceptance,
    Module,
    OpenQuestion,
    OwnedCollection,
    SharedContract,
)
from jig.spec_loader import (
    load_architecture,
    load_module_contracts,
    save_architecture,
    save_module_contracts,
)
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff


__all__ = [
    "handle_arch_finalize",
    "handle_arch_set_cross_cutting_policy",
    "handle_arch_set_data_store",
    "handle_arch_set_module",
    "handle_arch_set_open_question",
    "handle_arch_set_shared_contract",
    "handle_module_set_behavioral_contract",
    "handle_module_set_data_contract",
    "handle_module_set_external_dependency",
    "handle_module_set_integration_ac",
    "handle_module_set_open_question",
    "handle_module_set_owned_collection",
]


# ---- shared helpers -------------------------------------------------------


def _load_or_init_arch(project_path: Path) -> Architecture:
    """Load architecture.yaml or return a fresh empty one.

    Centralized so every upsert handler can author against the same
    starting state — empty list everywhere, spec_version=1 — without
    each handler reimplementing the absent-file branch.
    """
    try:
        return load_architecture(project_path)
    except FileNotFoundError:
        return Architecture()


def _load_or_init_contracts(
    project_path: Path, module_id: str
) -> ContractsFile:
    """Load module contracts or return a fresh empty file scoped to module_id.

    The empty default carries ``module=module_id`` so subsequent
    upserts see the right scoping without each handler having to
    re-set it. ``module`` is required by ``ContractsFile`` so the empty
    default can't omit it.
    """
    try:
        return load_module_contracts(project_path, module_id)
    except FileNotFoundError:
        return ContractsFile(module=module_id)


def _replace_or_append(items: list, new_item: Any, *, id_field: str = "id") -> list:
    """Replace items[i] where getattr(items[i], id_field) == new_item.id.

    Common upsert primitive — used by every handler. Returns a new
    list rather than mutating in place so callers can't accidentally
    reuse a mutable reference across the load + save round-trip.
    """
    new_id = getattr(new_item, id_field)
    return [
        item for item in items if getattr(item, id_field) != new_id
    ] + [new_item]


def _coerce(model_cls: type, raw: Any, *, kind: str) -> Any:
    """Coerce a dict (or instance) into a Pydantic model of model_cls.

    Re-raises ``ValidationError`` as ``ValueError`` so the MCP error
    surface is uniform across all upsert handlers — agents see one
    error type rather than two depending on whether the failure was at
    coerce time or schema time.
    """
    if isinstance(raw, model_cls):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(
            f"{kind} must be a dict or {model_cls.__name__}, got "
            f"{type(raw).__name__}"
        )
    try:
        return model_cls.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"{kind} does not validate: {e}") from e


# ---- architecture.yaml upserts -------------------------------------------


async def handle_arch_set_module(
    *, project_path: Path, module: Any
) -> str:
    """Upsert one Module entry into architecture.yaml.

    Returns the module id so the agent can immediately reference the
    module in subsequent ``module_set_*`` calls without re-reading the
    file.
    """
    m = _coerce(Module, module, kind="module")
    arch = _load_or_init_arch(project_path)
    arch.modules = _replace_or_append(arch.modules, m)
    save_architecture(project_path, arch)
    return m.id


async def handle_arch_set_data_store(
    *, project_path: Path, data_store: Any
) -> str:
    ds = _coerce(DataStore, data_store, kind="data_store")
    arch = _load_or_init_arch(project_path)
    arch.data_stores = _replace_or_append(arch.data_stores, ds)
    save_architecture(project_path, arch)
    return ds.id


async def handle_arch_set_shared_contract(
    *, project_path: Path, shared_contract: Any
) -> str:
    sc = _coerce(SharedContract, shared_contract, kind="shared_contract")
    arch = _load_or_init_arch(project_path)
    arch.shared_contracts = _replace_or_append(arch.shared_contracts, sc)
    save_architecture(project_path, arch)
    return sc.id


async def handle_arch_set_cross_cutting_policy(
    *, project_path: Path, policy: Any
) -> str:
    p = _coerce(CrossCuttingPolicy, policy, kind="cross_cutting_policy")
    arch = _load_or_init_arch(project_path)
    arch.cross_cutting_policies = _replace_or_append(
        arch.cross_cutting_policies, p
    )
    save_architecture(project_path, arch)
    return p.id


async def handle_arch_set_open_question(
    *, project_path: Path, open_question: Any
) -> str:
    q = _coerce(OpenQuestion, open_question, kind="open_question")
    arch = _load_or_init_arch(project_path)
    arch.open_questions = _replace_or_append(arch.open_questions, q)
    save_architecture(project_path, arch)
    return q.id


# ---- modules/<m>/contracts.yaml upserts ----------------------------------


async def handle_module_set_owned_collection(
    *, project_path: Path, module_id: str, owned_collection: Any
) -> str:
    """Upsert one OwnedCollection on a module's contracts.yaml.

    Keyed by ``collection`` (OwnedCollection has no ``id`` field; the
    collection name is the natural unique key).
    """
    oc = _coerce(OwnedCollection, owned_collection, kind="owned_collection")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.owns = _replace_or_append(cf.owns, oc, id_field="collection")
    save_module_contracts(project_path, module_id, cf)
    return oc.collection


async def handle_module_set_external_dependency(
    *, project_path: Path, module_id: str, external_dependency: Any
) -> str:
    ed = _coerce(
        ExternalDependency, external_dependency, kind="external_dependency"
    )
    cf = _load_or_init_contracts(project_path, module_id)
    cf.external_dependencies = _replace_or_append(cf.external_dependencies, ed)
    save_module_contracts(project_path, module_id, cf)
    return ed.id


async def handle_module_set_integration_ac(
    *, project_path: Path, module_id: str, integration_ac: Any
) -> str:
    """Upsert one IntegrationAcceptance entry, keyed by capability id."""
    ia = _coerce(
        IntegrationAcceptance, integration_ac, kind="integration_ac"
    )
    cf = _load_or_init_contracts(project_path, module_id)
    cf.integration_ac = _replace_or_append(
        cf.integration_ac, ia, id_field="capability"
    )
    save_module_contracts(project_path, module_id, cf)
    return ia.capability


async def handle_module_set_behavioral_contract(
    *, project_path: Path, module_id: str, behavioral_contract: Any
) -> dict[str, Any]:
    """Upsert a BehavioralContract; return the id + authoring-quality warnings.

    Unlike the other upsert handlers (which return the new id as a
    bare string), this one returns a structured dict so the SA agent
    sees authoring-quality warnings immediately and can iterate before
    ``arch_finalize``. Warnings are advisory — they do NOT raise —
    per Deliverable 2's "validation that returns warnings, not raises"
    pattern (mirrors the intent reviewer). The validator wiring lands
    in Track C MVP commit 3; commit 2 ships the upsert + an empty
    warnings list so the response shape is stable from the first call.
    """
    bc = _coerce(
        BehavioralContract, behavioral_contract, kind="behavioral_contract"
    )
    cf = _load_or_init_contracts(project_path, module_id)
    cf.behavioral_contracts = _replace_or_append(
        cf.behavioral_contracts, bc
    )
    save_module_contracts(project_path, module_id, cf)
    warnings = _validate_bc_warnings(bc)
    return {"id": bc.id, "warnings": warnings}


def _validate_bc_warnings(bc: BehavioralContract) -> list[str]:
    """Stub — replaced by ``jig.sa_validation`` import in Track C MVP commit 3.

    Intentionally returns an empty list here so the handler's response
    shape (``{"id": ..., "warnings": []}``) is fixed from the first
    commit. Commit 3 swaps this body for a delegating call into
    ``jig.sa_validation.validate_behavioral_contract``.
    """
    _ = bc  # silence unused-arg lint until commit 3 lands.
    return []


async def handle_module_set_data_contract(
    *, project_path: Path, module_id: str, data_contract: Any
) -> str:
    dc = _coerce(DataContract, data_contract, kind="data_contract")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.data_contracts = _replace_or_append(cf.data_contracts, dc)
    save_module_contracts(project_path, module_id, cf)
    return dc.id


async def handle_module_set_open_question(
    *, project_path: Path, module_id: str, open_question: Any
) -> str:
    q = _coerce(OpenQuestion, open_question, kind="open_question")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.open_questions = _replace_or_append(cf.open_questions, q)
    save_module_contracts(project_path, module_id, cf)
    return q.id


# ---- arch_finalize --------------------------------------------------------


def _validate_module_link(arch: Architecture, module_ids: set[str]) -> None:
    """Per-module contracts.yaml ``module`` must reference a known module id.

    Mirrors ``sa_mcp._validate_module_link`` for the incremental path
    — orphan contracts files break ticket dispatch (see bones rationale
    in ``sa_mcp.py``).
    """
    arch_module_ids = {m.id for m in arch.modules}
    orphans = module_ids - arch_module_ids
    if orphans:
        raise ValueError(
            f"contracts authored for modules {sorted(orphans)!r} that "
            f"don't appear in architecture.modules (known: "
            f"{sorted(arch_module_ids)!r})"
        )


def _collect_authored_module_ids(project_path: Path) -> list[str]:
    """Return every module id that has a contracts.yaml on disk.

    Walks the ``.jig/spec/modules/`` dir (not the architecture's
    declared module list) so the orphan check in ``arch_finalize``
    catches contracts authored against module ids that aren't in
    architecture.yaml. The MVP discovery loop may declare a module +
    intent at architecture level before authoring its contracts —
    those modules just won't appear in this list, and the checklist
    enforcer (commit 3) handles the missing-contracts case via its
    own checklist-missing path.
    """
    modules_dir = project_path / ".jig" / "spec" / "modules"
    if not modules_dir.is_dir():
        return []
    out: list[str] = []
    for child in sorted(modules_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "contracts.yaml").is_file():
            out.append(child.name)
    return out


async def handle_arch_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    summary: str,
    author: str,
) -> str:
    """Finalize the incrementally-authored architecture + per-module contracts.

    Steps:

    1. Re-load architecture.yaml; raise if it doesn't validate (one
       upsert may have left an orphan entry that violates a schema
       constraint when read in concert with the rest).
    2. Re-load every authored ``modules/<m>/contracts.yaml``; raise if
       any fails to validate, or if any references an unknown module.
    3. Run ``validate_module_checklist`` on each module + its
       contracts. Modules with unmet categories AND no
       ``n_a_categories`` exemption raise. The exemption is the SA's
       way to say "this module legitimately has no behavioral
       contracts (CRUD-only)" without filling a useless contract.
    4. Run ``validate_behavioral_contract`` across every module's
       contracts; consolidate warnings into a Note thread entry on
       the architecture ticket so the operator can see them via
       ``jig story architecture``. Warnings don't block — they're
       advisory.
    5. Post the same Handoff the bones path posts (phase=PM); resolve
       the ticket via the shared ``resolve_after_handoff``.
    """
    arch = _load_or_init_arch(project_path)
    if not arch.modules:
        raise ValueError(
            "arch_finalize: architecture has no modules — author at "
            "least one via arch_set_module before finalizing"
        )

    authored_ids = _collect_authored_module_ids(project_path)
    contracts_by_module: dict[str, ContractsFile] = {
        mid: load_module_contracts(project_path, mid)
        for mid in authored_ids
    }
    _validate_module_link(arch, set(contracts_by_module.keys()))

    # Checklist enforcement + behavioral-contract authoring warnings
    # both land in Track C MVP commit 3 (jig.sa_validation). Commit 2
    # ships the finalize plumbing so the upsert handlers have a real
    # exit path; commit 3 wires the validators into both this finalize
    # and the per-tool ``module_set_behavioral_contract`` upsert.

    handoff = Handoff(
        ticket_id=SA_TICKET_ID,
        author=author,
        phase=SA_NEXT_PHASE,
        outputs=[
            ".jig/spec/architecture.yaml",
            *[
                f".jig/spec/modules/{mid}/contracts.yaml"
                for mid in sorted(contracts_by_module.keys())
            ],
        ],
        summary=summary,
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": SA_TICKET_ID,
                "phase": SA_NEXT_PHASE,
                "module_ids": sorted(contracts_by_module.keys()),
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=SA_TICKET_ID,
        author=author,
    )
    return entry_id
