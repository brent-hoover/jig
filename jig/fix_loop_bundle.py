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

from jig.finding_ids import compute_finding_ids
from jig.reviewer_routing import _route_one

if TYPE_CHECKING:
    from jig.models import WorkflowConfig
    from jig.reviewers.comment import ReviewerComment
    from jig.store.finding_acks import FindingAck


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
) -> dict:
    """Build the bundle for a phase about to be back-routed.

    Includes only findings from the **latest cycle** (the one that just
    blocked) AND whose individual routing target matches
    ``target_phase_idx``. Earlier-cycle findings are not re-rendered
    — they've already been addressed (or not), and the reviewer
    re-flagging path will surface anything still broken in this cycle.

    Each finding carries its full ack history (across all cycles) so
    the agent can see what was previously claimed and rejected.
    """
    if not all_comments:
        return {"findings": [], "overflow_count": 0}

    latest_cycle = max(c.cycle for c in all_comments)
    blocking = [
        c
        for c in all_comments
        if c.cycle == latest_cycle and c.severity in ("critical", "important")
    ]
    if not blocking:
        return {"findings": [], "overflow_count": 0}

    # Compute stable IDs from the full insertion-ordered list so a
    # re-phrased finding still maps to its original RC-N.
    ids = compute_finding_ids(all_comments)

    # Group acks by finding_id for fast lookup.
    acks_by_finding: dict[str, list[FindingAck]] = {}
    for ack in all_acks:
        acks_by_finding.setdefault(ack.finding_id, []).append(ack)

    # Filter blocking comments to those routed to the target phase.
    routed: list[ReviewerComment] = []
    for c in blocking:
        idx, _ = await _route_one(workflow, blocked_phase_idx, c, worktree_path)
        if idx == target_phase_idx:
            routed.append(c)

    findings: list[dict] = []
    for c in routed:
        type_value = c.type.value if hasattr(c.type, "value") else str(c.type)
        sig = (c.reviewer, type_value, c.file, c.line)
        finding_id = ids.get(sig)
        if finding_id is None:
            # Defensive — every blocking comment should have a signature
            # in `ids` because they came from `all_comments`. Skip
            # silently rather than crash if something pathological
            # happens (e.g. a manually edited JSONL row).
            continue
        ack_dicts = [_ack_to_dict(a) for a in acks_by_finding.get(finding_id, [])]
        # De-duplicate by signature within the bundle — a finding
        # re-phrased twice in the same cycle should appear once.
        if any(f["finding_id"] == finding_id for f in findings):
            continue
        findings.append(
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

    overflow = max(0, len(findings) - MAX_RENDERED_FINDINGS)
    findings = findings[:MAX_RENDERED_FINDINGS]
    return {"findings": findings, "overflow_count": overflow}


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
        type_value = c.type.value if hasattr(c.type, "value") else str(c.type)
        sig = (c.reviewer, type_value, c.file, c.line)
        fid = ids.get(sig)
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
        type_value = c.type.value if hasattr(c.type, "value") else str(c.type)
        sig = (c.reviewer, type_value, c.file, c.line)
        fid = ids.get(sig)
        if fid is None or fid in seen_fids:
            continue
        seen_fids.add(fid)

        finding_acks = sorted(acks_by_finding.get(fid, []), key=lambda a: a.cycle)
        latest_addressed = None
        for ack in finding_acks:
            if ack.kind == "addressed":
                latest_addressed = ack
        is_resolved = any(a.kind == "resolved" for a in finding_acks)

        if is_resolved:
            status = "resolved"
        elif latest_addressed is not None:
            status = "addressed"
        else:
            status = "open"

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
                        "cycle": latest_addressed.cycle,
                        "author": latest_addressed.author,
                        "prose": latest_addressed.prose,
                    }
                    if latest_addressed
                    else None
                ),
                "status": status,
            }
        )

    if not findings:
        return None
    return {"findings": findings}


__all__ = [
    "MAX_RENDERED_FINDINGS",
    "build_fix_loop_bundle",
    "build_verify_bundle",
]
