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

import re
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


_ANCHOR_RE = re.compile(r"^\{#([a-z0-9]([a-z0-9-]*[a-z0-9])?)(?:\s+(.*?))?\}$")
_KEBAB_PATTERN = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
_REFERENCE_RE = re.compile(r"^\[([a-z0-9]([a-z0-9-]*[a-z0-9])?)\]$")


class AnchorParseError(ValueError):
    """Raised when a {#id ...} anchor is malformed."""


class ReferenceParseError(ValueError):
    """Raised when an [id] reference is malformed."""


@dataclass
class ParsedAnchor:
    id: str
    aliases: list[str]


def parse_anchor(text: str) -> ParsedAnchor:
    """Parse ``{#id}`` or ``{#id aliases:a,b}`` into a ParsedAnchor.

    Raises AnchorParseError on any malformation. Recognized attributes:
    ``aliases:a,b,c``. Unknown attribute keys are rejected.
    """
    text = text.strip()
    m = _ANCHOR_RE.match(text)
    if not m:
        raise AnchorParseError(f"malformed anchor: {text!r}")
    cap_id = m.group(1)
    attrs_raw = (m.group(3) or "").strip()
    aliases: list[str] = []
    if attrs_raw:
        for chunk in attrs_raw.split():
            if ":" not in chunk:
                raise AnchorParseError(
                    f"malformed attribute {chunk!r} in anchor {text!r}"
                )
            key, _, value = chunk.partition(":")
            if key != "aliases":
                raise AnchorParseError(
                    f"unknown anchor attribute {key!r} in {text!r}"
                )
            for alias in value.split(","):
                alias = alias.strip()
                if not re.fullmatch(_KEBAB_PATTERN, alias):
                    raise AnchorParseError(
                        f"alias {alias!r} is not kebab-case in anchor {text!r}"
                    )
                aliases.append(alias)
    return ParsedAnchor(id=cap_id, aliases=aliases)


def parse_reference(text: str) -> str:
    """Parse ``[id]`` into the bare id string."""
    m = _REFERENCE_RE.match(text.strip())
    if not m:
        raise ReferenceParseError(f"malformed reference: {text!r}")
    return m.group(1)


@dataclass
class BriefNonGoal:
    id: str
    text: str
    rationale: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class ParsedBrief:
    name: str
    summary: str
    sections: dict[str, str]  # section heading text → raw body


def split_into_sections(text: str) -> ParsedBrief:
    """Split brief markdown into intro + sections by H2 heading.

    Returns:
      ParsedBrief(name, summary, sections={heading: body, ...})

    Raises ValueError on missing H1 or duplicate H2.
    """
    lines = text.splitlines()
    name: str | None = None
    intro_lines: list[str] = []
    sections: dict[str, str] = {}
    current_section: str | None = None
    current_body: list[str] = []

    def _flush_section() -> None:
        nonlocal current_body
        if current_section is not None:
            if current_section in sections:
                raise ValueError(
                    f"duplicate H2 section heading: {current_section!r}"
                )
            sections[current_section] = "\n".join(current_body).strip("\n")
            current_body = []

    for line in lines:
        if line.startswith("# ") and name is None:
            name = line[2:].strip()
            continue
        if line.startswith("## "):
            _flush_section()
            current_section = line[3:].strip()
            continue
        if current_section is None:
            intro_lines.append(line)
        else:
            current_body.append(line)
    _flush_section()

    if name is None:
        raise ValueError("brief is missing an H1 (project name)")

    summary = "\n".join(intro_lines).strip()
    return ParsedBrief(name=name, summary=summary, sections=sections)
