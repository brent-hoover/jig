"""MCP tool handlers for the Proposal mechanism (Phase 3 Task G).

Three tools ship with this module:

* ``propose_change`` — create a Proposal thread entry on a ticket and
  route it to the owning role.
* ``resolve_proposal`` — accept / reject / refine an outstanding
  proposal. Enforces the self-certification guard per doc 04.
* ``list_proposals`` — query helper used by both humans and the TUI.

Phase 4: proposals live in ``ThreadStore`` as first-class
:class:`jig.thread.Proposal` entries. The self-approval audit marker
is a :class:`jig.thread.SystemEvent` (``event_type="status_change"``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jig.config import load_config
from jig.ownership import OwnerRouting, resolve_owner
from jig.section_locks import locked_sections_for_ticket
from jig.specs import (
    SpecValidationError,
    TicketSpec,
    load_ticket_spec,
    save_ticket_spec,
)
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Proposal, SystemEvent
from jig.work_types import load_work_type_schema

_logger = logging.getLogger(__name__)


class ProposalError(ValueError):
    """Raised when a proposal operation is disallowed by configuration."""


# ---- propose_change -------------------------------------------------------


async def handle_propose_change(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
) -> dict[str, Any]:
    """Create a Proposal on a ticket + record the routing decision.

    Required args:
      ticket_id, target (URI), change (content of the proposed change).
    Optional args:
      section, content (prose rationale).
    """
    ticket_id = args["ticket_id"]
    target = args["target"]
    change = args["change"]
    section = args.get("section")
    rationale = args.get("content") or f"Proposed change to {target}"

    ticket = await tickets.get(ticket_id)
    if ticket is None:
        raise KeyError(f"ticket {ticket_id} not found")

    cfg = load_config(project_path)

    # Route: resolve owners up front so downstream queries don't
    # re-derive, and so routing errors surface before the proposal
    # lands in the thread.
    schema = None
    if target.startswith("ticket://spec"):
        schema = load_work_type_schema(project_path, ticket.work_type.value)
    routing = resolve_owner(cfg, target, work_type_schema=schema)

    proposal = Proposal(
        ticket_id=ticket_id,
        author=sender,
        target=target,
        section=section,
        change=change,
        rationale=rationale,
        state="pending",
        owners=[routing.role],
    )
    pid = await threads.post(proposal)
    return {
        "comment_id": pid,
        "routing": _routing_to_dict(routing),
        "state": "pending",
    }


# ---- resolve_proposal -----------------------------------------------------


async def handle_resolve_proposal(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
) -> dict[str, Any]:
    """Accept / reject / refine a pending proposal.

    Required args:
      proposal_id, verdict ∈ {accept, reject, refine}.
    Optional args:
      reasoning (non-empty when self-approving under "warn").
      applied_change (replacement YAML fragment for the target field
        on ticket://spec targets — defaults to the original proposal's
        ``change`` if absent).
    """
    verdict = args["verdict"]
    if verdict not in {"accept", "reject", "refine"}:
        raise ValueError(f"verdict must be accept/reject/refine, got {verdict!r}")

    proposal = await _find_proposal(threads, args["proposal_id"])
    if proposal.state != "pending":
        raise ProposalError(
            f"proposal {proposal.id} is not pending (state={proposal.state})"
        )

    # --- self-cert guard (doc 04) ---
    cfg = load_config(project_path)
    self_approving = proposal.author == sender
    if self_approving and cfg.self_approval == "blocked":
        raise ProposalError(
            f"self-approval blocked: {sender!r} proposed this change "
            f"(config.self_approval == 'blocked')"
        )
    reasoning = args.get("reasoning", "") or ""
    if self_approving and cfg.self_approval == "warn" and not reasoning.strip():
        raise ProposalError(
            "self-approval requires non-empty reasoning (config."
            "self_approval == 'warn')"
        )

    new_state = {
        "accept": "accepted",
        "reject": "rejected",
        "refine": "refining",
    }[verdict]

    spec_version = None
    if (
        verdict == "accept"
        and proposal.target
        and (
            proposal.target == "ticket://spec"
            or proposal.target.startswith("ticket://spec.")
        )
    ):
        await _enforce_section_locks(
            tickets=tickets,
            threads=threads,
            proposal=proposal,
            applied_change=args.get("applied_change"),
            project_path=project_path,
        )
        spec_version = await _apply_spec_change(
            proposal=proposal,
            applied_change=args.get("applied_change"),
            project_path=project_path,
        )

    resolver = Proposal(
        ticket_id=proposal.ticket_id,
        author=sender,
        target=proposal.target,
        section=proposal.section,
        change=args.get("applied_change"),
        rationale=reasoning or f"{verdict}ed proposal {proposal.id}",
        state=new_state,  # type: ignore[arg-type]
        parent_id=proposal.id,
        owners=list(proposal.owners),
        spec_version=spec_version,
    )
    cid = await threads.post(resolver)

    # Flip the original proposal to the resolved state so re-resolution
    # attempts fail loud rather than silently spawning a second audit
    # entry. The resolver entry remains as the decision record.
    await threads.update(proposal.id, {"state": new_state})

    # Note self-approval with justification in the audit trail.
    if self_approving and cfg.self_approval == "warn":
        marker = SystemEvent(
            ticket_id=proposal.ticket_id,
            author=sender,
            event_type="status_change",
            content=(
                "self_approval_with_justification: true "
                f"(proposal={proposal.id}, resolver={cid})"
            ),
        )
        await threads.post(marker)

    return {
        "comment_id": cid,
        "state": new_state,
        "self_approved": self_approving,
        "spec_version": spec_version,
    }


# ---- list_proposals -------------------------------------------------------


async def handle_list_proposals(
    *,
    threads: ThreadStore,
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return proposals matching the filter. No filter = all proposals.

    Filters: ``ticket_id``, ``state``, ``target``.
    """
    ticket_id = args.get("ticket_id")
    state = args.get("state")
    target = args.get("target")

    if ticket_id:
        found = await threads.find_by_kind(ticket_id, "proposal")
    else:
        found = await threads.all_by_kind("proposal")

    # Always restrict to original proposals (``parent_id is None``).
    # Resolver entries are synthetic Proposals posted by
    # ``handle_resolve_proposal`` that carry ``parent_id=<original.id>``
    # — they exist to record the accept/reject audit trail, not as
    # proposals in their own right. Mixing them into the listing
    # caused I7: a state-filter query like ``state='accepted'`` would
    # return resolver entries, not the original accepted proposals the
    # caller wanted.
    proposals = [e for e in found if e.kind == "proposal" and e.parent_id is None]
    if state:
        proposals = [p for p in proposals if p.state == state]
    if target:
        proposals = [p for p in proposals if p.target == target]

    return [_proposal_to_dict(p) for p in proposals]


# ---- helpers --------------------------------------------------------------


async def _find_proposal(threads: ThreadStore, proposal_id: str) -> Proposal:
    entry = await threads.get(proposal_id)
    if entry is None or entry.kind != "proposal":
        raise KeyError(f"proposal {proposal_id!r} not found")
    assert isinstance(entry, Proposal)  # narrow for type-checkers
    return entry


async def _enforce_section_locks(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    proposal: Proposal,
    applied_change: str | None,
    project_path: Path,
) -> None:
    """Refuse a spec-targeted accept that would edit a locked section.

    Per doc 16: once a phase with ``section_locks.<field>.
    locked_after_phase=<phase>`` has an accepted Handoff on the
    ticket, ``field`` is off-limits. The check runs in the accept
    branch before any persistence happens, so a refused proposal
    stays in the thread untouched for a later reviewer.

    ``ticket://spec.<field>`` names a single field; ``ticket://spec``
    can touch many — we parse the YAML payload (opaque otherwise)
    just far enough to recover the keys and intersect them with the
    lock map.
    """
    ticket = await tickets.get(proposal.ticket_id)
    if ticket is None:
        # No ticket = deeper problem; the spec-apply step downstream
        # would fail anyway. Skip lock enforcement rather than shadow
        # that error.
        return

    locked = await locked_sections_for_ticket(
        project_path, threads, ticket.id, ticket.work_type
    )
    if not locked:
        return

    target = proposal.target or ""
    affected: set[str]
    if target == "ticket://spec":
        import yaml as _yaml

        payload = applied_change or proposal.change or ""
        parsed = _yaml.safe_load(payload) if payload.strip() else {}
        if not isinstance(parsed, dict):
            # Malformed payload — let ``_apply_spec_change`` raise
            # the proper error; don't mask it with a lock error.
            return
        affected = set(parsed.keys())
    else:
        field = target[len("ticket://spec.") :]
        affected = {field}

    hit = affected & locked.keys()
    if hit:
        fields = ", ".join(sorted(hit))
        phases = ", ".join(sorted({locked[f] for f in hit}))
        raise ProposalError(
            f"section locked: field(s) {fields} on ticket "
            f"{proposal.ticket_id!r} are locked after phase(s) "
            f"{phases}; that phase's handoff has already been accepted"
        )


async def _apply_spec_change(
    *,
    proposal: Proposal,
    applied_change: str | None,
    project_path: Path,
) -> int:
    """Apply an accepted spec proposal and return the new spec version.

    Phase 3 treats ``applied_change`` as opaque YAML — the acceptor
    provides the merged field value (or we fall back to
    ``proposal.change``). The work-type schema still runs against the
    composite spec at write time, so a bad payload fails loud as
    ``SpecValidationError``.
    """
    import yaml as _yaml

    spec = load_ticket_spec(project_path, proposal.ticket_id)
    if spec is None:
        raise ProposalError(
            f"cannot apply proposal to spec: ticket "
            f"{proposal.ticket_id!r} has no spec yet "
            "(create one before proposing changes)"
        )

    target = proposal.target or ""
    change_yaml = applied_change or proposal.change or ""
    if not change_yaml.strip():
        raise ProposalError("accepted spec proposal has no change payload")
    parsed = _yaml.safe_load(change_yaml)

    if target == "ticket://spec":
        if not isinstance(parsed, dict):
            raise ProposalError("whole-spec change must be a YAML mapping of fields")
        new_fields = {**spec.fields, **parsed}
    else:
        field = target[len("ticket://spec.") :]
        new_fields = {**spec.fields, field: parsed}

    next_spec = TicketSpec(
        ticket_id=spec.ticket_id,
        work_type=spec.work_type,
        size=spec.size,
        fields=new_fields,
        version=spec.version + 1,
        created_at=spec.created_at,
    )
    try:
        saved = save_ticket_spec(project_path, next_spec, bump_version=False)
    except SpecValidationError:
        # Fail loud — Phase 3 does not auto-rollback a malformed
        # change. The proposal stays in the thread for the operator
        # to fix.
        raise
    return saved.version


def _routing_to_dict(routing: OwnerRouting) -> dict[str, Any]:
    return {
        "role": routing.role,
        "assignee": routing.assignee,
        "helper_template": routing.helper_template,
        "assignment": routing.assignment,
    }


def _proposal_to_dict(p: Proposal) -> dict[str, Any]:
    return {
        "id": p.id,
        "ticket_id": p.ticket_id,
        "author": p.author,
        "content": p.rationale,
        "target": p.target,
        "section": p.section,
        "change": p.change,
        "state": p.state,
        "owners": list(p.owners),
        "parent_id": p.parent_id,
        "spec_version": p.spec_version,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


__all__ = [
    "ProposalError",
    "handle_list_proposals",
    "handle_propose_change",
    "handle_resolve_proposal",
]
