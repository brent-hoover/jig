"""IntentComplianceReviewer — MVP-scope mechanical intent-layer enforcement (Track I MVP).

End-of-ticket, deterministic, no LLM. Per
``docs/agent-leverage/problem.md`` §1: agents fill the
``problem`` / ``simplest_solution`` / ``complications_considered``
sequence on every authored artifact, and the reviewer's job is to
catch the failure modes humans can't reliably catch by skimming —
boilerplate, restatement, skipped steps.

Three deterministic checks per artifact's ``Intent``:

1. **Length** — ``problem`` and ``simplest_solution`` must each be
   at least 20 characters. Anything shorter is almost certainly the
   agent papering over the field. Returns ``intent-too-short``
   comments (severity IMPORTANT — the artifact is technically valid
   but the intent record won't be useful at retrospective time).

2. **Boilerplate restatement** — flags ``simplest_solution`` whose
   significant tokens overlap > 50% with ``problem`` significant
   tokens. The most common authoring failure per docs §1: agent
   jumps to the elegant answer and "the simplest solution" becomes
   a rephrasing of the proposal rather than a genuine simpler
   alternative. Returns ``intent-boilerplate-restatement``.

3. **Empty complications** — flags
   ``complications_considered`` where all four canonical fields
   are ``None`` AND no extra keys were added. Legitimate
   "no complications apply" must be filled with explicit prose
   (``"none — N/A"``) so the absence is intentional rather than
   skipped. Returns ``intent-complications-skipped``.

Token analysis reuses the same significant-token helper as
``ContractComplianceReviewer`` so the reviewer's shape across the
federation stays uniform. Length thresholds are intentionally
conservative — false-positives waste agent cycles, false-negatives
just degrade retrospective signal.

The reviewer operates on Pydantic model instances (Module,
DataContract, BehavioralContract, Risk, Epic) rather than raw
files; it's the synthetic operator's responsibility to load the
contracts and pass the typed artifacts in. That keeps the
reviewer dependency-free of the YAML loader.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from jig.intent import ComplicationsConsidered, Intent
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.reviewers.contract_compliance import _significant_tokens
from jig.reviewers.dispatch import INTENT_REVIEWER_ID

# Minimum length on the intent prose fields. Twenty characters is
# the floor that catches the obvious thin-fill cases ("x", "todo",
# "N/A") without rejecting legitimately terse one-liners. Bumping
# this is cheap if false-positives prove a problem.
_MIN_PROSE_LEN = 20

# Boilerplate-restatement threshold. > 50% overlap of significant
# tokens between simplest_solution and problem signals the agent
# rephrased the problem rather than offering a simpler alternative.
# Set deliberately at 0.5 (strictly greater than) — equal-share
# vocabulary is normal between two-sentence prose blocks on the
# same domain; > half is restatement.
_RESTATEMENT_OVERLAP_THRESHOLD = 0.5

# Re-export under a domain-specific name so callers reading the
# intent-reviewer code don't have to know the federation-wide enum
# name. The two are the same enum — just two import-site spellings
# for readability.
IntentCommentType = ReviewerCommentType


def _contract_uri(kind: str, artifact_id: str) -> str:
    """Pin the comment back to a referenceable artifact URI.

    URI scheme mirrors the existing ``project://arch/...`` shape that
    ContractComplianceReviewer emits. The ``intent`` segment lives
    inside the artifact's URI; the operator can tell at a glance
    which authored artifact a comment belongs to.
    """
    return f"project://intent/{kind.lower()}/{artifact_id}#intent"


def _check_length(intent: Intent, kind: str, artifact_id: str) -> list[ReviewerComment]:
    """Both ``problem`` and ``simplest_solution`` must clear the floor.

    Pydantic min_length=1 already keeps these from being empty; the
    20-char floor catches "x", "todo", "fix bug" — the actual
    failure mode where the agent generated *something* without
    thinking.
    """
    out: list[ReviewerComment] = []
    if len(intent.problem.strip()) < _MIN_PROSE_LEN:
        out.append(
            ReviewerComment(
                type=ReviewerCommentType.INTENT_TOO_SHORT,
                severity=Severity.IMPORTANT,
                reviewer=INTENT_REVIEWER_ID,
                prose=(
                    f"{kind} {artifact_id!r}: ``problem`` is shorter than "
                    f"{_MIN_PROSE_LEN} characters. The intent layer "
                    "exists so retrospectives can answer 'what was this "
                    "actually solving' — a one-word problem statement "
                    "won't carry that. Restate the problem concretely."
                ),
                contract_uri=_contract_uri(kind, artifact_id),
            )
        )
    if len(intent.simplest_solution.strip()) < _MIN_PROSE_LEN:
        out.append(
            ReviewerComment(
                type=ReviewerCommentType.INTENT_TOO_SHORT,
                severity=Severity.IMPORTANT,
                reviewer=INTENT_REVIEWER_ID,
                prose=(
                    f"{kind} {artifact_id!r}: ``simplest_solution`` is "
                    f"shorter than {_MIN_PROSE_LEN} characters. The "
                    "simplest-solution step is supposed to capture the "
                    "dumbest workable approach so any added complexity is "
                    "earned; a stub doesn't do that work."
                ),
                contract_uri=_contract_uri(kind, artifact_id),
            )
        )
    return out


def _check_boilerplate(intent: Intent, kind: str, artifact_id: str) -> list[ReviewerComment]:
    """Flag simplest_solution that's a rephrasing of problem.

    Token-overlap heuristic: if more than half of simplest_solution's
    significant tokens already appeared in problem, the agent was
    almost certainly rephrasing the problem. The technical
    vocabulary of a genuine simpler alternative differs from the
    problem's vocabulary — "deduplicate by hashing" vs "we need
    deduplication"; the former introduces "hashing", the latter
    doesn't.
    """
    problem_tokens = _significant_tokens(intent.problem)
    solution_tokens = _significant_tokens(intent.simplest_solution)
    if not solution_tokens:
        # No significant tokens at all in the solution — either it's
        # too short (caught by _check_length) or it's all stopwords
        # (low signal but not strictly boilerplate). Don't double-flag.
        return []
    overlap = len(problem_tokens & solution_tokens) / len(solution_tokens)
    if overlap > _RESTATEMENT_OVERLAP_THRESHOLD:
        return [
            ReviewerComment(
                type=ReviewerCommentType.INTENT_BOILERPLATE_RESTATEMENT,
                severity=Severity.IMPORTANT,
                reviewer=INTENT_REVIEWER_ID,
                prose=(
                    f"{kind} {artifact_id!r}: ``simplest_solution`` "
                    f"shares {overlap:.0%} of its significant vocabulary "
                    "with ``problem``, which suggests it's a restatement "
                    "rather than an actually-simpler alternative. The "
                    "step is meant to force the dumbest-workable answer; "
                    "if the simpler answer happens to look like the "
                    "proposal, name what makes it dumber (skip the "
                    "index, hardcode the value, do it inline)."
                ),
                contract_uri=_contract_uri(kind, artifact_id),
            )
        ]
    return []


def _complications_were_considered(c: ComplicationsConsidered) -> bool:
    """True if any canonical or extra complication field was filled.

    ``None`` on every canonical field AND no extra keys means the
    agent skipped the step. Legitimate "no complications apply" is
    explicit prose like ``"none — N/A"``, not ``None`` — the
    distinction is what lets retrospectives tell "considered and
    rejected" apart from "didn't think about it".

    ``model_extra`` returns ``None`` when no extras exist or an
    empty dict when extras were declared as a set; either way an
    empty mapping == "no extras".
    """
    if any(
        getattr(c, field) is not None
        for field in ("scale", "concurrency", "failure_modes", "cross_cutting")
    ):
        return True
    extras = c.model_extra or {}
    return bool(extras)


def _check_complications(intent: Intent, kind: str, artifact_id: str) -> list[ReviewerComment]:
    if _complications_were_considered(intent.complications_considered):
        return []
    return [
        ReviewerComment(
            type=ReviewerCommentType.INTENT_COMPLICATIONS_SKIPPED,
            severity=Severity.IMPORTANT,
            reviewer=INTENT_REVIEWER_ID,
            prose=(
                f"{kind} {artifact_id!r}: ``complications_considered`` "
                "has all four canonical fields unset and no problem-"
                "specific complications. The step is meant to surface "
                "what pushed the artifact past its simplest form — if "
                "no complications apply, fill each field with explicit "
                "prose (e.g. ``\"none — N/A\"``) so the absence is "
                "intentional rather than skipped."
            ),
            contract_uri=_contract_uri(kind, artifact_id),
        )
    ]


def review_intent(
    intent: Intent,
    *,
    kind: str,
    artifact_id: str,
) -> list[ReviewerComment]:
    """Run the three intent checks against one ``Intent`` block.

    Function form lives here so callers that already have a parsed
    ``Intent`` (e.g. an MCP handler validating a single artifact in
    isolation) don't have to construct the artifact wrapper.
    """
    comments: list[ReviewerComment] = []
    comments.extend(_check_length(intent, kind, artifact_id))
    comments.extend(_check_boilerplate(intent, kind, artifact_id))
    comments.extend(_check_complications(intent, kind, artifact_id))
    return comments


# Mapping of artifact kinds the reviewer recognizes → the field name
# carrying the Intent. Centralized so adding a new intent-bearing
# artifact (e.g. when ticket-level Intent lands) means one entry
# rather than touching the dispatch logic.
_INTENT_BEARING: dict[str, str] = {
    "Module": "intent",
    "DataContract": "intent",
    "BehavioralContract": "intent",
    "Risk": "intent",  # optional on Risk; reviewer skips when None
    "Epic": "intent",
}


def _artifact_kind(artifact: BaseModel) -> str | None:
    """Return the kind label the reviewer uses for ``artifact``.

    Uses the class name directly. Returns ``None`` for artifact types
    the reviewer doesn't recognize (so the synthetic operator can
    pass mixed lists without filtering upfront).
    """
    name = type(artifact).__name__
    return name if name in _INTENT_BEARING else None


def _artifact_id(artifact: BaseModel) -> str:
    """Best-effort id extraction. Every intent-bearing artifact has ``id``."""
    return getattr(artifact, "id", "<unknown>")


class IntentComplianceReviewer:
    """Mechanical intent-layer enforcement reviewer.

    Stateless. Same shape as ``ContractComplianceReviewer`` so the
    federation dispatch table can treat them uniformly. ``async``
    for symmetry with future judgment reviewers (which await LLM
    calls); the work itself is synchronous because the checks are
    deterministic.
    """

    reviewer_id: str = INTENT_REVIEWER_ID

    async def review_artifacts(
        self,
        artifacts: list[Any],
    ) -> list[ReviewerComment]:
        """Return all intent comments across the given artifacts.

        Unrecognized artifact types are silently skipped (the call
        site can pass any mix of authored objects). Artifacts with
        no intent block (e.g. ``Risk`` at status=open) are also
        skipped — the schema permits absence for them.
        """
        out: list[ReviewerComment] = []
        for artifact in artifacts:
            kind = _artifact_kind(artifact)
            if kind is None:
                continue
            field = _INTENT_BEARING[kind]
            intent = getattr(artifact, field, None)
            if intent is None:
                # Risk.intent is optional; absence isn't a finding.
                continue
            out.extend(
                review_intent(intent, kind=kind, artifact_id=_artifact_id(artifact))
            )
        return out


__all__ = [
    "INTENT_REVIEWER_ID",
    "IntentCommentType",
    "IntentComplianceReviewer",
    "review_intent",
]
