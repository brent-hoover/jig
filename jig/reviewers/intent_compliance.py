"""IntentComplianceReviewer — mechanical intent-layer enforcement.

End-of-ticket, deterministic, no LLM. Per
``docs/v2.0/agent-leverage/problem.md`` §1: agents fill the
``problem`` / ``simplest_solution`` / ``complications_considered``
sequence on every authored artifact, and the reviewer's job is to
catch the failure modes humans can't reliably catch by skimming —
boilerplate, restatement, skipped steps.

Per-artifact checks (MVP scope):

1. **Length** — ``problem`` and ``simplest_solution`` must each be
   at least 20 characters.
2. **Boilerplate restatement** — flags ``simplest_solution`` whose
   significant tokens overlap > 50% with ``problem`` significant
   tokens.
3. **Empty complications** — flags ``complications_considered``
   where all four canonical fields are ``None`` AND no extra keys
   were added.

Final-scope additions:

4. **Citation density** — flags ``complications_considered`` where
   none of the canonical prose values contain any concrete
   reference (a file path, a project URI, a ticket id, an identifier
   shape). Pure abstract prose without anchors is the failure mode
   docs §1 calls out: "the agent restates plausible-sounding
   complications without grounding them in the codebase".
5. **Uniqueness** (project-wide) — flags two artifacts whose
   normalized (problem, simplest_solution) tuples match. Indicates
   the agent copy-pasted the intent across artifacts rather than
   thinking through each one.

Token analysis reuses the same significant-token helper as
``ContractComplianceReviewer`` so the reviewer's shape across the
federation stays uniform. Length thresholds are intentionally
conservative — false-positives waste agent cycles, false-negatives
just degrade retrospective signal.

The reviewer operates on Pydantic model instances (Module,
DataContract, BehavioralContract, Risk, Epic, FrontendSpec) rather
than raw files; it's the synthetic operator's responsibility to
load the contracts and pass the typed artifacts in. The
``review_project`` entry point loads every authored v2 artifact off
disk and runs the full pass project-wide; that's what the dispatch
layer wires when a ticket completes at MVP+ layer.
"""

from __future__ import annotations

import re
from pathlib import Path
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

# Citation-density threshold. ``ComplicationsConsidered`` carries
# four canonical prose fields. If every set field is shorter than
# this many characters AND none contains a citation-shaped token,
# the prose is too thin to be considered grounded. Twelve chars is
# "needs covering index" — too generic to be evidence; longer prose
# usually carries enough specificity.
_CITATION_PROSE_FLOOR = 12

# Citation-shape regexes. Together they detect any of:
# - file paths (segments containing ``/`` or ending in ``.py``,
#   ``.yaml``, ``.md``, etc.)
# - project URIs (``project://...``)
# - kebab-case identifiers with two or more segments (e.g.
#   ``catalog-ingest``, ``tb-shopify-connect``)
# - snake_case identifiers two or more segments
#   (e.g. ``write_access``)
# - dotted module paths (``jig.foo.bar``)
# - PascalCase identifiers (e.g. ``ProductRow``)
# - ticket-shaped ids (``t-...``, ``tb-...``, ``spike-...``)
# Matching ANY of these counts as a citation; the heuristic is
# permissive on purpose (false-negatives are better than
# false-positives, per CLAUDE.md error-handling).
_CITATION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"project://"),
    re.compile(r"[A-Za-z0-9_/\.-]+\.(?:py|yaml|yml|md|sql|ts|tsx|js|jsx|json|toml)\b"),
    re.compile(r"\b[a-z][a-z0-9]*(?:-[a-z0-9]+)+\b"),  # kebab two-segment+
    re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b"),  # snake two-segment+
    re.compile(r"\b[a-z][a-z0-9_]*\.[a-z][a-z0-9_.]+\b"),  # dotted module
    re.compile(r"\b(?:[A-Z][a-z0-9]+){2,}\b"),  # PascalCase 2+ words
    re.compile(r"`[^`]+`"),  # backticked code spans
)


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
    """Both ``problem`` and ``simplest_solution`` must clear the floor."""
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


def _check_boilerplate(
    intent: Intent, kind: str, artifact_id: str
) -> list[ReviewerComment]:
    """Flag simplest_solution that's a rephrasing of problem."""
    problem_tokens = _significant_tokens(intent.problem)
    solution_tokens = _significant_tokens(intent.simplest_solution)
    if not solution_tokens:
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
    """True if any canonical or extra complication field was filled."""
    if any(
        getattr(c, field) is not None
        for field in ("scale", "concurrency", "failure_modes", "cross_cutting")
    ):
        return True
    extras = c.model_extra or {}
    return bool(extras)


def _check_complications(
    intent: Intent, kind: str, artifact_id: str
) -> list[ReviewerComment]:
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
                'prose (e.g. ``"none — N/A"``) so the absence is '
                "intentional rather than skipped."
            ),
            contract_uri=_contract_uri(kind, artifact_id),
        )
    ]


def _has_citation(text: str) -> bool:
    """True if ``text`` contains any concrete citation-shaped token.

    Permissive: any match against the citation patterns counts.
    The job is to distinguish "needs covering index at scale" (no
    anchor) from "needs a covering index on `dedup_key`" or
    "see `jig.store.tickets`" or "addressed by ``project://...``".
    """
    return any(p.search(text) for p in _CITATION_PATTERNS)


def _check_citation_density(
    intent: Intent, kind: str, artifact_id: str
) -> list[ReviewerComment]:
    """Flag complications_considered with no concrete references.

    Final-scope check. Only fires when every populated canonical
    field is short prose AND none carry a citation-shaped token.
    "none — N/A" prose is excluded from the check (it's an explicit
    "considered and rejected" — the empty-complications check
    already gates those).
    """
    c = intent.complications_considered
    populated: list[str] = []
    for field in ("scale", "concurrency", "failure_modes", "cross_cutting"):
        val = getattr(c, field)
        if val is None:
            continue
        norm = val.strip().lower()
        if norm.startswith("none") or norm in {"n/a", "na"}:
            # Explicit "doesn't apply" — already valid via the
            # empty-complications check; don't double-flag here.
            continue
        populated.append(val)
    extras = c.model_extra or {}
    for val in extras.values():
        if isinstance(val, str):
            populated.append(val)

    if not populated:
        # Either fully unset (caught by _check_complications) or
        # fully filled with "none — N/A" (intentional — operator
        # signal that no complications apply).
        return []

    # If any populated field carries a citation OR is reasonably
    # long, the prose is grounded enough.
    if any(
        _has_citation(text) or len(text.strip()) >= _CITATION_PROSE_FLOOR * 4
        for text in populated
    ):
        return []
    if any(_has_citation(text) for text in populated):
        return []

    return [
        ReviewerComment(
            type=ReviewerCommentType.INTENT_NO_CITATIONS,
            severity=Severity.NOTABLE,
            reviewer=INTENT_REVIEWER_ID,
            prose=(
                f"{kind} {artifact_id!r}: ``complications_considered`` "
                "is populated but none of the entries reference a "
                "concrete file, identifier, project URI, or ticket id. "
                "Per docs/v2.0/agent-leverage/problem.md §1, the intent "
                "layer is supposed to ground reasoning in the codebase "
                "— add a specific anchor (a file path, a behavioral-"
                "contract id, a `project://` URI) so the complication "
                "is auditable rather than abstract."
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
    """Run the deterministic intent checks against one ``Intent`` block."""
    comments: list[ReviewerComment] = []
    comments.extend(_check_length(intent, kind, artifact_id))
    comments.extend(_check_boilerplate(intent, kind, artifact_id))
    comments.extend(_check_complications(intent, kind, artifact_id))
    comments.extend(_check_citation_density(intent, kind, artifact_id))
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
    "FrontendSpec": "intent",
}


def _artifact_kind(artifact: BaseModel) -> str | None:
    """Return the kind label the reviewer uses for ``artifact``."""
    name = type(artifact).__name__
    return name if name in _INTENT_BEARING else None


def _artifact_id(artifact: BaseModel) -> str:
    """Best-effort id extraction.

    Most intent-bearing artifacts have ``id``; ``FrontendSpec`` is a
    project-singleton so we fall back to a fixed sentinel.
    """
    if hasattr(artifact, "id"):
        return getattr(artifact, "id", "<unknown>")
    if isinstance(artifact, BaseModel):
        return type(artifact).__name__.lower()
    return "<unknown>"


def _normalize_text(text: str) -> str:
    """Whitespace-collapsed, lowercase form for cross-artifact comparison.

    Centralizes the canonicalization so the uniqueness check sees
    "deduplicate by hashing\\nthe sku" and "deduplicate by hashing
    the sku" as equivalent.
    """
    return " ".join(text.lower().split())


def _check_uniqueness(
    intents: list[tuple[str, str, Intent]],
) -> list[ReviewerComment]:
    """Flag artifacts that share a normalized (problem, simplest_solution).

    ``intents`` is a list of (kind, artifact_id, intent) tuples. The
    check groups by the normalized tuple key; any group with > 1
    artifact emits one comment per duplicate (excluding the first,
    so the operator sees "this is a duplicate of the original
    artifact" rather than two comments pointing at each other).
    """
    by_key: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for kind, artifact_id, intent in intents:
        key = (
            _normalize_text(intent.problem),
            _normalize_text(intent.simplest_solution),
        )
        by_key.setdefault(key, []).append((kind, artifact_id))

    out: list[ReviewerComment] = []
    for entries in by_key.values():
        if len(entries) < 2:
            continue
        first_kind, first_id = entries[0]
        for dup_kind, dup_id in entries[1:]:
            out.append(
                ReviewerComment(
                    type=ReviewerCommentType.INTENT_DUPLICATE_ACROSS_ARTIFACTS,
                    severity=Severity.IMPORTANT,
                    reviewer=INTENT_REVIEWER_ID,
                    prose=(
                        f"{dup_kind} {dup_id!r}: ``intent.problem`` and "
                        "``intent.simplest_solution`` match those on "
                        f"{first_kind} {first_id!r}. Copy-paste across "
                        "artifacts is the boilerplate failure mode docs "
                        "§1 flags — every authored artifact's intent "
                        "should describe THAT artifact's specific reason "
                        "to exist. Reword for this artifact's scope."
                    ),
                    contract_uri=_contract_uri(dup_kind, dup_id),
                )
            )
    return out


def _intent_for_artifact(artifact: BaseModel) -> Intent | None:
    """Pull the Intent off an artifact, returning None when absent.

    Centralizes the "is this intent-bearing" lookup so the per-
    artifact and project-wide passes share one code path.
    """
    kind = _artifact_kind(artifact)
    if kind is None:
        return None
    field = _INTENT_BEARING[kind]
    return getattr(artifact, field, None)


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

        Per-artifact checks run on each artifact independently; the
        cross-artifact uniqueness check runs across the whole batch.
        Unrecognized artifact types are silently skipped.
        """
        intents_for_uniqueness: list[tuple[str, str, Intent]] = []
        out: list[ReviewerComment] = []
        for artifact in artifacts:
            kind = _artifact_kind(artifact)
            if kind is None:
                continue
            intent = _intent_for_artifact(artifact)
            if intent is None:
                continue
            artifact_id = _artifact_id(artifact)
            out.extend(review_intent(intent, kind=kind, artifact_id=artifact_id))
            intents_for_uniqueness.append((kind, artifact_id, intent))
        out.extend(_check_uniqueness(intents_for_uniqueness))
        return out

    async def review_project(
        self, project_root: Path
    ) -> dict[str, list[ReviewerComment]]:
        """Run the full intent pass against every authored v2 artifact.

        Loads architecture.yaml + every modules/<m>/contracts.yaml +
        the build plan + the frontend spec, gathers every Intent-
        bearing artifact, runs the per-artifact and cross-artifact
        checks, and returns ``{artifact_uri: [comments]}``. Missing
        files are treated as "not authored yet" — the reviewer only
        gates what's been written.

        The map shape (rather than a flat list) makes operator
        triage easier: the operator sees which artifact each finding
        belongs to without re-parsing ``contract_uri`` on every
        comment. Empty list is the success case.
        """
        from jig.spec_loader import (
            architecture_path,
            frontend_spec_path,
            load_architecture,
            load_build_plan,
            load_frontend_spec,
            load_module_contracts,
        )

        artifacts: list[BaseModel] = []
        if architecture_path(project_root).is_file():
            try:
                arch = load_architecture(project_root)
            except FileNotFoundError:
                arch = None
            if arch is not None:
                artifacts.extend(arch.modules)
                artifacts.extend(arch.risks)
                # Walk per-module contracts.
                modules_dir = project_root / ".jig" / "spec" / "modules"
                if modules_dir.is_dir():
                    for module_id_dir in sorted(modules_dir.iterdir()):
                        if not module_id_dir.is_dir():
                            continue
                        if not (module_id_dir / "contracts.yaml").is_file():
                            continue
                        try:
                            cf = load_module_contracts(project_root, module_id_dir.name)
                        except FileNotFoundError:
                            continue
                        artifacts.extend(cf.behavioral_contracts)
                        artifacts.extend(cf.data_contracts)

        # Build plan epics.
        try:
            plan = load_build_plan(project_root)
        except FileNotFoundError:
            plan = None
        if plan is not None:
            artifacts.extend(plan.epics)

        # Frontend spec (singleton).
        if frontend_spec_path(project_root).is_file():
            try:
                fs = load_frontend_spec(project_root)
                artifacts.append(fs)
            except FileNotFoundError:
                pass

        # Run the per-artifact + cross-artifact checks via
        # review_artifacts so the uniqueness check sees the full set.
        comments = await self.review_artifacts(artifacts)
        out: dict[str, list[ReviewerComment]] = {}
        for c in comments:
            key = c.contract_uri or "<unknown>"
            out.setdefault(key, []).append(c)
        return out


__all__ = [
    "INTENT_REVIEWER_ID",
    "IntentCommentType",
    "IntentComplianceReviewer",
    "review_intent",
]
