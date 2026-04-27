"""Parser for the brief markdown (``.jig/spec/project.md``).

Hand-rolled so we control the anchor syntax (``{#id}`` for definitions,
``[id]`` for references) precisely. Output is an intermediate
representation consumed by the spec-generator's regen merge logic; this
module never produces ``StructuredSpec`` directly.

Format rules (each blocking gap if violated):

1. Every ``### <title>`` heading has a trailing ``{#slug}``.
2. Every bullet under Planned-not-committed / Backlog / Non-goals /
   Archived has a leading ``{#slug}``.
3. Every behavior bullet has a leading ``{#behavior-id}``. Every AC
   bullet has a leading ``[behavior-id]`` reference (or no behavior
   reference when the AC is capability-level).
4. AC ``[behavior-id]`` references resolve to a behavior in the same
   capability.
5. IDs are unique across the brief (capabilities, non-goals, behaviors
   namespaced per capability).
6. Aliases are unique across the brief, no collision with any ``id``.

See ``docs/project-spec-schema/design.md`` §"Brief format".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


BriefSection = Literal[
    "built",
    "planned_committed",
    "planned_not_committed",
    "backlog",
    "archived",
]


@dataclass
class BriefBehavior:
    id: str
    description: str
    examples: list[str] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)


@dataclass
class BriefUserStory:
    as_: str
    want: str
    benefit: str


@dataclass
class BriefCapability:
    id: str
    title: str
    section: BriefSection
    summary: str = ""
    user_story: BriefUserStory | None = None
    behaviors: list[BriefBehavior] = field(default_factory=list)
    capability_acceptance_criteria: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


@dataclass
class BriefNonGoal:
    id: str
    text: str
    rationale: str = ""
    aliases: list[str] = field(default_factory=list)
