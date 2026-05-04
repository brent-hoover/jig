"""Scenario coverage taxonomy + aggregation (Track H MVP follow-on).

Per ``docs/synthetic-operator/design.md`` §"Coverage metrics", each
scenario tags itself with ``coverage_tags`` naming the workflow paths
it exercises. The aggregate coverage view answers:

- which canonical tags are exercised by at least one scenario?
- which canonical tags have zero scenario coverage (gaps)?
- per-tag, which scenarios cover it?

For MVP scope the taxonomy is a small enum-ish constants module so
tags don't drift across scenario files — every tag a scenario claims
must appear in ``CANONICAL_TAGS`` or schema-load fails. The Final
upgrade adds tier-coverage + persona-x-stage-coverage.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CANONICAL_TAGS",
    "CoverageReport",
    "CoverageThreshold",
    "TagCoverage",
    "compute_coverage",
    "compute_coverage_with_threshold",
    "format_coverage",
]


# Canonical taxonomy. Drawn from the constraint list in the Track H
# follow-on task description; one source of truth so a typo in a
# scenario YAML fails schema validation rather than silently
# producing a tag nobody else claims.
#
# Adding a new tag: append here, then claim it from at least one
# scenario YAML's ``coverage_tags``. Removing a tag is a breaking
# change — bump the schema version + sweep scenario files.
CANONICAL_TAGS: Final[frozenset[str]] = frozenset({
    # PO ladder — L0 pitch, L1 discovery, L2 organizer, L3 brief.
    "po-l0",
    "po-l1",
    "po-l2",
    "po-l3",
    # SA — bones (hand-write), MVP incremental loop, risk register +
    # spike + cascade workflows.
    "sa-bones",
    "sa-incremental",
    "sa-risks",
    "sa-spike-mitigated",
    "sa-spike-confirmed-impossible",
    "sa-cascade",
    # Track C Final — cascade failure-mode mitigations + Coordinator
    # bones-first override per docs/sa-architecture/design.md
    # §"Failure modes and mitigations" + docs/pm-workflow/design.md
    # §"Bones-first ordering".
    "cascade-rejected",
    "cascade-staged",
    "cascade-risk-low-override",
    "cascade-mitigated-with-constraints",
    "cascade-concurrent-hold",
    # PM — Planner agent, Coordinator (bones one-shot, MVP cycle-aware,
    # multi-layer dispatch, DEFERRED queue).
    "pm-planner",
    "pm-coordinator-bones",
    "pm-coordinator-multi-layer",
    "pm-deferred",
    # Dev — mock vs real LLM mode.
    "dev-mock",
    "dev-real",
    # Reviewer federation — bones contract-compliance + the MVP set.
    "reviewer-contract-compliance",
    "reviewer-cross-cutting-policy",
    "reviewer-spec-compliance",
    "reviewer-intent-compliance",
    # Track E MVP — dev-environment provisioning happy-path coverage.
    "dev-provisioning",
    # Track E Final — per_agent_ephemeral provisioning (SQLite + Postgres
    # DB), recorded-fixtures replay + record_new modes, periodic orphan
    # sweeper with operator-confirmation.
    "dev-ephemeral-sqlite",
    "dev-ephemeral-postgres",
    "fixtures-replay",
    "fixtures-record-new",
    "orphan-sweeper",
    # Track D MVP — VD finalize, wireframe linter, visual_compliance
    # reviewer (basic mechanical version).
    "vd-finalize",
    "wireframe-lint",
    "visual-compliance",
    # Track D Final — vision-based screenshot diff (with stub
    # provider), accessibility (WCAG AA mechanical), responsive-
    # design enforcement.
    "vision-diff-stub",
    "reviewer-accessibility",
    "reviewer-responsive",
    # Track I Final — quartermaster feedback loop + Pydantic auto-render
    # on data-contract upsert.
    "quartermaster-feedback",
    "pydantic-renderer-auto",
    # Track G Final — specialty reviewers (LLM-driven judgment),
    # severity-tier disposition policy (mechanical), and the
    # self-check gate that runs before any judgment-reviewer comment
    # lands in the store.
    "reviewer-security",
    "reviewer-performance",
    "reviewer-architectural",
    "severity-disposition",
    "comment-self-check",
    # Track H Final — synthetic operator final scope: two new personas,
    # policy-driven turns, coverage threshold, regression-scenario
    # discipline, and TUI-driving mode.
    "persona-scope-creeper",
    "persona-hostile",
    "policy-driven-turns",
    "coverage-threshold",
    "regression-scenarios",
    "tui-driver-mode",
    # Track F Final — mid-work tier promotion mechanics, estimation
    # calibration loop, manual bones-first override + audit, full
    # cycle view data model.
    "tier-promotion",
    "estimation-calibration",
    "bones-first-override",
    "cycle-view",
    # Track B Final — L1 resume-from-state edge cases, project-ontology
    # operator-edit affordances, multi-level PO TUI slash commands.
    "discovery-resume",
    "ontology-edit",
    "tui-slash-commands",
})


class TagCoverage(BaseModel):
    """Per-tag aggregation: who covers it + how many."""

    model_config = ConfigDict(extra="forbid")

    tag: str
    scenario_ids: list[str] = Field(default_factory=list)

    @property
    def covered(self) -> bool:
        return len(self.scenario_ids) > 0

    @property
    def count(self) -> int:
        return len(self.scenario_ids)


class CoverageReport(BaseModel):
    """Coverage aggregate across the scenario library.

    Built by ``compute_coverage(scenarios)``. Renders to markdown via
    ``format_coverage(report)``. Surfaces gaps (canonical tags with
    zero scenario coverage) prominently — those are the next
    scenarios the operator should write.

    ``meets_threshold`` is populated by
    ``compute_coverage_with_threshold`` (Track H Final); the bare
    ``compute_coverage`` leaves it ``None`` so old call sites stay
    untouched.
    """

    model_config = ConfigDict(extra="forbid")

    total_scenarios: int
    per_tag: dict[str, TagCoverage]
    unknown_tags: list[str] = Field(default_factory=list)
    # Track H Final — populated by compute_coverage_with_threshold; left
    # ``None`` by the threshold-agnostic compute_coverage so existing
    # callers don't have to change.
    meets_threshold: bool | None = None
    threshold_percent: float | None = None
    coverage_percent: float | None = None

    @property
    def covered_tags(self) -> list[str]:
        return sorted(t for t, c in self.per_tag.items() if c.covered)

    @property
    def gap_tags(self) -> list[str]:
        return sorted(t for t, c in self.per_tag.items() if not c.covered)


class CoverageThreshold(BaseModel):
    """Operator-configurable coverage threshold (Track H Final).

    Per the v2-plan's "≥80%" target, the threshold sets the floor for
    canonical-tag coverage. ``enforce_in_ci`` is advisory metadata; the
    actual CI gate is enforced by ``jig sim run-tier --enforce-threshold``
    + ``jig sim coverage --threshold N`` exit codes — those check the
    floor and exit 1 below it. The CI YAML is operator-owned.
    """

    model_config = ConfigDict(extra="forbid")

    min_percent: float = Field(default=80.0, ge=0.0, le=100.0)
    enforce_in_ci: bool = False


# Imported lazily inside the function to avoid a circular import
# (scenario imports assertions imports nothing here, but the public
# ``Scenario`` type lives in ``jig.sim.scenario`` which should depend
# on this module, not the other way round).
def compute_coverage(scenarios: list) -> CoverageReport:  # type: ignore[type-arg]
    """Aggregate ``coverage_tags`` across a list of ``Scenario`` objects.

    Returns a ``CoverageReport`` carrying per-tag coverage state +
    a list of any tags claimed by scenarios that aren't in
    ``CANONICAL_TAGS`` (validation should have caught those at load
    time, but a mismatched library state surfaces them here too).

    Empty input → report with zero scenarios and the canonical tags
    all present-but-uncovered.
    """
    per_tag: dict[str, TagCoverage] = {
        tag: TagCoverage(tag=tag) for tag in sorted(CANONICAL_TAGS)
    }
    unknown: set[str] = set()
    by_tag: dict[str, list[str]] = defaultdict(list)
    for scn in scenarios:
        for tag in getattr(scn, "coverage_tags", []) or []:
            if tag not in CANONICAL_TAGS:
                unknown.add(tag)
                continue
            by_tag[tag].append(scn.id)
    for tag, ids in by_tag.items():
        per_tag[tag] = TagCoverage(tag=tag, scenario_ids=sorted(set(ids)))
    return CoverageReport(
        total_scenarios=len(scenarios),
        per_tag=per_tag,
        unknown_tags=sorted(unknown),
    )


def compute_coverage_with_threshold(
    scenarios: list,  # type: ignore[type-arg]
    threshold: CoverageThreshold,
) -> CoverageReport:
    """``compute_coverage`` plus threshold gating (Track H Final).

    Populates ``meets_threshold``, ``threshold_percent``, and
    ``coverage_percent`` on the returned report. ``coverage_percent``
    is ``len(covered_tags) / len(CANONICAL_TAGS) * 100`` (i.e. it's
    measured against the canonical taxonomy, not against tags claimed
    in the library — un-claimed canonical tags are gaps and they
    count against the floor).
    """
    report = compute_coverage(scenarios)
    canonical_total = len(CANONICAL_TAGS)
    if canonical_total == 0:
        coverage_pct = 100.0
    else:
        coverage_pct = (len(report.covered_tags) / canonical_total) * 100.0
    report.coverage_percent = round(coverage_pct, 2)
    report.threshold_percent = threshold.min_percent
    report.meets_threshold = coverage_pct >= threshold.min_percent
    return report


def format_coverage(report: CoverageReport) -> str:
    """Markdown rendering of a ``CoverageReport``.

    Three sections: summary line, covered tags w/ scenario list,
    gap tags (canonical tags that no scenario claims). Operator-
    readable; gets piped into ``jig sim coverage`` stdout.
    """
    lines: list[str] = []
    covered = report.covered_tags
    gaps = report.gap_tags
    lines.append("# Scenario coverage report")
    lines.append("")
    lines.append(
        f"Scenarios analyzed: {report.total_scenarios} | "
        f"Tags covered: {len(covered)}/{len(CANONICAL_TAGS)} | "
        f"Gaps: {len(gaps)}"
    )
    if report.coverage_percent is not None:
        gate = "PASS" if report.meets_threshold else "FAIL"
        lines.append(
            f"Coverage: {report.coverage_percent:.2f}% "
            f"(threshold {report.threshold_percent:.2f}%) → {gate}"
        )
    lines.append("")

    lines.append("## Covered tags")
    lines.append("")
    if not covered:
        lines.append("_(no tags covered)_")
    else:
        for tag in covered:
            ids = report.per_tag[tag].scenario_ids
            lines.append(f"- `{tag}` ({len(ids)}): {', '.join(ids)}")
    lines.append("")

    lines.append("## Gap tags (no scenario coverage)")
    lines.append("")
    if not gaps:
        lines.append("_(none — every canonical tag is covered)_")
    else:
        for tag in gaps:
            lines.append(f"- `{tag}`")
    lines.append("")

    if report.unknown_tags:
        lines.append("## Unknown tags (claimed but not in canonical taxonomy)")
        lines.append("")
        for tag in report.unknown_tags:
            lines.append(f"- `{tag}`")
        lines.append("")

    return "\n".join(lines)
