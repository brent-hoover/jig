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


class BriefParseError(ValueError):
    """Raised when the brief markdown violates a format rule."""


_HEADING_ANCHOR_RE = re.compile(r"^### (.+?)\s+(\{#[^}]+\})\s*$")


def parse_elaborated_section(body: str, *, section: BriefSection) -> list[BriefCapability]:
    """Parse the body of a Built / Planned (committed) / Archived section.

    Each capability is introduced by ``### Title {#id}`` and may contain
    a summary paragraph plus labelled blocks: ``**User story:**``,
    ``**Behaviors:**``, ``**Acceptance criteria:**``, ``**Excluded:**``,
    ``**Open questions:**``.
    """
    blocks = _split_on_h3(body)
    out: list[BriefCapability] = []
    for raw_block in blocks:
        out.append(_parse_capability_block(raw_block, section=section))
    return out


def _split_on_h3(body: str) -> list[str]:
    """Split a section body into sub-blocks at each ``### `` heading.

    Returns each block including its ``### `` line. Empty result if no
    headings present.
    """
    lines = body.splitlines()
    blocks: list[list[str]] = []
    current: list[str] | None = None
    for line in lines:
        if line.startswith("### "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is not None:
            current.append(line)
    if current is not None:
        blocks.append(current)
    return ["\n".join(b) for b in blocks]


def _parse_capability_block(text: str, *, section: BriefSection) -> BriefCapability:
    lines = text.splitlines()
    if not lines or not lines[0].startswith("### "):
        raise BriefParseError(f"capability block missing ### heading: {text[:80]!r}")
    m = _HEADING_ANCHOR_RE.match(lines[0])
    if not m:
        raise BriefParseError(
            f"capability heading missing trailing {{#anchor}}: {lines[0]!r}"
        )
    title = m.group(1).strip()
    try:
        anchor = parse_anchor(m.group(2))
    except AnchorParseError as e:
        raise BriefParseError(str(e)) from e

    body_lines = lines[1:]
    blocks = _split_on_labels(body_lines)

    summary = blocks.pop("__intro__", "").strip()
    user_story = _parse_user_story_block(blocks.pop("User story", None))
    behaviors = _parse_behavior_block(blocks.pop("Behaviors", None))
    raw_ac_lines = _parse_bullet_lines(blocks.pop("Acceptance criteria", None))
    excluded = _parse_bullet_lines(blocks.pop("Excluded", None))
    open_questions = _parse_bullet_lines(blocks.pop("Open questions", None))
    if blocks:
        raise BriefParseError(
            f"unknown labelled block(s) in capability {anchor.id!r}: "
            f"{list(blocks.keys())}"
        )

    behavior_ids = {b.id for b in behaviors}
    capability_ac: list[str] = []
    for ac_line in raw_ac_lines:
        ref, ac_text = _split_ac_reference(ac_line)
        if ref is None:
            capability_ac.append(ac_text)
        else:
            if ref not in behavior_ids:
                raise BriefParseError(
                    f"AC references missing behavior [{ref}] in capability "
                    f"{anchor.id!r}"
                )
            for b in behaviors:
                if b.id == ref:
                    b.acceptance_criteria.append(ac_text)
                    break

    if section in ("planned_committed", "built") and not behaviors:
        if not capability_ac:
            raise BriefParseError(
                f"capability {anchor.id!r} has no behaviors and no "
                "capability-level acceptance criteria"
            )

    return BriefCapability(
        id=anchor.id,
        title=title,
        section=section,
        summary=summary,
        user_story=user_story,
        behaviors=behaviors,
        capability_acceptance_criteria=capability_ac,
        excluded=excluded,
        open_questions=open_questions,
        aliases=anchor.aliases,
    )


_LABEL_RE = re.compile(r"^\*\*(.+?):\*\*\s*$")


def _split_on_labels(lines: list[str]) -> dict[str, str]:
    """Split body lines into labelled blocks. Lines before the first
    labelled block become the ``__intro__`` block (the summary prose).
    """
    blocks: dict[str, list[str]] = {"__intro__": []}
    current = "__intro__"
    for line in lines:
        m = _LABEL_RE.match(line.strip())
        if m:
            current = m.group(1).strip()
            blocks.setdefault(current, [])
            continue
        blocks[current].append(line)
    return {k: "\n".join(v).strip("\n") for k, v in blocks.items()}


def _parse_user_story_block(text: str | None) -> BriefUserStory | None:
    if text is None or not text.strip():
        return None
    body = " ".join(line.strip() for line in text.splitlines() if line.strip())
    pattern = re.compile(
        r"^As an? (.+?), I want (.+?) so(?: that)? (.+?)\.?$",
        re.IGNORECASE,
    )
    m = pattern.match(body)
    if not m:
        raise BriefParseError(
            "user story must read 'As a X, I want Y so [that] Z.': "
            f"got {body!r}"
        )
    return BriefUserStory(as_=m.group(1).strip(), want=m.group(2).strip(),
                          benefit=m.group(3).strip())


def _parse_behavior_block(text: str | None) -> list[BriefBehavior]:
    if text is None:
        return []
    out: list[BriefBehavior] = []
    for line in _bullet_lines(text):
        anchor_text, _, rest = line.partition(" ")
        try:
            anchor = parse_anchor(anchor_text)
        except AnchorParseError as e:
            raise BriefParseError(
                f"behavior bullet missing leading {{#id}}: {line!r}"
            ) from e
        if not rest.strip():
            raise BriefParseError(
                f"behavior bullet has anchor but no description: {line!r}"
            )
        out.append(BriefBehavior(id=anchor.id, description=rest.strip()))
    return out


def _parse_bullet_lines(text: str | None) -> list[str]:
    if text is None:
        return []
    return list(_bullet_lines(text))


def _bullet_lines(text: str):
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("- "):
            raise BriefParseError(f"expected bullet line, got: {line!r}")
        yield stripped[2:].strip()


def _split_ac_reference(line: str) -> tuple[str | None, str]:
    """Split an AC bullet body into (behavior_ref, ac_text).

    AC may start with ``[behavior-id]`` (behavior-level) or have no
    bracketed prefix (capability-level).
    """
    line = line.strip()
    if not line.startswith("["):
        return None, line
    end = line.find("]")
    if end == -1:
        raise BriefParseError(f"unterminated [reference] in AC: {line!r}")
    ref_text = line[: end + 1]
    rest = line[end + 1 :].strip()
    try:
        ref_id = parse_reference(ref_text)
    except ReferenceParseError as e:
        raise BriefParseError(str(e)) from e
    return ref_id, rest


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
