"""MCP tool handlers for the v2 SA (Tracks C1+C2, bones).

The v2 SA reads the L3 suite brief + structured spec, then writes the
architectural contracts in two artifacts:

- ``.jig/spec/architecture.yaml`` — project-level (data stores, modules,
  cross-cutting policies, risks).
- ``.jig/spec/modules/<module_id>/contracts.yaml`` — per-module (owned
  collections, exposed APIs, integration AC, behavioral / data
  contracts).

Bones SA does the absolute minimum: one project-level architecture
covering one module + one capability + one integration AC + zero risks.
The full discovery loop (multi-module checklist walk, behavioral
contracts, risk register + spike workflow, cascade-after-impossible)
lands in C3-C6 — out of bones scope.

Distinct from ``init_mcp.py`` (v1 ``arch_set_field`` style — free-form
dict at the same path) and ``po_l3_mcp.py`` (the upstream L3 PO that
hands off to phase ``"sa"``). v1 SA stays intact; v2 ships alongside
until the rest of the v2 tracks land and v1 retires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff
from jig.schemas.arch import Architecture, ContractsFile
from jig.spec_loader import architecture_path, module_contracts_path
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff

# v2 SA is project-level — one arch ticket per project. Mirrors v1 SA's
# ``ticket_id="architecture"`` convention so existing scaffolding around
# the architecture ticket (CLI, TUI status panes) keeps working.
SA_TICKET_ID = "architecture"

# The v2 SA hands off to PM next. Track F will land the planner agent;
# until then, the orchestrator side just records the handoff. Captured
# as a constant so the F track can swap it without touching call sites.
SA_NEXT_PHASE = "pm"


# ---- coercion -------------------------------------------------------------


def _coerce_architecture(raw: Any) -> Architecture:
    """Validate ``raw`` against the ``Architecture`` schema.

    Accepts the model instance directly (idempotent) or a dict from
    the MCP tool boundary. Re-raised as ``ValueError`` so the agent
    sees a uniform error type alongside other ``handle_sa_finalize``
    rejections.
    """
    if isinstance(raw, Architecture):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(
            f"architecture must be a dict or Architecture, got {type(raw).__name__}"
        )
    try:
        return Architecture.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"architecture does not validate: {e}") from e


def _coerce_module_contracts(raw: Any) -> ContractsFile:
    if isinstance(raw, ContractsFile):
        return raw
    if not isinstance(raw, dict):
        raise ValueError(
            f"module_contracts must be a dict or ContractsFile, got {type(raw).__name__}"
        )
    try:
        return ContractsFile.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"module_contracts does not validate: {e}") from e


# ---- bones-scope validation ----------------------------------------------


def _validate_bones_minimums(arch: Architecture, contracts: ContractsFile) -> None:
    """Enforce the bones floor per ``docs/v2.0/implementation/v2-plan.md``.

    The Pydantic schemas allow empty lists everywhere — convenient for
    later authoring tools that build the artifact incrementally — but
    the bones SA must produce something walkable end-to-end. We enforce
    here rather than via min-length validators on the schema so that
    section-by-section editing tools (Track C3+) can build up a partial
    artifact between calls.
    """
    if not arch.data_stores:
        raise ValueError("bones architecture must declare at least one data_store")
    if not arch.modules:
        raise ValueError("bones architecture must declare at least one module")
    if not contracts.owns:
        raise ValueError(
            f"bones module {contracts.module!r} must declare at least one "
            "owned collection"
        )
    if not contracts.integration_ac:
        raise ValueError(
            f"bones module {contracts.module!r} must declare at least one "
            "integration_ac entry (one capability with one MUST)"
        )


def _validate_module_link(arch: Architecture, contracts: ContractsFile) -> None:
    """The contracts file's ``module`` must reference an architecture module.

    Without this, dev agents could be dispatched against contracts whose
    owning module isn't recorded in the project arch — breaking ticket
    routing and reviewer-context auto-injection (per design.md
    §"Contract consumption by dev agents").
    """
    arch_module_ids = {m.id for m in arch.modules}
    if contracts.module not in arch_module_ids:
        raise ValueError(
            f"module_contracts.module {contracts.module!r} does not match "
            f"any module id in architecture (known: {sorted(arch_module_ids)!r})"
        )


# ---- the finalize handler -------------------------------------------------


async def handle_sa_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    architecture: Any,
    module_contracts: Any,
    author: str,
) -> str:
    """One-shot SA finalize for bones — write both artifacts + hand off to PM.

    Returns the Handoff entry id. Raises ``ValueError`` when:

    - ``architecture`` or ``module_contracts`` don't validate against
      the Pydantic schemas (e.g. missing ``intent`` on a Module — the
      v2 schemas require it for every authored artifact).
    - the bones minimums are unmet (no data store / no module / no
      owned collection / no integration AC).
    - ``module_contracts.module`` doesn't appear in
      ``architecture.modules[*].id`` — orphan contracts.

    Writes are atomic: ``architecture.yaml`` and ``modules/<m>/contracts.yaml``
    replace on success, untouched on validation failure. Both writes are
    sequenced; if the second fails after the first lands the architecture
    is still consistent on its own (the module entry is declared, just
    without its contracts file yet) so a retry can finish the job
    without manual cleanup.
    """
    arch = _coerce_architecture(architecture)
    contracts = _coerce_module_contracts(module_contracts)
    _validate_module_link(arch, contracts)
    _validate_bones_minimums(arch, contracts)

    arch_yaml = yaml.safe_dump(
        arch.model_dump(mode="json"),
        sort_keys=False,
    )
    atomic_write_text(architecture_path(project_path), arch_yaml)

    contracts_yaml = yaml.safe_dump(
        contracts.model_dump(mode="json"),
        sort_keys=False,
    )
    atomic_write_text(
        module_contracts_path(project_path, contracts.module),
        contracts_yaml,
    )

    handoff = Handoff(
        ticket_id=SA_TICKET_ID,
        author=author,
        phase=SA_NEXT_PHASE,
        outputs=[
            ".jig/spec/architecture.yaml",
            f".jig/spec/modules/{contracts.module}/contracts.yaml",
        ],
        summary=(
            f"SA bones: 1 module ({contracts.module}), "
            f"{len(contracts.integration_ac)} capability AC, "
            f"{len(arch.risks)} risks"
        ),
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
                "module_id": contracts.module,
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


__all__ = [
    "SA_NEXT_PHASE",
    "SA_TICKET_ID",
    "handle_sa_finalize",
]
