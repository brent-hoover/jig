"""Incremental SA authoring MCP tool handlers (Track C MVP).

The bones SA path (``jig.sa_mcp``) exposes a single one-shot
``sa_finalize`` that takes a complete ``Architecture`` +
``ContractsFile`` payload. That works for a one-module tracer-bullet
project but doesn't support the design's discovery-loop per
``docs/v2.0/sa-architecture/design.md`` §"SA workflow — discovery loop":
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

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import RiskStatusChanged
from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff
from jig.intent import ComplicationsConsidered, Intent
from jig.sa_mcp import SA_NEXT_PHASE, SA_TICKET_ID
from jig.sa_validation import (
    validate_behavioral_contract,
    validate_module_checklist,
)
from jig.schemas.arch import (
    Architecture,
    BehavioralContract,
    CascadeAuditEntry,
    CascadeContractDisposition,
    CascadeProposal,
    CascadeStage,
    CascadeState,
    ContractsFile,
    CrossCuttingPolicy,
    DataContract,
    DataStore,
    ExternalDependency,
    IntegrationAcceptance,
    Module,
    OpenQuestion,
    OwnedCollection,
    Risk,
    RiskStatus,
    SharedContract,
)
from jig.spec_loader import (
    cascade_audit_path,
    cascade_proposal_path,
    cascades_dir,
    generated_contract_path,
    load_architecture,
    load_module_contracts,
    save_architecture,
    save_module_contracts,
)
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note
from jig.ticket import Ticket, WorkType


__all__ = [
    "handle_arch_approve_cascade_stage",
    "handle_arch_complete_spike",
    "handle_arch_finalize",
    "handle_arch_propose_spike",
    "handle_arch_regenerate_pydantic_models",
    "handle_arch_reject_cascade",
    "handle_arch_set_cascade_risk_low",
    "handle_arch_set_cross_cutting_policy",
    "handle_arch_set_data_store",
    "handle_arch_set_module",
    "handle_arch_set_open_question",
    "handle_arch_set_risk",
    "handle_arch_set_shared_contract",
    "handle_arch_stage_cascade",
    "handle_module_set_behavioral_contract",
    "handle_module_set_data_contract",
    "handle_module_set_external_dependency",
    "handle_module_set_integration_ac",
    "handle_module_set_open_question",
    "handle_module_set_owned_collection",
]


# Valid terminal outcomes for ``arch_complete_spike``. Mirrors the
# three states the design.md §"The cascade workflow" spike-completion
# branch enumerates; ``confirmed_impossible`` is the cascade trigger.
_SPIKE_OUTCOME_STATUSES: dict[str, RiskStatus] = {
    "mitigated": RiskStatus.MITIGATED,
    # Track C Final per ``docs/v2.0/sa-architecture/design.md`` §"Failure modes
    # and mitigations" mitigation #3: spikes that return "depends on
    # constraint X" land here rather than in confirmed_impossible. The
    # cascade fires conditionally — the constraint is captured on the
    # cascade artifact and contracts gain a constraint clause rather
    # than being reshaped unconditionally.
    "mitigated_with_constraints": RiskStatus.MITIGATED_WITH_CONSTRAINTS,
    "accepted": RiskStatus.ACCEPTED,
    "confirmed_impossible": RiskStatus.CONFIRMED_IMPOSSIBLE,
}


# Outcomes that emit a cascade-proposal artifact. Mitigation #3 brings
# ``mitigated_with_constraints`` into the cascade-emit set because the
# downstream contract revisions still need an audit trail — they're
# just smaller and conditional rather than full reshapes.
_CASCADE_EMITTING_STATUSES: frozenset[RiskStatus] = frozenset(
    {RiskStatus.CONFIRMED_IMPOSSIBLE, RiskStatus.MITIGATED_WITH_CONSTRAINTS}
)


# Default chunk size for ``arch_stage_cascade`` per failure mode #2 — the
# operator can override per-call but most cascades-too-big situations
# resolve cleanly at 5-per-stage (an operator can still hold a five-row
# decision in their head; ten rows blurs).
_DEFAULT_STAGE_CHUNK_SIZE = 5


# URI prefix for risks in the architecture register. Used as the
# spike ticket's ``derived_from`` so reviewers + the cascade-generator
# can trace lineage back to the originating risk without scanning the
# risk register.
_RISK_URI_PREFIX = "project://arch/risks/"


# Status values >= ``spike_proposed`` trigger the cascade-prep gates
# (dependent_contracts + intent required) per
# ``docs/v2.0/sa-architecture/design.md`` §"Risk schema requires
# `dependent_contracts`". ``OPEN`` is the noted-but-uncommitted state
# that escapes the gate so the SA can capture nascent risks without
# pre-committing to the dependency map.
_CASCADE_PREP_STATUSES: frozenset[RiskStatus] = frozenset(
    {
        RiskStatus.SPIKE_PROPOSED,
        RiskStatus.SPIKE_RUNNING,
        RiskStatus.MITIGATED,
        RiskStatus.ACCEPTED,
        RiskStatus.CONFIRMED_IMPOSSIBLE,
    }
)


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


def _load_or_init_contracts(project_path: Path, module_id: str) -> ContractsFile:
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
    return [item for item in items if getattr(item, id_field) != new_id] + [new_item]


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
            f"{kind} must be a dict or {model_cls.__name__}, got {type(raw).__name__}"
        )
    try:
        return model_cls.model_validate(raw)
    except ValidationError as e:
        raise ValueError(f"{kind} does not validate: {e}") from e


# ---- architecture.yaml upserts -------------------------------------------


async def handle_arch_set_module(*, project_path: Path, module: Any) -> str:
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


async def handle_arch_set_data_store(*, project_path: Path, data_store: Any) -> str:
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
    arch.cross_cutting_policies = _replace_or_append(arch.cross_cutting_policies, p)
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


def _validate_risk_cascade_prep(risk: Risk) -> None:
    """Enforce dependent_contracts + intent on risks past ``open``.

    Per ``docs/v2.0/sa-architecture/design.md`` §"Risk schema requires
    ``dependent_contracts``": once a risk transitions past ``open``
    the cascade workflow needs the dependents declared up front, and
    every authored v2 artifact carries an intent layer. Both gates
    fire together because they're paid at the same moment (the risk
    moves into the spike pipeline) and a partial declaration is the
    failure mode this catches.
    """
    if risk.status not in _CASCADE_PREP_STATUSES:
        return
    if not risk.dependent_contracts:
        raise ValueError(
            f"risk {risk.id!r}: dependent_contracts is required when "
            f"status is {risk.status.value!r} (>= spike_proposed). "
            "Without it the cascade workflow can't enumerate what "
            "changes when a spike confirms an assumption is wrong."
        )
    if risk.intent is None:
        raise ValueError(
            f"risk {risk.id!r}: intent is required when status is "
            f"{risk.status.value!r} (>= spike_proposed). Every v2 "
            "artifact past the early-capture state carries an intent "
            "layer; risks aren't an exception."
        )


async def handle_arch_set_risk(*, project_path: Path, risk: Any) -> str:
    """Upsert one Risk into architecture.yaml with cascade-prep gating.

    Mirrors the other ``arch_set_*`` upserts (keyed on ``id``,
    idempotent on re-set, accumulating across new ids). Adds two
    gates per ``docs/v2.0/sa-architecture/design.md`` §"Risk identification
    and spikes": once a risk passes ``open``, both ``dependent_contracts``
    and ``intent`` must be set so the cascade workflow has what it needs
    to enumerate impact when a spike confirms an assumption is wrong.

    Returns the risk id so the agent can immediately reference it from
    ``arch_propose_spike`` without re-reading the file.
    """
    r = _coerce(Risk, risk, kind="risk")
    _validate_risk_cascade_prep(r)
    arch = _load_or_init_arch(project_path)
    arch.risks = _replace_or_append(arch.risks, r)
    save_architecture(project_path, arch)
    return r.id


# ---- spike workflow ------------------------------------------------------


def _spike_intent_from_summary(summary: str) -> Intent:
    """Synthesize an Intent for a risk being upgraded to spike_proposed.

    The risk-cascade-prep gate requires ``intent`` once status >=
    spike_proposed. When the SA proposes the spike (rather than
    re-authoring the risk) we synthesize an intent from the spike
    summary so the gate stays satisfied without forcing a separate
    re-author step. The spike's summary IS the simplest expression of
    what the spike intends to learn — re-using it here keeps the
    intent layer non-vacuous without adding ceremony.
    """
    return Intent(
        problem=f"Verify whether the assumption holds: {summary}",
        simplest_solution=summary,
        complications_considered=ComplicationsConsidered(),
    )


def _spike_ticket_id_for_risk(risk_id: str) -> str:
    """Deterministic spike ticket id for a risk.

    One spike per risk at a time per the design's serialization rule
    (§"Failure modes and mitigations" — only one cascade in flight at
    a time per risk; spikes are 1:1 with risks at proposal time).
    Deterministic so the operator + the cascade artifact can reference
    the ticket id without round-tripping through the spike-creation
    return value.
    """
    return f"spike-{risk_id}"


def _truncate_for_title(text: str, *, limit: int = 80) -> str:
    """Truncate prose for a ticket title without breaking on whitespace."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


async def handle_arch_propose_spike(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    risk_id: str,
    summary: str,
    dependent_contracts: list[str],
    author: str,
) -> str:
    """Create a spike ticket linked to ``risk_id``; return the ticket id.

    Steps:

    1. Look up the risk in architecture.yaml; raise KeyError if absent
       (silent-create would mask a typo in the agent's risk_id).
    2. Create a ``WorkType.SPIKE`` ticket with deterministic id
       ``spike-<risk_id>`` so the cascade artifact + operator UX can
       reference it without round-tripping through this handler's
       return value.
    3. Update the risk: ``spike_ticket = <new ticket id>``, ``status =
       spike_proposed``, populate ``dependent_contracts``, synthesize
       ``intent`` from ``summary`` so the cascade-prep gate is satisfied.

    Idempotent on re-call with the same risk_id — re-running with a
    revised summary updates the existing spike ticket's description and
    re-saves the risk; this lets the SA iterate without manual cleanup.
    """
    arch = _load_or_init_arch(project_path)
    risks_by_id = {r.id: r for r in arch.risks}
    if risk_id not in risks_by_id:
        raise KeyError(
            f"risk {risk_id!r} not found in architecture.yaml — author "
            "via arch_set_risk before proposing a spike"
        )
    risk = risks_by_id[risk_id]

    spike_id = _spike_ticket_id_for_risk(risk_id)
    risk_uri = f"{_RISK_URI_PREFIX}{risk_id}"
    title = f"spike: {_truncate_for_title(risk.text)}"

    existing = await tickets.get(spike_id)
    if existing is None:
        await tickets.create(
            Ticket(
                id=spike_id,
                work_type=WorkType.SPIKE,
                title=title,
                description=summary,
                derived_from=risk_uri,
                risks_addressed=[risk_id],
                created_by=author,
            )
        )
    else:
        # Idempotent re-propose: refresh description + title; status
        # stays whatever the spike ticket currently has so a partially-
        # complete spike isn't reset.
        await tickets.update(
            spike_id,
            title=title,
            description=summary,
        )

    # Update the risk in place. Status moves to spike_proposed; the
    # cascade-prep gate now requires dependent_contracts + intent —
    # we satisfy both before save.
    updated = risk.model_copy(
        update={
            "spike_ticket": spike_id,
            "status": RiskStatus.SPIKE_PROPOSED,
            "dependent_contracts": list(dependent_contracts),
            "intent": risk.intent or _spike_intent_from_summary(summary),
        }
    )
    _validate_risk_cascade_prep(updated)
    arch.risks = _replace_or_append(arch.risks, updated)
    save_architecture(project_path, arch)

    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "spike_proposed",
                "ticket_id": spike_id,
                "risk_id": risk_id,
            },
            topic="orchestrator",
        )
    )
    return spike_id


async def handle_arch_complete_spike(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    spike_ticket_id: str,
    finding: str,
    status: str,
    author: str,
    emitter: EventEmitter | None = None,
    constraint: str | None = None,
) -> None:
    """Record the spike outcome — finding-as-Note + risk status update.

    ``status`` is one of ``mitigated``, ``accepted``,
    ``confirmed_impossible``. Other values raise.

    Steps:

    1. Validate the spike ticket exists AND has work_type == SPIKE
       (refusing to operate on non-spike tickets keeps the handler
       boundary clean — operator typos on ticket_id surface as
       errors, not as accidental status transitions on real work).
    2. Look up the linked risk via the spike's risks_addressed.
       Raise KeyError on orphan spikes — without a backing risk we
       can't transition status and the cascade workflow can't fire.
    3. Append a Note with the finding so ``jig story <spike_id>``
       carries operator-readable evidence of what the spike learned.
    4. Transition the risk's status to the matching outcome.
    5. Resolve the spike ticket via the shared resolver.
    6. When ``status == confirmed_impossible``: write a cascade-proposal
       artifact under ``.jig/arch/cascades/`` enumerating each dependent
       contract, post a Handoff on the architecture ticket targeting
       phase ``operator-cascade-confirm``, emit ``RiskStatusChanged``
       carrying the cascade path. MVP scope per
       ``docs/v2.0/implementation/v2-plan.md`` Track C: operator manually
       edits the YAML and re-runs SA; full transactional confirmation
       lands in Final.

    ``emitter`` is optional so other outcomes (mitigated, accepted)
    don't require an analytics emitter wired through. Only the
    ``confirmed_impossible`` branch consumes it.
    """
    if status not in _SPIKE_OUTCOME_STATUSES:
        raise ValueError(
            f"arch_complete_spike: status must be one of "
            f"{sorted(_SPIKE_OUTCOME_STATUSES)!r}, got {status!r}"
        )

    spike = await tickets.get(spike_ticket_id)
    if spike is None:
        raise KeyError(f"spike ticket {spike_ticket_id!r} not found")
    if spike.work_type != WorkType.SPIKE:
        raise ValueError(
            f"ticket {spike_ticket_id!r} is not a spike "
            f"(work_type={spike.work_type.value!r})"
        )

    if not spike.risks_addressed:
        raise KeyError(
            f"spike {spike_ticket_id!r} has no risks_addressed link — "
            "can't transition any risk status"
        )
    risk_id = spike.risks_addressed[0]

    arch = _load_or_init_arch(project_path)
    risks_by_id = {r.id: r for r in arch.risks}
    if risk_id not in risks_by_id:
        raise KeyError(
            f"spike {spike_ticket_id!r} references risk {risk_id!r} "
            "which is not in architecture.yaml"
        )
    risk = risks_by_id[risk_id]
    new_status = _SPIKE_OUTCOME_STATUSES[status]
    prior_status = risk.status.value

    # Note with the finding — operator-readable evidence on the spike
    # ticket. Posted before the risk-status flip so the trail order
    # (note then status change) reads naturally in ``jig story``.
    await threads.post(
        Note(
            ticket_id=spike_ticket_id,
            author=author,
            text=(f"Spike finding ({status}):\n{finding}"),
            payload={
                "kind": "spike_finding",
                "risk_id": risk_id,
                "outcome": status,
            },
        )
    )

    updated_risk = risk.model_copy(update={"status": new_status})
    arch.risks = _replace_or_append(arch.risks, updated_risk)
    save_architecture(project_path, arch)

    if status == "mitigated_with_constraints" and not constraint:
        # Mitigation #3: the new state demands a constraint clause —
        # without one the cascade artifact's ``constraint`` field
        # would be empty and the conditional-fire semantics collapse
        # to "we said constrained but didn't say how", which is the
        # silent-drift failure this state was added to prevent.
        raise ValueError(
            "arch_complete_spike: status='mitigated_with_constraints' "
            "requires a non-empty constraint clause describing the "
            "condition under which the cascade fires"
        )

    cascade_path: Path | None = None
    if new_status in _CASCADE_EMITTING_STATUSES:
        cascade_path = _write_cascade_proposal(
            project_path=project_path,
            risk=updated_risk,
            spike_ticket_id=spike_ticket_id,
            finding=finding,
            constraint=constraint,
            actor=author,
        )
        # Cascade Handoff on the architecture ticket — surfaces the
        # cascade for operator review. Phase name ``operator-cascade-
        # confirm`` matches the design's §"Cascade after confirmed-
        # impossible spike" UX hook for the (Final-scope) confirmation
        # screen; for MVP it just signals the operator to inspect the
        # YAML and re-run SA.
        await threads.post(
            Handoff(
                ticket_id=SA_TICKET_ID,
                author=author,
                phase="operator-cascade-confirm",
                outputs=[
                    str(cascade_path.relative_to(project_path)),
                ],
                summary=(
                    f"Cascade proposal for risk {risk_id!r}: "
                    f"{len(updated_risk.dependent_contracts)} dependent "
                    "contract(s) need operator review."
                ),
            )
        )
        if emitter is not None:
            emitter.emit_nowait(
                RiskStatusChanged(
                    risk_id=risk_id,
                    from_status=prior_status,
                    to_status=new_status.value,
                    spike_ticket_id=spike_ticket_id,
                    operator_confirmed=False,
                    cascade_proposal_path=str(cascade_path.relative_to(project_path)),
                )
            )

    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "spike_completed",
                "ticket_id": spike_ticket_id,
                "risk_id": risk_id,
                "outcome": status,
                "cascade_proposal_path": (
                    str(cascade_path.relative_to(project_path))
                    if cascade_path is not None
                    else None
                ),
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=spike_ticket_id,
        author=author,
    )


# ---- cascade-after-impossible-spike artifact -----------------------------


def _cascade_timestamp() -> str:
    """Filesystem-safe UTC timestamp suffix for cascade-proposal paths.

    Format ``YYYYMMDDTHHMMSS`` so multiple cascade rounds for the same
    risk sort by name in chronological order — operators reading the
    cascades dir see the audit trail in time order without parsing.
    """
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")


def _resolve_dependent_shape(project_path: Path, uri: str) -> str | None:
    """Best-effort lookup of a dependent contract's current shape.

    The URI scheme is partial in MVP — only architecture-resident
    shapes (modules, shared contracts) resolve cleanly. Per-module
    contract URIs (``project://arch/modules/<m>/contracts#...``)
    return None and the operator reads the contract source directly.
    Full URI resolution lands as the URI authority resolvers mature
    (out of MVP scope per ``docs/v2.0/implementation/v2-plan.md``).
    """
    if not uri.startswith("project://arch/modules/"):
        return None
    # Extract module id; we can only confirm presence + dump the
    # whole module's contracts file by reference. Fragment resolution
    # (``#<section>/<id>``) is what matures in the URI authority work.
    rest = uri.removeprefix("project://arch/modules/")
    module_id = rest.split("/", 1)[0]
    try:
        # Confirm the module's contracts file is loadable; we don't
        # render the full contents here (the operator opens the file
        # directly). Full fragment resolution lands as the URI
        # authority resolvers mature.
        load_module_contracts(project_path, module_id)
    except FileNotFoundError:
        return None
    return f"module={module_id}; contracts.yaml present"


def _cascade_id(risk_id: str, ts: str) -> str:
    """Compose the cascade id used in artifacts + audit log.

    Mirrors the on-disk filename (``<risk-id>-<timestamp>.yaml``) so
    operators can grep/sort either dimension consistently. Audit log
    entries reference the same id so a viewer can join the per-action
    history with the proposal artifact via a single key.
    """
    return f"{risk_id}-{ts}"


def _load_cascade_proposal(path: Path) -> CascadeProposal:
    """Load + validate one cascade-proposal YAML."""
    return CascadeProposal.model_validate(yaml.safe_load(path.read_text()))


def _save_cascade_proposal(path: Path, proposal: CascadeProposal) -> None:
    """Atomically rewrite one cascade-proposal YAML.

    Used by all the state-mutating handlers (reject / stage / approve)
    so disk + in-memory state stay aligned. ``sort_keys=False`` mirrors
    the writer for stable diffs across mutations.
    """
    payload = yaml.safe_dump(proposal.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(path, payload)


def _find_cascade_path(project_path: Path, cascade_id: str) -> Path:
    """Resolve a cascade_id to its on-disk YAML path; raise KeyError if absent.

    The cascade_id format is ``<risk-id>-<timestamp>`` so we can
    compute the path directly without globbing — but we still validate
    the file exists to surface typos as KeyError rather than letting
    the caller hit FileNotFoundError on the next read.
    """
    target_dir = cascades_dir(project_path)
    candidate = target_dir / f"{cascade_id}.yaml"
    if not candidate.is_file():
        raise KeyError(
            f"cascade {cascade_id!r} not found at {candidate} — "
            "audit trail and viewer reference cascades by id; verify "
            "the id matches the on-disk artifact"
        )
    return candidate


def _append_cascade_audit(project_path: Path, entry: CascadeAuditEntry) -> None:
    """Append one CascadeAuditEntry to ``.jig/arch/cascades/audit.jsonl``.

    JSONL append-only — the audit log is a write-once record per
    action so the viewer + analytics consumers never see partial state.
    Read-rewrite-replace mirrors the deferred-queue pattern in
    ``coordinator.py``: the cascade audit log is small (operator-action
    granularity), so a full-file rewrite is cheap.
    """
    path = cascade_audit_path(project_path)
    rows: list[str] = []
    if path.is_file():
        rows = [line for line in path.read_text().splitlines() if line.strip()]
    rows.append(json.dumps(entry.model_dump(mode="json"), sort_keys=True))
    atomic_write_text(path, "\n".join(rows) + "\n")


def _detect_holding_for(project_path: Path, dependents: list[str]) -> str | None:
    """Mitigation #4: scan existing cascades for overlapping URIs.

    Returns the cascade_id of an in-flight cascade (state == pending,
    staged, or holding) whose ``contracts[].uri`` set overlaps the
    new cascade's dependent URIs — or None when nothing overlaps.
    URI string-equality is sufficient for Final per the task spec;
    semantic URI normalization is v2.x.

    "In-flight" intentionally excludes ``rejected`` and ``resolved``:
    a cascade the operator already disposed of can't merge-conflict
    with the new one's contract amendments.
    """
    target_dir = cascades_dir(project_path)
    if not target_dir.is_dir():
        return None
    new_uris = set(dependents)
    in_flight = {
        CascadeState.PENDING,
        CascadeState.STAGED,
        CascadeState.HOLDING,
    }
    # Sort by filename so timestamp order = chronological order; we
    # return the oldest in-flight overlap (FIFO release once it's
    # acted on, even though auto-release is v2.x scope).
    for path in sorted(target_dir.glob("*.yaml")):
        try:
            existing = _load_cascade_proposal(path)
        except Exception:
            # A malformed YAML in the cascades dir shouldn't block a
            # new cascade — surface via the viewer separately.
            continue
        if existing.state not in in_flight:
            continue
        existing_uris = {c.uri for c in existing.contracts}
        if existing_uris & new_uris:
            return existing.cascade_id
    return None


def _write_cascade_proposal(
    *,
    project_path: Path,
    risk: Risk,
    spike_ticket_id: str,
    finding: str,
    constraint: str | None = None,
    actor: str = "sa-mvp",
) -> Path:
    """Atomically write a cascade-proposal YAML for ``risk``; return its path.

    Per ``docs/v2.0/sa-architecture/design.md`` §"The cascade workflow" step
    5 (audit trail) plus Track C Final §"Failure modes and mitigations":

    - Mitigation #4: scans existing cascades for dependent_contracts
      overlap. When an in-flight cascade overlaps, the new cascade is
      written with state=holding and ``holding_for=<existing-id>`` so
      the operator/viewer can see it's serialized behind another
      cascade. Auto-release on resolution is v2.x.
    - Mitigation #3: when ``constraint`` is provided (i.e. the spike
      came back ``mitigated_with_constraints``), the cascade artifact
      records it so contracts get a constraint clause rather than full
      replacement.
    - Mitigation #1: every cascade write appends a ``proposed`` audit
      entry (and a ``holding`` entry when held) so the viewer + cross-
      project analytics see the lineage from the first action onward.

    MVP scope still applies for the contract-disposition default —
    every dependent starts at ``still_holds`` and the operator (or, in
    a future SA delta-pass) flips entries to ``invalidated`` /
    ``needs_revision``.
    """
    contracts = [
        CascadeContractDisposition(
            uri=uri,
            current_shape=_resolve_dependent_shape(project_path, uri),
            proposed_disposition="still_holds",
        )
        for uri in risk.dependent_contracts
    ]
    ts = _cascade_timestamp()
    cascade_id = _cascade_id(risk.id, ts)
    holding_for = _detect_holding_for(project_path, risk.dependent_contracts)
    state = CascadeState.HOLDING if holding_for else CascadeState.PENDING
    proposal = CascadeProposal(
        cascade_id=cascade_id,
        risk_id=risk.id,
        spike_ticket_id=spike_ticket_id,
        finding=finding,
        contracts=contracts,
        constraint=constraint,
        holding_for=holding_for,
        state=state,
    )
    cascades_dir(project_path).mkdir(parents=True, exist_ok=True)
    target = cascade_proposal_path(project_path, risk.id, ts)
    _save_cascade_proposal(target, proposal)
    # Audit: always log the proposed action; add a held entry when the
    # concurrent-cascade detector blocked emission.
    _append_cascade_audit(
        project_path,
        CascadeAuditEntry(
            cascade_id=cascade_id,
            risk_id=risk.id,
            action="proposed",
            actor=actor,
        ),
    )
    if holding_for is not None:
        _append_cascade_audit(
            project_path,
            CascadeAuditEntry(
                cascade_id=cascade_id,
                risk_id=risk.id,
                action="holding",
                actor=actor,
                holding_for=holding_for,
                reason=(f"overlapping dependent_contracts with cascade {holding_for}"),
            ),
        )
    return target


# ---- cascade failure-mode mitigation handlers (Track C Final) -----------


async def handle_arch_reject_cascade(
    *,
    project_path: Path,
    cascade_id: str,
    reason: str,
    actor: str,
) -> CascadeAuditEntry:
    """Reject a cascade proposal; record the action in the audit log.

    Mitigation #1 per ``docs/v2.0/sa-architecture/design.md`` §"Failure modes
    and mitigations". The proposal's ``state`` flips to ``rejected`` and
    ``rejected_reason`` is stamped so the cascade YAML carries the
    decision inline; the JSONL audit entry is the cross-project signal
    analytics consumes for "operator rejects N% of cascades from this
    risk family" analysis.

    ``reason`` is required (no silent rejections) — the design's
    "structured category" applies once the operator UX taxonomy is
    finalized; for Final scope any non-empty string is sufficient and
    the consumer aggregates by exact-match.
    """
    if not reason or not reason.strip():
        raise ValueError(
            "arch_reject_cascade: reason is required — silent rejections "
            "defeat the audit-flagging mitigation"
        )
    path = _find_cascade_path(project_path, cascade_id)
    proposal = _load_cascade_proposal(path)
    updated = proposal.model_copy(
        update={
            "state": CascadeState.REJECTED,
            "rejected_reason": reason,
        }
    )
    _save_cascade_proposal(path, updated)
    entry = CascadeAuditEntry(
        cascade_id=cascade_id,
        risk_id=proposal.risk_id,
        action="rejected",
        actor=actor,
        reason=reason,
    )
    _append_cascade_audit(project_path, entry)
    return entry


def _split_into_stages(
    cascade_id: str,
    contracts: list[CascadeContractDisposition],
    chunk_size: int,
) -> list[CascadeStage]:
    """Slice ``contracts`` into stages of at most ``chunk_size`` entries.

    Stage ids are deterministic (``<cascade-id>-stage-<n>``) so the
    operator UX, audit log, and stage-approve handler can reference
    stages without round-tripping through the cascade artifact.
    """
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size!r}")
    out: list[CascadeStage] = []
    for i in range(0, len(contracts), chunk_size):
        out.append(
            CascadeStage(
                stage_id=f"{cascade_id}-stage-{(i // chunk_size) + 1}",
                contracts=contracts[i : i + chunk_size],
            )
        )
    return out


async def handle_arch_stage_cascade(
    *,
    project_path: Path,
    cascade_id: str,
    actor: str,
    chunk_size: int = _DEFAULT_STAGE_CHUNK_SIZE,
) -> list[CascadeStage]:
    """Split a cascade into operator-approval stages.

    Mitigation #2: huge cascades (10+ contracts in the design's example)
    overwhelm the all-or-nothing confirmation screen. Staging splits the
    contracts into ``chunk_size`` slices; the operator approves stage-
    by-stage via ``arch_approve_cascade_stage`` so partial progress is
    explicit. The cascade's overall ``state`` becomes ``staged`` until
    every stage approves (then ``resolved``) or the operator rejects
    the whole thing (``rejected``).

    Idempotent on re-call: the cascade gets re-staged from the live
    contracts list; existing stages are replaced. This lets the
    operator change chunk_size mid-flight without manual cleanup.
    """
    path = _find_cascade_path(project_path, cascade_id)
    proposal = _load_cascade_proposal(path)
    stages = _split_into_stages(cascade_id, proposal.contracts, chunk_size)
    updated = proposal.model_copy(
        update={
            "stages": stages,
            "state": CascadeState.STAGED,
        }
    )
    _save_cascade_proposal(path, updated)
    _append_cascade_audit(
        project_path,
        CascadeAuditEntry(
            cascade_id=cascade_id,
            risk_id=proposal.risk_id,
            action="staged",
            actor=actor,
            reason=f"chunk_size={chunk_size}; stages={len(stages)}",
        ),
    )
    return stages


async def handle_arch_approve_cascade_stage(
    *,
    project_path: Path,
    cascade_id: str,
    stage_id: str,
    actor: str,
) -> CascadeStage:
    """Approve one stage of a staged cascade; resolve the whole when last.

    Mitigation #2 follow-on: the per-stage approval primitive. Each
    approval stamps ``approved=True``, ``approved_by=<actor>``, and
    ``approved_at=<now>`` on the named stage. When every stage is
    approved the cascade as a whole transitions to ``resolved``; an
    additional ``resolved`` audit entry lands so the audit-trail viewer
    and downstream consumers see the terminal action without scanning
    every prior stage_approved entry.
    """
    path = _find_cascade_path(project_path, cascade_id)
    proposal = _load_cascade_proposal(path)
    if not proposal.stages:
        raise ValueError(
            f"cascade {cascade_id!r} has no stages — call "
            "arch_stage_cascade before approving stages"
        )
    target_stage: CascadeStage | None = None
    new_stages: list[CascadeStage] = []
    for s in proposal.stages:
        if s.stage_id == stage_id:
            target_stage = s.model_copy(
                update={
                    "approved": True,
                    "approved_at": datetime.now(timezone.utc),
                    "approved_by": actor,
                }
            )
            new_stages.append(target_stage)
        else:
            new_stages.append(s)
    if target_stage is None:
        raise KeyError(
            f"cascade {cascade_id!r} has no stage {stage_id!r}; "
            f"known stages: {[s.stage_id for s in proposal.stages]!r}"
        )
    all_approved = all(s.approved for s in new_stages)
    new_state = CascadeState.RESOLVED if all_approved else CascadeState.STAGED
    updated = proposal.model_copy(update={"stages": new_stages, "state": new_state})
    _save_cascade_proposal(path, updated)
    _append_cascade_audit(
        project_path,
        CascadeAuditEntry(
            cascade_id=cascade_id,
            risk_id=proposal.risk_id,
            action="stage_approved",
            actor=actor,
            stage_id=stage_id,
        ),
    )
    if all_approved:
        _append_cascade_audit(
            project_path,
            CascadeAuditEntry(
                cascade_id=cascade_id,
                risk_id=proposal.risk_id,
                action="resolved",
                actor=actor,
            ),
        )
    return target_stage


async def handle_arch_set_cascade_risk_low(
    *,
    project_path: Path,
    module_id: str,
    low: bool,
    rationale: str,
) -> str:
    """Mark a Module's ``cascade_risk_low`` flag with operator/SA rationale.

    Per ``docs/v2.0/pm-workflow/design.md`` §"Bones-first ordering" /
    ``cascade_risk_low`` flag. PM Coordinator reads this when deciding
    whether to allow MVP promotion despite a still-running bones layer
    on a sibling epic; a True flag with rationale is the SA's signal
    that promotion won't cascade upward.

    ``rationale`` is required when ``low=True`` so the audit trail
    captures the SA's reasoning rather than a bare boolean toggle.
    Setting ``low=False`` clears the rationale alongside the flag —
    re-running with a fresh rationale flips both back together.
    """
    if low and not rationale.strip():
        raise ValueError(
            "arch_set_cascade_risk_low: rationale is required when "
            "low=True; the flag is the SA's signal to PM that bones "
            "promotion is safe — that signal is meaningless without "
            "the reasoning behind it"
        )
    arch = _load_or_init_arch(project_path)
    by_id = {m.id: m for m in arch.modules}
    if module_id not in by_id:
        raise KeyError(
            f"module {module_id!r} not found in architecture.yaml — "
            "set the module via arch_set_module before flagging risk"
        )
    target = by_id[module_id]
    updated = target.model_copy(
        update={
            "cascade_risk_low": low,
            "cascade_risk_low_rationale": rationale if low else None,
        }
    )
    arch.modules = _replace_or_append(arch.modules, updated)
    save_architecture(project_path, arch)
    return module_id


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
    ed = _coerce(ExternalDependency, external_dependency, kind="external_dependency")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.external_dependencies = _replace_or_append(cf.external_dependencies, ed)
    save_module_contracts(project_path, module_id, cf)
    return ed.id


async def handle_module_set_integration_ac(
    *, project_path: Path, module_id: str, integration_ac: Any
) -> str:
    """Upsert one IntegrationAcceptance entry, keyed by capability id."""
    ia = _coerce(IntegrationAcceptance, integration_ac, kind="integration_ac")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.integration_ac = _replace_or_append(cf.integration_ac, ia, id_field="capability")
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
    bc = _coerce(BehavioralContract, behavioral_contract, kind="behavioral_contract")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.behavioral_contracts = _replace_or_append(cf.behavioral_contracts, bc)
    save_module_contracts(project_path, module_id, cf)
    warnings = validate_behavioral_contract(bc)
    return {"id": bc.id, "warnings": warnings}


async def handle_module_set_data_contract(
    *, project_path: Path, module_id: str, data_contract: Any
) -> str:
    """Upsert a DataContract; auto-render Pydantic class when fields are present.

    Track I Final: when ``DataContract.fields`` is non-empty the
    handler also writes a Pydantic class source file under
    ``.jig/generated/contracts/<module>/<contract>.py``. The render
    is best-effort — when the contract has no inline ``fields`` the
    handler skips the generation step (legitimate; not every contract
    carries an inline shape).
    """
    dc = _coerce(DataContract, data_contract, kind="data_contract")
    cf = _load_or_init_contracts(project_path, module_id)
    cf.data_contracts = _replace_or_append(cf.data_contracts, dc)
    save_module_contracts(project_path, module_id, cf)
    _maybe_render_pydantic_for_contract(project_path, module_id, dc)
    return dc.id


def _maybe_render_pydantic_for_contract(
    project_path: Path, module_id: str, contract: DataContract
) -> Path | None:
    """Render the contract to a Pydantic file when ``fields`` is populated.

    Returns the written path on success; None when the contract has
    no fields or rendering raises (we re-raise ValueError because
    that's a real authoring error worth surfacing — the agent set
    fields but with a bad identifier, etc.).
    """
    if not contract.fields:
        return None
    # Lazy import keeps the SA module's import surface unchanged for
    # callers that never trigger the renderer.
    from jig.renderers.pydantic_from_data_contract import (
        render_pydantic_from_data_contract,
    )

    src = render_pydantic_from_data_contract(contract)
    target = generated_contract_path(project_path, module_id, contract.id)
    atomic_write_text(target, src)
    return target


async def handle_arch_regenerate_pydantic_models(
    *, project_path: Path, module_id: str | None = None
) -> list[Path]:
    """Re-render every renderable DataContract in scope; return the file paths.

    Track I Final operator-facing tool. Walks every authored
    ``modules/<m>/contracts.yaml`` (or just one when ``module_id`` is
    given) and re-runs the renderer on each contract that carries
    inline ``fields``. The returned list is a flat sequence of
    written paths — useful both as MCP return value and as a CLI
    summary the operator can scan.

    Contracts without ``fields`` are skipped silently (URI-based
    schema resolution is the v2.x lift; the renderer itself raises
    on bad inputs but the bulk path treats absence as "nothing to
    render here, that's fine").
    """
    written: list[Path] = []
    if module_id is not None:
        try:
            cf = load_module_contracts(project_path, module_id)
        except FileNotFoundError:
            return []
        for dc in cf.data_contracts:
            path = _maybe_render_pydantic_for_contract(project_path, module_id, dc)
            if path is not None:
                written.append(path)
        return written

    modules_dir = project_path / ".jig" / "spec" / "modules"
    if not modules_dir.is_dir():
        return []
    for child in sorted(modules_dir.iterdir()):
        if not child.is_dir():
            continue
        if not (child / "contracts.yaml").is_file():
            continue
        mid = child.name
        try:
            cf = load_module_contracts(project_path, mid)
        except FileNotFoundError:
            continue
        for dc in cf.data_contracts:
            path = _maybe_render_pydantic_for_contract(project_path, mid, dc)
            if path is not None:
                written.append(path)
    return written


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


def _validate_consumption_refs(
    arch: "Architecture",
    contracts_by_module: "dict[str, ContractsFile]",
) -> None:
    """Verify Module.consumes_apis and consumes_events resolve to real entries.

    For each ApiConsumption entry on a module, checks that the named
    provider module has a contracts.yaml with an ExposedAPI whose ``name``
    matches.  For EventConsumption, checks EmittedEvent.name on the
    publisher's contracts.

    Raises ValueError listing all broken references so the SA sees the
    full set at once rather than fixing one at a time.
    """
    errors: list[str] = []
    for module in arch.modules:
        for api_cons in module.consumes_apis:
            provider_cf = contracts_by_module.get(api_cons.module)
            if provider_cf is None:
                errors.append(
                    f"{module.id}.consumes_apis: provider module "
                    f"'{api_cons.module}' has no contracts.yaml"
                )
                continue
            known = {e.name for e in provider_cf.exposes}
            if api_cons.name not in known:
                errors.append(
                    f"{module.id}.consumes_apis: '{api_cons.name}' not found "
                    f"in {api_cons.module}.exposes (known: {sorted(known)!r})"
                )

        for ev_cons in module.consumes_events:
            publisher_cf = contracts_by_module.get(ev_cons.module)
            if publisher_cf is None:
                errors.append(
                    f"{module.id}.consumes_events: publisher module "
                    f"'{ev_cons.module}' has no contracts.yaml"
                )
                continue
            known_ev = {e.name for e in publisher_cf.emits}
            if ev_cons.name not in known_ev:
                errors.append(
                    f"{module.id}.consumes_events: '{ev_cons.name}' not found "
                    f"in {ev_cons.module}.emits (known: {sorted(known_ev)!r})"
                )

    if errors:
        raise ValueError(
            "arch_finalize: consumption cross-reference errors:\n"
            + "\n".join(f"  - {e}" for e in errors)
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
        mid: load_module_contracts(project_path, mid) for mid in authored_ids
    }
    _validate_module_link(arch, set(contracts_by_module.keys()))

    # Checklist enforcement — raise on the first module with unmet
    # categories (aggregating across modules would be friendlier, but
    # MVP YAGNI: operator fixes one and re-finalizes). Modules without
    # an authored contracts.yaml still get checked — the checklist
    # treats absent contracts as "everything missing".
    for m in arch.modules:
        cf = contracts_by_module.get(m.id)
        missing = validate_module_checklist(m, cf)
        if missing:
            raise ValueError(
                f"module {m.id!r} unmet checklist categories: "
                f"{sorted(missing)!r}. Either author them, or add to "
                "the module's ``n_a_categories`` field to declare them "
                "intentionally not applicable."
            )

    # Behavioral-contract authoring warnings — advisory only, posted
    # as a Note on the architecture ticket so the operator can see
    # them via ``jig story architecture``. Empty warning list = no
    # Note (avoids cluttering the thread with empty advisories).
    bc_warnings: list[str] = []
    for mid, cf in contracts_by_module.items():
        for bc in cf.behavioral_contracts:
            for w in validate_behavioral_contract(bc):
                bc_warnings.append(f"[{mid}/{bc.id}] {w}")
    if bc_warnings:
        await threads.post(
            Note(
                ticket_id=SA_TICKET_ID,
                author=author,
                text=(
                    "Behavioral-contract authoring warnings:\n- "
                    + "\n- ".join(bc_warnings)
                ),
            )
        )

    # Phase 2 dep-graph PR #1 — consumption cross-reference validation.
    # Each Module.consumes_apis entry must resolve to a real ExposedAPI on
    # the named provider; likewise for consumes_events vs. EmittedEvent.
    # Fails loud at finalize so the SA sees the breakage before PM planning.
    _validate_consumption_refs(arch, contracts_by_module)

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
