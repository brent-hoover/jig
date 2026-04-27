"""Merge a parsed brief with an existing StructuredSpec.

Preserves operator-owned metadata (id, aliases, created_at,
state_changed_at, tickets) for matched capabilities; assigns new
metadata to capabilities new in the brief; surfaces removed-from-brief
capabilities as blocking gaps for the operator to resolve.

Pure function: takes a ParsedBriefResult, an optional existing
StructuredSpec, and a ticket-lookup callback. Returns a
RegenerationResult containing either a new StructuredSpec or a list
of blocking gaps.

See ``docs/project-spec-schema/design.md`` §"Regeneration semantics".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from jig.brief_parser import (
    BriefBehavior, BriefCapability, BriefUserStory,
    ParsedBriefResult,
)
from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec, UserStory,
)


# Map BriefCapability.section → CapabilityState. in_progress is detected
# in Phase 4 by ticket-store inspection, so this map only handles the
# brief-section-to-base-state mapping.
_SECTION_TO_STATE: dict[str, CapabilityState] = {
    "built": CapabilityState.BUILT,
    "planned_committed": CapabilityState.PLANNED,
    "planned_not_committed": CapabilityState.PLANNED,
    "backlog": CapabilityState.BACKLOG,
    "archived": CapabilityState.ARCHIVED,
}


# Type alias: (capability_id, aliases) → list of ticket IDs.
TicketLookup = Callable[[str, list[str]], list[str]]


@dataclass
class RegenerationGap:
    """A blocking issue surfaced during regeneration."""
    kind: str             # 'removed_from_brief', 'ambiguous_alias', etc.
    location: str         # human-readable
    description: str
    severity: str = "blocking"
    suggested_question: str | None = None


@dataclass
class RegenerationResult:
    spec: StructuredSpec | None
    gaps: list[RegenerationGap] = field(default_factory=list)


def regenerate(
    *,
    brief: ParsedBriefResult,
    existing: StructuredSpec | None,
    ticket_lookup: TicketLookup,
    now: datetime,
) -> RegenerationResult:
    """Merge brief content with existing spec metadata. See module docstring."""
    capabilities, gaps = _merge_capabilities(brief, existing, ticket_lookup, now)
    non_goals, ng_gaps = _merge_non_goals(brief, existing)
    gaps.extend(ng_gaps)

    if gaps:
        # Any blocking gap aborts the publish — return gaps without a spec.
        if any(g.severity == "blocking" for g in gaps):
            return RegenerationResult(spec=None, gaps=gaps)

    spec = StructuredSpec(
        name=brief.name,
        summary=brief.summary,
        capabilities=capabilities,
        non_goals=non_goals,
        generated_at=now,
        spec_version=1,
    )
    return RegenerationResult(spec=spec, gaps=gaps)


def _to_capability_new(
    brief_cap: BriefCapability, *, now: datetime, tickets: list[str]
) -> Capability:
    return Capability(
        id=brief_cap.id,
        title=brief_cap.title,
        state=_SECTION_TO_STATE[brief_cap.section],
        summary=brief_cap.summary,
        user_story=_to_user_story(brief_cap.user_story),
        behaviors=[_to_behavior(b) for b in brief_cap.behaviors],
        acceptance_criteria=brief_cap.capability_acceptance_criteria,
        excluded=brief_cap.excluded,
        open_questions=brief_cap.open_questions,
        tickets=tickets,
        aliases=list(brief_cap.aliases),
        created_at=now,
        last_updated=now,
        state_changed_at=now,
    )


def _to_user_story(s: BriefUserStory | None) -> UserStory | None:
    if s is None:
        return None
    return UserStory(**{"as": s.as_, "want": s.want, "benefit": s.benefit})


def _to_behavior(b: BriefBehavior) -> Behavior:
    return Behavior(
        id=b.id,
        description=b.description,
        examples=b.examples,
        acceptance_criteria=b.acceptance_criteria,
    )


def _merge_capabilities(
    brief: ParsedBriefResult,
    existing: StructuredSpec | None,
    ticket_lookup: TicketLookup,
    now: datetime,
) -> tuple[list[Capability], list[RegenerationGap]]:
    out: list[Capability] = []
    matched_ids: set[str] = set()

    for brief_cap in brief.capabilities:
        match = None
        if existing is not None:
            match = existing.capability_by_id_or_alias(brief_cap.id)
            if match is None:
                for alias in brief_cap.aliases:
                    candidate = existing.capability_by_id_or_alias(alias)
                    if candidate is not None:
                        match = candidate
                        break
        tickets = ticket_lookup(brief_cap.id, brief_cap.aliases)
        if match is None:
            out.append(_to_capability_new(brief_cap, now=now, tickets=tickets))
        else:
            matched_ids.add(match.id)
            out.append(_merge_one(brief_cap, match, tickets=tickets, now=now))

    # Removed-from-brief gaps
    gaps: list[RegenerationGap] = []
    if existing is not None:
        for ec in existing.capabilities:
            if ec.id in matched_ids:
                continue
            gaps.append(RegenerationGap(
                kind="removed_from_brief",
                location=f"capability {ec.id!r} (state={ec.state.value})",
                description=(
                    f"capability {ec.id!r} is in structured.yaml but not in "
                    "the brief. Add it to a brief section (Built / Planned / "
                    "Backlog / Archived), or add it as an alias to another "
                    "capability if you renamed it."
                ),
            ))
    return out, gaps


def _merge_one(
    brief_cap: BriefCapability,
    match: Capability,
    *,
    tickets: list[str],
    now: datetime,
) -> Capability:
    new_state = _SECTION_TO_STATE[brief_cap.section]
    state_changed = match.state_changed_at if new_state == match.state else now
    return Capability(
        # Brief is source of truth for id + aliases (operator may rename via brief)
        id=brief_cap.id,
        aliases=list(brief_cap.aliases),
        title=brief_cap.title,
        state=new_state,
        summary=brief_cap.summary,
        user_story=_to_user_story(brief_cap.user_story),
        behaviors=[_to_behavior(b) for b in brief_cap.behaviors],
        acceptance_criteria=brief_cap.capability_acceptance_criteria,
        excluded=brief_cap.excluded,
        open_questions=brief_cap.open_questions,
        tickets=tickets,
        # Preserved
        created_at=match.created_at,
        # Recomputed
        last_updated=now,
        state_changed_at=state_changed,
    )


def _merge_non_goals(
    brief: ParsedBriefResult,
    existing: StructuredSpec | None,
) -> tuple[list[NonGoal], list[RegenerationGap]]:
    out: list[NonGoal] = []
    matched_ids: set[str] = set()
    for brief_ng in brief.non_goals:
        match = None
        if existing is not None:
            match = existing.non_goal_by_id_or_alias(brief_ng.id)
            if match is None:
                for alias in brief_ng.aliases:
                    candidate = existing.non_goal_by_id_or_alias(alias)
                    if candidate is not None:
                        match = candidate
                        break
        out.append(NonGoal(
            id=brief_ng.id,
            text=brief_ng.text,
            rationale=brief_ng.rationale,
            aliases=list(brief_ng.aliases),
        ))
        if match is not None:
            matched_ids.add(match.id)

    gaps: list[RegenerationGap] = []
    if existing is not None:
        for eng in existing.non_goals:
            if eng.id in matched_ids:
                continue
            gaps.append(RegenerationGap(
                kind="removed_from_brief",
                location=f"non-goal {eng.id!r}",
                description=(
                    f"non-goal {eng.id!r} is in structured.yaml but not in "
                    "the brief. Add it back to ## Non-goals or add it as an "
                    "alias to another non-goal if you renamed it."
                ),
            ))
    return out, gaps
