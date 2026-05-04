"""SpecComplianceReviewer — MVP-scope mechanical spec-compliance check (Track G MVP).

Per ``docs/pm-workflow/design.md`` §"Reviewer federation — selection
logic": "spec-compliance — does it satisfy the behavior AC and
integration AC the ticket cites?"

End-of-ticket and per-commit, deterministic, no LLM. Reads the ticket's
``capability_ids`` + ``suite_id``, loads the suite's
``StructuredSpec``, and checks the ticket's worktree diff for token
references to each capability's behavior AC.

Two checks per cited capability:

1. **capability-not-found-in-spec** — the ticket cites a capability id
   that doesn't exist in the suite's structured spec. Severity:
   critical. Either the spec was rewritten and the ticket carries a
   stale id, or the ticket was authored against a different suite's
   capability namespace; either way the operator needs to look at it
   before merge.

2. **behavior-AC-not-referenced** — for each behavior on a cited
   capability AND each top-level ``acceptance_criteria`` entry on the
   capability itself, the diff must reference at least one significant
   token from the AC text. Severity: important. Mirrors the
   integration-AC reference miss disposition from contract-compliance.

If the ticket has no ``suite_id`` (or no capability_ids), the reviewer
returns the empty list — nothing to check against. The synthetic
operator's bones tickets predate the suite-spec wiring; MVP+ tickets
land with ``suite_id`` populated by the Coordinator.

Default-on for MVP / final layers when the ticket has no explicit
``reviewer_set``. Skipped on bones — the bones budget keeps just the
two cheap mechanical reviewers (contract + cross-cutting); bones
tickets predate the structured-spec coverage on a trivial scenario.
"""
from __future__ import annotations

from pathlib import Path

from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.contract_compliance import (
    _default_worktree_path,
    _git_diff,
    _significant_tokens,
)
from jig.reviewers.dispatch import SPEC_COMPLIANCE_REVIEWER_ID
from jig.spec_loader import suite_structured_path
from jig.spec_schema import Capability, StructuredSpec
from jig.ticket import Ticket


def _capability_uri(suite_id: str, capability_id: str) -> str:
    """URI for the offending capability; mirrors contract-compliance scheme."""
    return f"project://spec/suites/{suite_id}/capabilities/{capability_id}"


def _ac_uri(suite_id: str, capability_id: str, kind: str, anchor: str) -> str:
    """URI for a specific AC inside a capability.

    ``kind`` is ``capability_ac`` for top-level AC entries and
    ``behavior``/``<behavior_id>`` for behavior-scoped AC; ``anchor``
    is the position or behavior id so the operator can navigate to
    the offending entry.
    """
    return (
        f"project://spec/suites/{suite_id}/capabilities/"
        f"{capability_id}#{kind}/{anchor}"
    )


def _capability_not_found_comment(
    capability_id: str,
    suite_id: str,
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.CAPABILITY_NOT_FOUND_IN_SPEC,
        severity=Severity.CRITICAL,
        reviewer=SPEC_COMPLIANCE_REVIEWER_ID,
        prose=(
            f"Ticket cites capability {capability_id!r} but it doesn't "
            f"exist in the structured spec for suite {suite_id!r}. The "
            "spec may have been rewritten without the ticket being "
            "updated, or the ticket was authored against a different "
            "suite's capability namespace. Either fix the ticket's "
            "capability_ids or update the spec; merging with a phantom "
            "capability id will leak into downstream analytics and "
            "review provenance."
        ),
        contract_uri=_capability_uri(suite_id, capability_id),
    )


def _behavior_ac_not_referenced_comment(
    suite_id: str,
    capability_id: str,
    ac_text: str,
    *,
    kind: str,
    anchor: str,
) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType.BEHAVIOR_AC_NOT_REFERENCED,
        severity=Severity.IMPORTANT,
        reviewer=SPEC_COMPLIANCE_REVIEWER_ID,
        prose=(
            f"Capability {capability_id!r} acceptance criterion is not "
            f"referenced in the diff: {ac_text!r}. The dev agent's "
            "changes don't mention any significant terms from the AC; "
            "this is a weak signal that the AC wasn't considered. (MVP "
            "uses token matching; Final replaces this with real "
            "semantic checks.)"
        ),
        contract_uri=_ac_uri(suite_id, capability_id, kind, anchor),
    )


def _check_capability_acs(
    suite_id: str,
    capability: Capability,
    diff_tokens: set[str],
) -> list[ReviewerComment]:
    """Walk a capability's ACs (capability-level + behavior-scoped).

    Each AC entry that has at least one significant token must
    intersect with ``diff_tokens`` or it's flagged. Mirrors the
    contract-compliance integration-AC walk shape.
    """
    out: list[ReviewerComment] = []

    # Top-level capability AC entries (used when the capability has no
    # behaviors; otherwise each behavior carries its own AC list).
    for index, ac_text in enumerate(capability.acceptance_criteria):
        ac_tokens = _significant_tokens(ac_text)
        if not ac_tokens:
            continue
        if ac_tokens.isdisjoint(diff_tokens):
            out.append(
                _behavior_ac_not_referenced_comment(
                    suite_id,
                    capability.id,
                    ac_text,
                    kind="capability_ac",
                    anchor=str(index),
                )
            )

    # Behavior-scoped AC entries.
    for behavior in capability.behaviors:
        for index, ac_text in enumerate(behavior.acceptance_criteria):
            ac_tokens = _significant_tokens(ac_text)
            if not ac_tokens:
                continue
            if ac_tokens.isdisjoint(diff_tokens):
                out.append(
                    _behavior_ac_not_referenced_comment(
                        suite_id,
                        capability.id,
                        ac_text,
                        kind=f"behaviors/{behavior.id}/ac",
                        anchor=str(index),
                    )
                )

    return out


def _load_suite_spec(project_root: Path, suite_id: str) -> StructuredSpec | None:
    """Load the suite's structured spec; ``None`` if missing.

    Missing-spec is a silent skip rather than a notable comment: the
    structured spec lands per-suite during PO L3, so a ticket
    authored before the suite was elaborated legitimately has nothing
    to check against. The contract-compliance reviewer's
    missing-contracts handling owns the visible warning.
    """
    src = suite_structured_path(project_root, suite_id)
    if not src.is_file():
        return None
    import yaml

    data = yaml.safe_load(src.read_text()) or {}
    return StructuredSpec.model_validate(data)


class SpecComplianceReviewer:
    """Mechanical spec-compliance reviewer.

    Stateless. Same shape as ``ContractComplianceReviewer`` so the
    federation dispatch table can treat them uniformly. ``async`` for
    symmetry with future judgment reviewers.

    Returns the empty list when:

    * Ticket has no ``suite_id`` or ``capability_ids`` — nothing to
      check.
    * The suite's ``spec.structured.yaml`` is absent.
    * The diff is empty (contract-compliance owns that critical;
      duplicating it here would noise up the federation comment set).
    """

    reviewer_id: str = SPEC_COMPLIANCE_REVIEWER_ID

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
        *,
        worktree_path: Path | None = None,
        base_ref: str = "main",
    ) -> list[ReviewerComment]:
        """Run the spec-compliance checks. Empty list = pass.

        ``worktree_path`` defaults to the orchestrator-convention
        ``<project_root>/.jig/worktrees/<ticket.id>/``; tests and the
        synthetic operator can override. ``base_ref`` defaults to
        ``main`` per the orchestrator's worktree branching convention.
        """
        if not ticket.capability_ids or ticket.suite_id is None:
            # Nothing to compare against. Bones tickets that predate
            # MVP suite wiring legitimately reach this branch; the
            # contract-compliance reviewer surfaces capability-id gaps
            # via its own dispatch.
            return []

        spec = _load_suite_spec(project_root, ticket.suite_id)
        if spec is None:
            return []

        wt = worktree_path or _default_worktree_path(project_root, ticket.id)
        diff = _git_diff(wt, base_ref)
        if not diff.strip():
            return []

        diff_tokens = _significant_tokens(diff)
        comments: list[ReviewerComment] = []

        # Build a lookup once; every cited capability either resolves
        # or trips the not-found comment.
        by_id: dict[str, Capability] = {c.id: c for c in spec.capabilities}

        for cap_id in ticket.capability_ids:
            cap = by_id.get(cap_id)
            if cap is None:
                # Also try alias resolution to support spec-renames.
                cap = spec.capability_by_id_or_alias(cap_id)
            if cap is None:
                comments.append(
                    _capability_not_found_comment(cap_id, ticket.suite_id)
                )
                continue
            comments.extend(
                _check_capability_acs(ticket.suite_id, cap, diff_tokens)
            )

        return comments


__all__ = ["SpecComplianceReviewer"]
