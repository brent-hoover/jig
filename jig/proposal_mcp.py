"""MCP tool handlers for the Proposal mechanism (Phase 3 Task G).

Three tools ship with this module:

* ``propose_change`` — create a Proposal thread entry on a ticket and
  route it to the owning role.
* ``resolve_proposal`` — accept / reject / refine an outstanding
  proposal. Enforces the self-certification guard per doc 04.
* ``list_proposals`` — query helper used by both humans and the TUI.

Proposals are stored as specialized ``Comment`` records (Phase 3E)
rather than their own collection. That avoids a forked store for
something Phase 4 plans to re-home alongside the other typed thread
entries.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jig.config import load_config
from jig.ownership import OwnerRouting, resolve_owner
from jig.specs import (
    SpecValidationError,
    TicketSpec,
    load_ticket_spec,
    save_ticket_spec,
)
from jig.store.comments import CommentStore
from jig.store.tickets import TicketStore
from jig.ticket import Comment
from jig.work_types import load_work_type_schema

_logger = logging.getLogger(__name__)


class ProposalError(ValueError):
    """Raised when a proposal operation is disallowed by configuration."""


# ---- propose_change -------------------------------------------------------


async def handle_propose_change(
    *,
    tickets: TicketStore,
    comments: CommentStore,
    sender: str,
    args: dict[str, Any],
    project_path: Path,
) -> dict[str, Any]:
    """Create a Proposal on a ticket + record the routing decision.

    Required args:
      ticket_id, target (URI), change (content of the proposed change).
    Optional args:
      section, content (prose commentary).
    """
    ticket_id = args["ticket_id"]
    target = args["target"]
    change = args["change"]
    section = args.get("section")
    content = args.get("content") or f"Proposed change to {target}"

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

    proposal = Comment(
        ticket_id=ticket_id,
        author=sender,
        content=content,
        kind="proposal",
        proposal_target=target,
        proposal_section=section,
        proposal_change=change,
        proposal_state="pending",
        proposal_owners=[routing.role],
    )
    cid = await comments.post(proposal)
    return {
        "comment_id": cid,
        "routing": _routing_to_dict(routing),
        "state": "pending",
    }


# ---- resolve_proposal -----------------------------------------------------


async def handle_resolve_proposal(
    *,
    tickets: TicketStore,
    comments: CommentStore,
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
        ``proposal_change`` if absent).
    """
    verdict = args["verdict"]
    if verdict not in {"accept", "reject", "refine"}:
        raise ValueError(
            f"verdict must be accept/reject/refine, got {verdict!r}"
        )

    proposal = await _find_proposal(comments, args["proposal_id"])
    if proposal.proposal_state != "pending":
        raise ProposalError(
            f"proposal {proposal.id} is not pending "
            f"(state={proposal.proposal_state})"
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
    if verdict == "accept" and proposal.proposal_target and (
        proposal.proposal_target == "ticket://spec"
        or proposal.proposal_target.startswith("ticket://spec.")
    ):
        spec_version = await _apply_spec_change(
            comments=comments,
            proposal=proposal,
            applied_change=args.get("applied_change"),
            project_path=project_path,
        )

    resolver = Comment(
        ticket_id=proposal.ticket_id,
        author=sender,
        content=reasoning or f"{verdict}ed proposal {proposal.id}",
        kind="proposal",
        proposal_target=proposal.proposal_target,
        proposal_section=proposal.proposal_section,
        proposal_change=args.get("applied_change"),
        proposal_state=new_state,  # type: ignore[arg-type]
        proposal_parent_id=proposal.id,
        proposal_owners=list(proposal.proposal_owners),
        proposal_spec_version=spec_version,
    )
    cid = await comments.post(resolver)

    # Flip the original proposal to the resolved state so re-resolution
    # attempts fail loud rather than silently spawning a second audit
    # entry. The resolver Comment remains as the decision record.
    await comments._collection.update(
        proposal.id, {"proposal_state": new_state}
    )

    # Note self-approval with justification in the audit trail.
    if self_approving and cfg.self_approval == "warn":
        marker = Comment(
            ticket_id=proposal.ticket_id,
            author=sender,
            kind="status_change",
            content=(
                "self_approval_with_justification: true "
                f"(proposal={proposal.id}, resolver={cid})"
            ),
        )
        await comments.post(marker)

    return {
        "comment_id": cid,
        "state": new_state,
        "self_approved": self_approving,
        "spec_version": spec_version,
    }


# ---- list_proposals -------------------------------------------------------


async def handle_list_proposals(
    *,
    comments: CommentStore,
    args: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return proposals matching the filter. No filter = all proposals.

    Filters: ``ticket_id``, ``state``, ``target``.
    """
    ticket_id = args.get("ticket_id")
    state = args.get("state")
    target = args.get("target")

    if ticket_id:
        found = await comments.for_ticket(ticket_id)
    else:
        # No cheap "all" method on CommentStore; grab via the collection.
        found = await comments._collection.find(
            lambda c: c.kind == "proposal"
        )

    proposals = [c for c in found if c.kind == "proposal"]
    # Only return originating proposals (not resolver entries) unless
    # the caller explicitly asked for a non-pending state.
    if state:
        proposals = [c for c in proposals if c.proposal_state == state]
    else:
        proposals = [
            c for c in proposals if c.proposal_parent_id is None
        ]
    if target:
        proposals = [c for c in proposals if c.proposal_target == target]

    return [_proposal_to_dict(p) for p in proposals]


# ---- helpers --------------------------------------------------------------


async def _find_proposal(
    comments: CommentStore, proposal_id: str
) -> Comment:
    found = await comments._collection.get(proposal_id)
    if found is None or found.kind != "proposal":
        raise KeyError(f"proposal {proposal_id!r} not found")
    return found


async def _apply_spec_change(
    *,
    comments: CommentStore,
    proposal: Comment,
    applied_change: str | None,
    project_path: Path,
) -> int:
    """Apply an accepted spec proposal and return the new spec version.

    Phase 3 treats ``applied_change`` as opaque YAML — the acceptor
    provides the merged field value (or we fall back to
    ``proposal.proposal_change``). The work-type schema still runs
    against the composite spec at write time, so a bad payload fails
    loud as ``SpecValidationError``.
    """
    import yaml as _yaml

    spec = load_ticket_spec(project_path, proposal.ticket_id)
    if spec is None:
        raise ProposalError(
            f"cannot apply proposal to spec: ticket "
            f"{proposal.ticket_id!r} has no spec yet "
            "(create one before proposing changes)"
        )

    target = proposal.proposal_target or ""
    change_yaml = applied_change or proposal.proposal_change or ""
    if not change_yaml.strip():
        raise ProposalError("accepted spec proposal has no change payload")
    parsed = _yaml.safe_load(change_yaml)

    if target == "ticket://spec":
        if not isinstance(parsed, dict):
            raise ProposalError(
                "whole-spec change must be a YAML mapping of fields"
            )
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
        saved = save_ticket_spec(
            project_path, next_spec, bump_version=False
        )
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


def _proposal_to_dict(c: Comment) -> dict[str, Any]:
    return {
        "id": c.id,
        "ticket_id": c.ticket_id,
        "author": c.author,
        "content": c.content,
        "target": c.proposal_target,
        "section": c.proposal_section,
        "change": c.proposal_change,
        "state": c.proposal_state,
        "owners": list(c.proposal_owners),
        "parent_id": c.proposal_parent_id,
        "spec_version": c.proposal_spec_version,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


__all__ = [
    "ProposalError",
    "handle_list_proposals",
    "handle_propose_change",
    "handle_resolve_proposal",
]
