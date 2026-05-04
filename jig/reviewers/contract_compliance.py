"""ContractComplianceReviewer — bones-scope mechanical reviewer (Track G2).

End-of-ticket, deterministic, no LLM. Runs three checks against the
ticket's worktree diff:

1. **Existence check** — the diff is non-empty (dev agent committed
   something). Empty diff returns one ``empty-diff`` critical comment.
2. **Module path check** — at least one file changed. Implemented
   together with (1) for bones — both reduce to "is the unified diff
   non-empty?" The full module-to-source-path mapping that lets us
   say "this file belongs to this module" lands in MVP (Track G2 MVP
   addition); bones treats every changed file as in-scope.
3. **Integration AC reference check** — for each ``IntegrationAcceptance.must``
   entry whose ``capability`` is in ``ticket.capability_ids``, the
   diff text must contain at least one significant token from the AC
   text. This is intentionally a weak signal — it catches "the dev
   agent didn't even read the AC" without claiming to verify the AC
   is satisfied. MVP replaces this with real semantic checks (G6
   spec-compliance with LLM judgment).

Diff source: ``git diff <base>..HEAD`` from the ticket's worktree
path. We tolerate the absence of a worktree, the absence of git
itself in the environment, and an empty diff — all three reduce to
"empty diff" comments rather than raised exceptions, because the
synthetic operator has no recourse if review crashes (the dev agent
already finished).

Worktree path resolution: the orchestrator places worktrees at
``<project_root>/.jig/worktrees/<ticket_id>/`` per
``jig.worktree.create_worktree``. The reviewer reads from there.
For test setups that don't use the orchestrator's layout, the
explicit ``worktree_path`` argument overrides the convention.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.dispatch import BONES_REVIEWER_ID
from jig.spec_loader import load_module_contracts
from jig.ticket import Ticket

# Stopwords intentionally tiny + inline. The full English stopword list
# adds about 150 words and a dependency; bones gets the high-frequency
# tokens that would otherwise dominate the AC-reference signal. Adding
# more here is cheap if false-positives prove a problem in practice.
_STOPWORDS: frozenset[str] = frozenset(
    {
        "must",
        "should",
        "shall",
        "with",
        "from",
        "into",
        "that",
        "this",
        "when",
        "then",
        "than",
        "have",
        "been",
        "will",
        "each",
        "such",
        "they",
        "them",
        "their",
        "what",
        "which",
        "where",
        "while",
        "after",
        "before",
        "every",
        "some",
        "more",
        "most",
        "less",
        "least",
        "only",
        "also",
        "above",
        "below",
        "between",
        "across",
        "during",
        "without",
        "within",
        "about",
        "other",
        "another",
        "those",
        "these",
        "there",
        "here",
        "does",
        "doesnt",
        "isnt",
        "wasnt",
        "arent",
        "cant",
        "wont",
        "would",
        "could",
        "should",
        "shouldnt",
        "wouldnt",
        "couldnt",
    }
)

# Minimum token length. ``[a-zA-Z]{4,}`` rejects two- and three-letter
# words like "of", "is", "to", "the", "and" — these dominate AC text
# and would let any commit slip through the reference check.
_TOKEN_RE = re.compile(r"[a-zA-Z]{4,}")


def _significant_tokens(text: str) -> set[str]:
    """Lowercase tokens of length >= 4 that aren't in the stopword set.

    Returned as a set so callers can do membership checks in a single
    pass over the diff. Order doesn't matter — bones only needs "is at
    least one of these in the diff."
    """
    return {
        tok.lower()
        for tok in _TOKEN_RE.findall(text)
        if tok.lower() not in _STOPWORDS
    }


def _git_diff(worktree_path: Path, base_ref: str) -> str:
    """Return ``git diff <base_ref>..HEAD`` from the worktree.

    Returns the empty string in every failure mode (no git binary, no
    repo, no commits, base_ref absent). The reviewer interprets empty
    output as "no diff to review" — that surfaces as the empty-diff
    critical comment, which is the right disposition: if the worktree
    is in a state where we can't even diff it, the dev agent didn't
    produce reviewable work.
    """
    if not worktree_path.is_dir():
        return ""
    try:
        proc = subprocess.run(
            ["git", "diff", f"{base_ref}..HEAD"],
            cwd=worktree_path,
            capture_output=True,
            check=False,
            text=True,
        )
    except (FileNotFoundError, OSError):
        return ""
    if proc.returncode != 0:
        # `git diff base..HEAD` fails when base_ref doesn't exist or
        # HEAD has no commits. Fall back to the working-tree diff so
        # uncommitted bones changes still surface for review.
        try:
            fallback = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                check=False,
                text=True,
            )
        except (FileNotFoundError, OSError):
            return ""
        if fallback.returncode != 0:
            return ""
        return fallback.stdout
    return proc.stdout


def _default_worktree_path(project_root: Path, ticket_id: str) -> Path:
    """Match the convention in ``jig.worktree.create_worktree``."""
    return project_root / ".jig" / "worktrees" / ticket_id


class ContractComplianceReviewer:
    """Bones-scope mechanical contract-compliance reviewer.

    Stateless. Construct once and call ``review`` for each ticket; no
    per-ticket setup. The class wrapper exists so MVP can add reviewer-
    level config (cache, prompt template, etc.) without changing the
    invocation shape at the synthetic operator's call site.
    """

    reviewer_id: str = BONES_REVIEWER_ID

    async def review(
        self,
        ticket: Ticket,
        project_root: Path,
        *,
        worktree_path: Path | None = None,
        base_ref: str = "main",
    ) -> list[ReviewerComment]:
        """Run the bones contract-compliance checks. Empty list = pass.

        ``worktree_path`` defaults to the orchestrator-convention
        ``<project_root>/.jig/worktrees/<ticket.id>/``; tests and the
        synthetic operator can override.

        ``base_ref`` defaults to ``main`` because that's what
        ``jig.worktree.create_worktree`` branches off in production
        runs. Test setups that init their fixture repo on a different
        default branch (``master``, ``develop``) pass it through.

        The function is ``async`` for symmetry with future judgment
        reviewers (which will await LLM calls); bones does its work
        synchronously inside the coroutine — there's no I/O to
        parallelize for a deterministic check.
        """
        comments: list[ReviewerComment] = []

        # Check 1+2: existence + at-least-one-file. Combined for bones.
        wt = worktree_path or _default_worktree_path(project_root, ticket.id)
        diff = _git_diff(wt, base_ref)
        if not diff.strip():
            comments.append(
                ReviewerComment(
                    type=ReviewerCommentType.EMPTY_DIFF,
                    severity=Severity.CRITICAL,
                    reviewer=self.reviewer_id,
                    prose=(
                        "The ticket worktree produced no diff against "
                        f"{base_ref!r}. The dev agent must commit at "
                        "least one change before the ticket can be "
                        "reviewed. (If the agent intentionally produced "
                        "no code — e.g. a spike — switch the ticket "
                        "work_type to spike so this reviewer is not run.)"
                    ),
                )
            )
            # Skip downstream checks: with no diff there's nothing to
            # cross-reference against the AC. Returning here keeps the
            # critical comment from being drowned by N "AC X not
            # referenced" comments that all reduce to "no diff".
            return comments

        # Check 3: integration-AC reference. Skipped if module_id or
        # capability_ids are absent — bones tickets without a module_id
        # (operator-shared sqlite scenarios) can't be checked against
        # any contracts file. The synthetic operator surfaces this as
        # a notable-severity comment so the operator notices the gap.
        if ticket.module_id is None:
            comments.append(
                ReviewerComment(
                    type=ReviewerCommentType.CONTRACT_VIOLATION,
                    severity=Severity.NOTABLE,
                    reviewer=self.reviewer_id,
                    prose=(
                        "Ticket has no module_id; cannot resolve a "
                        "contracts.yaml to check the diff against. "
                        "Bones tickets created outside the Coordinator "
                        "may legitimately lack a module_id; the "
                        "Planner (MVP) populates it from the build "
                        "plan."
                    ),
                )
            )
            return comments

        try:
            contracts = load_module_contracts(project_root, ticket.module_id)
        except FileNotFoundError:
            comments.append(
                ReviewerComment(
                    type=ReviewerCommentType.CONTRACT_VIOLATION,
                    severity=Severity.NOTABLE,
                    reviewer=self.reviewer_id,
                    prose=(
                        f"No contracts.yaml found for module "
                        f"{ticket.module_id!r}. The contract-compliance "
                        "reviewer cannot run without it. SA discovery "
                        "may not have authored the module yet, or the "
                        "module_id on the ticket may be stale."
                    ),
                    contract_uri=f"project://arch/modules/{ticket.module_id}/contracts",
                )
            )
            return comments

        diff_tokens = _significant_tokens(diff)
        capability_set = set(ticket.capability_ids)

        for ac in contracts.integration_ac:
            if capability_set and ac.capability not in capability_set:
                # Tickets scope to specific capabilities; integration
                # AC for other capabilities on the same module aren't
                # this ticket's responsibility.
                continue
            for must_text in ac.must:
                must_tokens = _significant_tokens(must_text)
                if not must_tokens:
                    # AC text was all stopwords / short words — nothing
                    # to require. Skip rather than fail; the AC itself
                    # is unreviewable but the contract author owns
                    # that, not the dev agent.
                    continue
                if must_tokens.isdisjoint(diff_tokens):
                    comments.append(
                        ReviewerComment(
                            type=ReviewerCommentType.INTEGRATION_AC_NOT_REFERENCED,
                            severity=Severity.IMPORTANT,
                            reviewer=self.reviewer_id,
                            prose=(
                                f"Integration AC for capability "
                                f"{ac.capability!r} is not referenced "
                                f"in the diff: {must_text!r}. The dev "
                                "agent's changes don't mention any of "
                                "the significant terms from the AC; "
                                "this is a weak signal that the AC "
                                "wasn't considered. (Bones uses token "
                                "matching; MVP replaces this with "
                                "real semantic checks.)"
                            ),
                            contract_uri=(
                                f"project://arch/modules/{ticket.module_id}"
                                f"/contracts#integration_ac/{ac.capability}"
                            ),
                        )
                    )

        return comments


__all__ = ["ContractComplianceReviewer"]
