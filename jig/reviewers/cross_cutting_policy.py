"""CrossCuttingPolicyReviewer — MVP-scope mechanical universal-rule check (Track G MVP).

Per ``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation — selection
logic": "cross-cutting-policy — does it violate any universal rule
(PII, secrets, no-direct-cross-module-db)?"

End-of-ticket and per-commit, deterministic, no LLM. Reads the
project's ``architecture.yaml`` and walks every
``Architecture.cross_cutting_policies`` entry against the ticket's
worktree diff.

Two checks per policy, one per polarity:

1. **Negative-polarity** (``polarity: "negative"``) — these encode
   prohibitions ("MUST NOT write directly to another module's owned
   collections", "MUST NOT log secrets"). The reviewer scans the diff
   for token patterns that suggest the rule is violated. Implementation
   is intentionally a weak signal — the same significant-token approach
   as contract-compliance, applied to the rule prose. A real semantic
   check (AST analysis, ownership graph traversal) lands in the Final
   tier; MVP catches the obvious cases without an LLM.

   Emits ``cross-cutting-policy-violation`` (severity: critical).
   Critical because negative policies are by-construction "do not do
   this ever" rules; if the diff matches, blocking is the right
   disposition.

2. **Positive-polarity** (``polarity: "positive"``) WITH
   ``auto_generates_integration_ac: true`` — these encode universal
   MUST rules ("every public endpoint MUST emit an audit event"). The
   ``auto_generates_integration_ac`` flag means the rule applies to
   every module's integration AC even though it's authored once at
   the architecture level. Same token-disjoint check as
   contract-compliance: the diff must reference at least one
   significant token from the rule.

   Emits ``cross-cutting-policy-not-referenced`` (severity:
   important). Important rather than critical: the diff might
   legitimately not be the place that satisfies the rule (another
   ticket on the epic might own it). The operator decides.

Positive-polarity rules WITHOUT ``auto_generates_integration_ac`` are
informational — they govern shared-vocabulary design decisions
("treat IDs as opaque strings") rather than enforceable contract
obligations. The MVP reviewer skips them; Final adds a separate
check.

Default-on for **all** layers when the ticket has no explicit
``reviewer_set`` — universal rules apply everywhere, including bones.
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
from jig.reviewers.dispatch import CROSS_CUTTING_REVIEWER_ID
from jig.schemas.arch import ContractPolarity, CrossCuttingPolicy
from jig.spec_loader import load_architecture
from jig.ticket import Ticket


def _policy_uri(policy_id: str) -> str:
    """URI for the offending policy; mirrors the contract-compliance scheme."""
    return f"project://arch/cross_cutting_policies/{policy_id}"


def _negative_violation_comment(
    policy: CrossCuttingPolicy,
    matched_tokens: set[str],
) -> ReviewerComment:
    """Build the critical comment for a negative-polarity match.

    The comment quotes the policy rule and the matched-token set so the
    operator can see exactly what tripped the heuristic. WHY-not-WHAT
    framing: the rule prose IS the why; we add the matched tokens to
    show why the heuristic fired (without claiming the diff genuinely
    violates the rule — token match is a weak signal).
    """
    sample = ", ".join(sorted(matched_tokens)[:5])
    return ReviewerComment(
        type=ReviewerCommentType.CROSS_CUTTING_POLICY_VIOLATION,
        severity=Severity.CRITICAL,
        reviewer=CROSS_CUTTING_REVIEWER_ID,
        prose=(
            f"Cross-cutting policy {policy.id!r} (negative): {policy.rule!r}. "
            f"The diff contains tokens from the rule prose ({sample}); "
            "this is a weak signal that the prohibition may have been "
            "violated. (MVP uses token matching; Final replaces this "
            "with real semantic checks via AST + ownership-graph "
            "analysis.) If this is a false positive, the operator can "
            "override; otherwise the dev agent should rework the diff "
            "so the prohibited construct doesn't appear."
        ),
        contract_uri=_policy_uri(policy.id),
    )


def _positive_not_referenced_comment(
    policy: CrossCuttingPolicy,
) -> ReviewerComment:
    """Build the important comment for a positive-polarity reference miss.

    Mirrors the integration-AC-not-referenced shape from
    contract-compliance — same disposition, same severity. The rule's
    significant tokens didn't appear in the diff, so the dev agent
    plausibly didn't address the universal MUST.
    """
    return ReviewerComment(
        type=ReviewerCommentType.CROSS_CUTTING_POLICY_NOT_REFERENCED,
        severity=Severity.IMPORTANT,
        reviewer=CROSS_CUTTING_REVIEWER_ID,
        prose=(
            f"Cross-cutting policy {policy.id!r} (positive, "
            f"auto-generates integration AC): {policy.rule!r}. The "
            "diff contains none of the significant terms from the rule; "
            "this is a weak signal that the universal MUST wasn't "
            "considered. Another ticket on this epic may legitimately "
            "be the place that satisfies the rule — if so, the operator "
            "can defer this comment."
        ),
        contract_uri=_policy_uri(policy.id),
    )


class CrossCuttingPolicyReviewer:
    """Mechanical cross-cutting-policy enforcement reviewer.

    Stateless. Same shape as ``ContractComplianceReviewer`` so the
    federation dispatch table can treat them uniformly. ``async`` for
    symmetry with future judgment reviewers; the work itself is
    synchronous (token matching, no I/O beyond the one-shot
    architecture.yaml read + git diff).

    Returns the empty list when:

    * ``architecture.yaml`` is absent (SA hasn't run yet — silently
      skipped, same disposition as contract-compliance for a missing
      contracts file). The bones scenario writes architecture.yaml,
      so this only fires in pre-SA setups.
    * No ``cross_cutting_policies`` are declared.
    * The diff is empty (caught by contract-compliance's empty-diff
      critical; we'd duplicate the comment if we re-flagged here).
    """

    reviewer_id: str = CROSS_CUTTING_REVIEWER_ID

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
        *,
        worktree_path: Path | None = None,
        base_ref: str = "main",
    ) -> list[ReviewerComment]:
        """Run the cross-cutting checks. Empty list = pass.

        ``worktree_path`` defaults to the orchestrator-convention
        ``<project_root>/.jig/worktrees/<ticket.id>/``; tests and the
        synthetic operator can override for fixture-style setups.

        ``base_ref`` defaults to ``main`` per the orchestrator's
        worktree branching convention. Test setups on ``develop`` /
        ``master`` pass it through.
        """
        try:
            arch = load_architecture(project_root)
        except FileNotFoundError:
            # No architecture.yaml → no cross-cutting policies to check.
            # Silent rather than a notable comment because the
            # contract-compliance reviewer already surfaces the
            # missing-architecture case via its own contracts.yaml
            # check; we don't need to double-flag.
            return []

        if not arch.cross_cutting_policies:
            return []

        wt = worktree_path or _default_worktree_path(project_root, ticket.id)
        diff = _git_diff(wt, base_ref)
        if not diff.strip():
            # Empty diff is the contract-compliance reviewer's
            # responsibility to flag (CRITICAL empty-diff). We can't
            # check policies against a non-existent diff; returning
            # empty here keeps the federation's per-ticket comment
            # set from carrying duplicate "no diff" criticals.
            return []

        diff_tokens = _significant_tokens(diff)
        comments: list[ReviewerComment] = []

        for policy in arch.cross_cutting_policies:
            rule_tokens = _significant_tokens(policy.rule)
            if not rule_tokens:
                # The rule prose was all stopwords or short words; no
                # tokens to check against. The architect owns that
                # gap, not the dev agent.
                continue

            if policy.polarity == ContractPolarity.NEGATIVE:
                # Negative policies are prohibitions; the diff
                # mentioning the prohibited vocabulary is the heuristic
                # signal that the rule may have been violated. Token
                # intersection (not disjoint) is the trigger.
                matched = rule_tokens & diff_tokens
                if matched:
                    comments.append(_negative_violation_comment(policy, matched))
            elif (
                policy.polarity == ContractPolarity.POSITIVE
                and policy.auto_generates_integration_ac
            ):
                # Positive-with-auto-AC means the rule is a universal
                # MUST. Same disjoint check as integration AC: the
                # diff must reference at least one significant token.
                if rule_tokens.isdisjoint(diff_tokens):
                    comments.append(_positive_not_referenced_comment(policy))
            # Positive-without-auto-AC policies are informational;
            # MVP doesn't check them. Final adds a separate handler.

        return comments


__all__ = ["CrossCuttingPolicyReviewer"]
