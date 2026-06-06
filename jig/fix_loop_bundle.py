"""Build the fix_loop_bundle handed to a back-routed phase agent.

Given:
- The full chronological set of reviewer comments for the ticket.
- The full ack history (FindingAck rows) for the ticket.
- The workflow + blocked phase + target phase from
  :mod:`jig.reviewer_routing`.

Produces a structured dict the prompt builder's
``_blocking_findings_section`` renders into the agent's prompt.

Pure-ish — the only non-pure piece is the per-comment routing, which
shells out to git when there's a multi-glob ambiguity (already exists
in ``reviewer_routing._route_one``). Tests use a worktree without
a git history so the shell-out is a no-op fallback.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from jig.finding_ids import FindingSignature, compute_finding_ids, signature_of
from jig.reviewer_routing import _route_one
from jig.store.finding_acks import FindingAck

if TYPE_CHECKING:
    from jig.models import WorkflowConfig
    from jig.reviewers.comment import ReviewerComment


# Hard cap on rendered findings per the design. Overflow renders as
# an "(N more — see ...)" pointer rather than blowing the prompt.
MAX_RENDERED_FINDINGS = 30


def _ack_to_dict(ack: "FindingAck") -> dict:
    return {
        "kind": ack.kind,
        "author": ack.author,
        "cycle": ack.cycle,
        "prose": ack.prose,
    }


async def build_fix_loop_bundle(
    *,
    workflow: "WorkflowConfig",
    blocked_phase_idx: int,
    target_phase_idx: int,
    all_comments: "list[ReviewerComment]",
    all_acks: "list[FindingAck]",
    worktree_path: Path,
    in_scope_notable_comments: "list[ReviewerComment] | None" = None,
) -> dict:
    """Build the bundle for a phase about to be back-routed.

    Critical/important findings come from the latest cycle only, routed to
    ``target_phase_idx`` via ``_route_one``. Notable findings come from the
    full ``in_scope_notable_comments`` list (all cycles, pre-filtered by the
    caller via ``_filter_out_of_scope_comments``), restricted to those with
    no satisfying ack. Notables bypass ``_route_one`` and always target dev.

    Each finding carries its full ack history so the agent can see what was
    previously claimed and rejected.
    """
    # Compute stable IDs from the full insertion-ordered list so a
    # re-phrased finding still maps to its original RC-N.
    ids = compute_finding_ids(all_comments) if all_comments else {}

    # Group acks by finding_id for fast lookup.
    acks_by_finding: dict[str, list[FindingAck]] = {}
    for ack in all_acks:
        acks_by_finding.setdefault(ack.finding_id, []).append(ack)

    # --- Blocking path: latest-cycle critical/important, routed ---
    blocking_findings: list[dict] = []
    if all_comments:
        latest_cycle = max(c.cycle for c in all_comments)
        blocking = [
            c
            for c in all_comments
            if c.cycle == latest_cycle and c.severity in ("critical", "important")
        ]
        routed: list[ReviewerComment] = []
        for c in blocking:
            idx, _ = await _route_one(workflow, blocked_phase_idx, c, worktree_path)
            if idx == target_phase_idx:
                routed.append(c)

        seen_fids: set[str] = set()
        for c in routed:
            finding_id = ids.get(signature_of(c))
            if finding_id is None or finding_id in seen_fids:
                continue
            seen_fids.add(finding_id)
            ack_dicts = [_ack_to_dict(a) for a in acks_by_finding.get(finding_id, [])]
            blocking_findings.append(
                {
                    "finding_id": finding_id,
                    "file": c.file,
                    "line": c.line,
                    "severity": c.severity,
                    "reviewer": c.reviewer,
                    "prose": c.prose,
                    "ack_history": ack_dicts,
                }
            )

    # --- Notable path: all cycles, bypass routing, unsatisfied acks only ---
    notable_findings: list[dict] = []
    for c in in_scope_notable_comments or []:
        finding_id = ids.get(signature_of(c))
        if finding_id is None:
            continue
        if any(
            f["finding_id"] == finding_id for f in blocking_findings + notable_findings
        ):
            continue
        acks = acks_by_finding.get(finding_id, [])
        if _notable_is_satisfied(acks):
            continue
        ack_dicts = [_ack_to_dict(a) for a in acks]
        notable_findings.append(
            {
                "finding_id": finding_id,
                "file": c.file,
                "line": c.line,
                "severity": c.severity,
                "reviewer": c.reviewer,
                "prose": c.prose,
                "ack_history": ack_dicts,
            }
        )

    findings = blocking_findings + notable_findings
    if not findings:
        return {"findings": [], "overflow_count": 0}

    overflow = max(0, len(findings) - MAX_RENDERED_FINDINGS)
    findings = findings[:MAX_RENDERED_FINDINGS]
    return {"findings": findings, "overflow_count": overflow}


def _notable_is_satisfied(acks: "list[FindingAck]") -> bool:
    """A notable is satisfied when the latest ack (by append order)
    is 'addressed' or 'resolved'. 'reject' requires reviewer sign-off first;
    'reraised' or absent means still open."""
    if not acks:
        return False
    latest = acks[-1]  # append-ordered; matches ws_server convention
    if latest.kind == "resolved":
        return True
    if latest.kind == "addressed":
        return True
    return False


def build_verify_bundle(
    *,
    all_comments: "list[ReviewerComment]",
    all_acks: "list[FindingAck]",
) -> dict | None:
    """Build the "Previous Cycle Findings" bundle for reviewers on
    cycle 2+ of a ticket with prior addressed claims.

    Returns ``None`` when there are no prior acks at all (cycle 1
    reviewers run unchanged with no verify task).

    Each finding entry carries the **first-appearance prose** (so the
    reviewer sees the original framing, not a re-phrased version),
    the **most recent addressed claim** if any (so the reviewer
    knows what the dev claims they did), and a **status** marker:

    - ``resolved`` — an earlier reviewer already confirmed this fix.
      The current reviewer can skip verification.
    - ``addressed`` — dev claims it's fixed but no reviewer has
      confirmed yet. The current reviewer should verify.
    - ``open`` — no dev claim. The reviewer should re-flag if the
      issue is still present.
    """
    if not all_acks:
        return None
    if not all_comments:
        return None

    ids = compute_finding_ids(all_comments)

    # Index comments by finding_id; capture the FIRST appearance
    # (signature's earliest insertion) for original_prose.
    first_comment: dict[str, ReviewerComment] = {}
    for c in all_comments:
        fid = ids.get(signature_of(c))
        if fid is not None and fid not in first_comment:
            first_comment[fid] = c

    # Group acks by finding_id, then pick the most recent ack of
    # each kind (addressed / resolved) by cycle ascending.
    acks_by_finding: dict[str, list[FindingAck]] = {}
    for ack in all_acks:
        acks_by_finding.setdefault(ack.finding_id, []).append(ack)

    findings: list[dict] = []
    # Iterate in RC-N order so the prompt reads top-to-bottom in the
    # same order as the audit trail.
    seen_fids: set[str] = set()
    for c in all_comments:
        fid = ids.get(signature_of(c))
        if fid is None or fid in seen_fids:
            continue
        seen_fids.add(fid)

        finding_acks = sorted(acks_by_finding.get(fid, []), key=lambda a: a.cycle)
        is_resolved = any(a.kind == "resolved" for a in finding_acks)

        # Status uses "latest ack kind wins" (with resolved as terminal)
        # so a finding whose dev claim was reraised reads "reraised", not
        # "addressed". Matches ws_server._get_findings_for_ticket.
        if is_resolved:
            status = "resolved"
        elif finding_acks:
            status = finding_acks[-1].kind
        else:
            status = "open"

        # Surface the latest dev claim (addressed or reject) in append
        # order — the same ordering used for status — so same-cycle acks
        # are always consistent between status and dev_claim.
        latest_dev_claim = None
        for ack in finding_acks:
            if ack.kind in ("addressed", "reject"):
                latest_dev_claim = ack

        first = first_comment[fid]
        findings.append(
            {
                "finding_id": fid,
                "file": first.file,
                "line": first.line,
                "severity": first.severity,
                "reviewer": first.reviewer,
                "original_prose": first.prose,
                "dev_claim": (
                    {
                        "cycle": latest_dev_claim.cycle,
                        "author": latest_dev_claim.author,
                        "prose": latest_dev_claim.prose,
                        "kind": latest_dev_claim.kind,
                    }
                    if latest_dev_claim
                    else None
                ),
                "status": status,
            }
        )

    if not findings:
        return None
    return {"findings": findings}


def compute_reraised_acks(
    *,
    prior_comments: "list[ReviewerComment]",
    new_comments: "list[ReviewerComment]",
    prior_acks: "list[FindingAck]",
    ticket_id: str,
) -> "list[FindingAck]":
    """Detect re-flagged findings and produce auto-``reraised`` acks.

    A finding is "re-raised" when:

    - Its signature (see :func:`jig.finding_ids.signature_of`) was
      already present in ``prior_comments`` (i.e., it's not brand
      new), AND
    - Its signature has no ``resolved`` ack in ``prior_acks``.

    The result list is suitable for direct append to
    ``FindingAcksStore`` — one ack per re-flagged finding, prose
    copied from the new comment so the audit trail is self-contained.

    Pure: no I/O. Caller writes the acks after the federation pass
    completes.
    """
    # Compute IDs across the union so signatures shared between prior
    # and new resolve to the same RC-N.
    union = list(prior_comments) + list(new_comments)
    ids = compute_finding_ids(union)

    # Signatures present in the prior set.
    prior_sigs: set[FindingSignature] = {signature_of(c) for c in prior_comments}

    # finding_ids that already have a resolved ack — those do NOT
    # auto-reraise (the resolution stands).
    resolved_fids: set[str] = {a.finding_id for a in prior_acks if a.kind == "resolved"}

    out: list[FindingAck] = []
    seen_fids: set[str] = set()
    for c in new_comments:
        sig = signature_of(c)
        if sig not in prior_sigs:
            continue  # new finding, not a reraise
        fid = ids.get(sig)
        if fid is None or fid in resolved_fids or fid in seen_fids:
            continue
        seen_fids.add(fid)
        # Author is the orchestrator — this row is auto-written by the
        # post-federation scan, NOT by the reviewer agent. Misattributing
        # to the reviewer would make the audit trail look like an
        # explicit reviewer action when it's really a system inference.
        out.append(
            FindingAck(
                ticket_id=ticket_id,
                finding_id=fid,
                kind="reraised",
                author="orchestrator",
                cycle=c.cycle,
                prose=c.prose,
            )
        )
    return out


__all__ = [
    "MAX_RENDERED_FINDINGS",
    "build_fix_loop_bundle",
    "build_verify_bundle",
    "compute_reraised_acks",
]
