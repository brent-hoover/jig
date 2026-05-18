"""MCP tool handlers for the L3 PO (Track B5, bones).

The L3 PO writes ONE suite's brief and structured spec, scoped to the
capabilities listed for that suite in ``.jig/spec/suites.yaml``.
Per ``docs/v2.0/multi-level-spec/design.md`` §"L3 — Suite brief", the L3
PO reuses the existing simple-brief format but at suite scope:

- ``.jig/spec/suites/<suite_id>/brief.md`` — markdown brief
- ``.jig/spec/suites/<suite_id>/spec.structured.yaml`` — structured
  projection (same shape as v1 ``StructuredSpec``)

For bones, the synthetic operator hand-writes ``suites.yaml`` (L2
authoring is a later track). The L3 PO reads it to scope its work.
Adding a capability id not listed in the suite is rejected — for bones
this is the simplest gap-handling that keeps L3 honest. The full L1
gap-loop is out of bones scope.

Distinct from ``init_mcp.py`` (v1 monolithic-brief path) and
``po_l0_mcp.py`` (v2 L0 pitch). All three coexist until the rest of
Track B/C land and v1 is removed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff
from jig.schemas.po import ProductNonGoal, Suite, SuitesIndex
from jig.spec_loader import (
    load_suites_index,
    suite_brief_path,
    suite_structured_path,
)
from jig.spec_schema import (
    Behavior,
    Capability,
    NonGoal,
    StructuredSpec,
    UserStory,
)
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff


def _l3_ticket_id(suite_id: str) -> str:
    """The ticket the L3 PO works on for ``suite_id``.

    Convention: ``suite-<id>`` so the synthetic operator (and later the
    TUI's ``/suite init <id>``) can deterministically pre-create one.
    Distinct from the L0 ``"project"`` ticket so simultaneous L0/L3
    flows don't collide.
    """
    return f"suite-{suite_id}"


# ---- markdown rendering ---------------------------------------------------


def render_suite_brief_md(
    *,
    suite: Suite,
    intro: str,
    capabilities: list[Capability],
    non_goals: list[NonGoal],
) -> str:
    """Render an L3 suite brief to markdown.

    Mirrors the v1 brief format (per ``jig/defaults/roles/po.yaml``
    §"Brief format") but scoped to one suite. Sections:

        # <suite title>

        <intro paragraph>

        ## Built              (bones: empty)
        ## Planned (committed)  — capabilities with state PLANNED / IN_PROGRESS / BUILT
        ## Planned (not yet committed)  — bullets for state == BACKLOG
        ## Backlog           (bones: empty separate from "not yet committed")
        ## Archived          — capabilities with state == ARCHIVED
        ## Non-goals (suite-level)

    For bones we route every elaborated capability into "Planned
    (committed)" regardless of state — the v1 format separates them but
    the L3 PO only needs one bucket to hand off a workable brief.
    """
    lines: list[str] = []
    lines.append(f"# {suite.title}")
    lines.append("")
    lines.append(intro.rstrip())
    lines.append("")

    # Built (always emitted for shape continuity with v1 readers).
    lines.append("## Built")
    lines.append("")

    # Planned (committed) — every elaborated capability lands here for
    # bones; state stays accurate in spec.structured.yaml.
    lines.append("## Planned (committed)")
    lines.append("")
    for cap in capabilities:
        lines.extend(_render_capability_block(cap))
        lines.append("")

    lines.append("## Planned (not yet committed)")
    lines.append("")

    lines.append("## Backlog")
    lines.append("")

    lines.append("## Archived")
    lines.append("")

    lines.append("## Non-goals (suite-level)")
    lines.append("")
    for ng in non_goals:
        if ng.rationale:
            lines.append(f"- {{#{ng.id}}} {ng.text} — {ng.rationale}")
        else:
            lines.append(f"- {{#{ng.id}}} {ng.text}")

    return "\n".join(lines).rstrip() + "\n"


def _render_capability_block(cap: Capability) -> list[str]:
    """Render one capability per the v1 elaboration grammar."""
    out: list[str] = []
    alias_attr = f" aliases:{','.join(cap.aliases)}" if cap.aliases else ""
    out.append(f"### {cap.title} {{#{cap.id}{alias_attr}}}")
    out.append("")
    if cap.summary:
        out.append(cap.summary.rstrip())
        out.append("")
    if cap.user_story is not None:
        out.append("**User story:**")
        out.append("")
        out.append(
            f"As {cap.user_story.as_}, I want {cap.user_story.want} so "
            f"{cap.user_story.benefit}"
        )
        out.append("")
    if cap.behaviors:
        out.append("**Behaviors:**")
        out.append("")
        for b in cap.behaviors:
            out.append(f"- {{#{b.id}}} {b.description}")
        out.append("")
        # AC bullets: one per behavior AC, prefixed with [behavior-id].
        ac_lines: list[str] = []
        for b in cap.behaviors:
            for ac in b.acceptance_criteria:
                ac_lines.append(f"- [{b.id}] {ac}")
        for ac in cap.acceptance_criteria:
            ac_lines.append(f"- {ac}")
        if ac_lines:
            out.append("**Acceptance criteria:**")
            out.append("")
            out.extend(ac_lines)
            out.append("")
    elif cap.acceptance_criteria:
        out.append("**Acceptance criteria:**")
        out.append("")
        for ac in cap.acceptance_criteria:
            out.append(f"- {ac}")
        out.append("")
    if cap.excluded:
        out.append("**Excluded:**")
        out.append("")
        for ex in cap.excluded:
            out.append(f"- {ex}")
        out.append("")
    if cap.open_questions:
        out.append("**Open questions:**")
        out.append("")
        for q in cap.open_questions:
            out.append(f"- {q}")
        out.append("")
    # Strip the trailing blank we always leave on each block — the
    # outer renderer adds its own separator.
    while out and out[-1] == "":
        out.pop()
    return out


# ---- input coercion -------------------------------------------------------


def _coerce_capabilities(
    raw: list[Any],
    *,
    now: datetime,
) -> list[Capability]:
    """Validate and construct ``Capability`` objects from MCP input.

    Backfills timestamps the agent shouldn't bother with — the v2 brief
    flow doesn't carry per-capability state-changed-at semantics yet,
    so all three timestamps default to ``now``. The structured spec
    schema enforces kebab-case ids and AC-required-for-elaborated-states
    on its own.
    """
    out: list[Capability] = []
    for entry in raw:
        if isinstance(entry, Capability):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"capability entry must be a dict, got {type(entry).__name__}"
            )
        # Backfill timestamps if absent — the L3 PO doesn't track these.
        for key in ("created_at", "last_updated", "state_changed_at"):
            entry.setdefault(key, now.isoformat())
        # Coerce nested user_story / behaviors so partial dicts work.
        if "user_story" in entry and isinstance(entry["user_story"], dict):
            entry["user_story"] = UserStory.model_validate(entry["user_story"])
        if "behaviors" in entry and isinstance(entry["behaviors"], list):
            entry["behaviors"] = [
                b if isinstance(b, Behavior) else Behavior.model_validate(b)
                for b in entry["behaviors"]
            ]
        try:
            out.append(Capability.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid capability entry: {e}") from e
    return out


def _coerce_non_goals(raw: list[Any]) -> list[NonGoal]:
    out: list[NonGoal] = []
    for entry in raw:
        if isinstance(entry, NonGoal):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"non_goal entry must be a dict, got {type(entry).__name__}"
            )
        try:
            out.append(NonGoal.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid non_goal entry: {e}") from e
    return out


# ---- validation -----------------------------------------------------------


def validate_capability_allowlist(
    *,
    suite: Suite,
    capabilities: list[Capability],
) -> None:
    """Reject any capability id not listed in the suite's allowlist.

    Per design.md §"L3 — Suite brief": adding new capabilities at L3
    flags a gap that must go back to L1. For bones, we don't loop —
    we just refuse so the operator notices and fixes ``suites.yaml``
    before retrying. (The ``ProductNonGoal``-style aliases mechanism
    is intentionally not honored here: the allowlist is about scope,
    not naming continuity.)
    """
    allowed = set(suite.capabilities)
    extras = [c.id for c in capabilities if c.id not in allowed]
    if extras:
        raise ValueError(
            f"capability id(s) {extras!r} not listed in suite "
            f"{suite.id!r} (allowed: {sorted(allowed)!r}). "
            "Add them to .jig/spec/suites.yaml first, or drop them "
            "from the brief."
        )


# ---- the finalize handler -------------------------------------------------


async def handle_l3_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    suite_id: str,
    intro: str,
    capabilities: list[Any],
    non_goals: list[Any] | None = None,
    author: str,
) -> str:
    """Construct the L3 brief + structured spec for ``suite_id``.

    Returns the Handoff entry id. Raises ``ValueError`` when:
    - ``suite_id`` is not in ``suites.yaml``
    - any capability id is not in the suite's allowlist
    - capability/non-goal inputs don't validate against the schema
    - ``intro`` is empty (every brief has at least a one-line intro)

    Writes are atomic: brief.md and spec.structured.yaml replace
    on success, untouched on failure.
    """
    if not intro.strip():
        raise ValueError("L3 brief intro must not be empty")

    index = load_suites_index(project_path)
    suite = index.suite_by_id(suite_id)
    if suite is None:
        known = [s.id for s in index.suites]
        raise ValueError(
            f"suite {suite_id!r} not found in suites.yaml (known: {known!r})"
        )

    now = datetime.now(timezone.utc)
    cap_objs = _coerce_capabilities(capabilities, now=now)
    ng_objs = _coerce_non_goals(non_goals or [])

    validate_capability_allowlist(suite=suite, capabilities=cap_objs)

    try:
        spec = StructuredSpec(
            name=suite.title,
            summary=suite.summary,
            capabilities=cap_objs,
            non_goals=ng_objs,
            generated_at=now,
        )
    except ValidationError as e:
        raise ValueError(f"L3 structured spec does not validate: {e}") from e

    md = render_suite_brief_md(
        suite=suite,
        intro=intro,
        capabilities=cap_objs,
        non_goals=ng_objs,
    )
    atomic_write_text(suite_brief_path(project_path, suite_id), md)

    yaml_text = yaml.safe_dump(
        spec.model_dump(mode="json", by_alias=True),
        sort_keys=False,
    )
    atomic_write_text(suite_structured_path(project_path, suite_id), yaml_text)

    ticket_id = _l3_ticket_id(suite_id)
    handoff = Handoff(
        ticket_id=ticket_id,
        author=author,
        phase="sa",
        outputs=[
            f".jig/spec/suites/{suite_id}/brief.md",
            f".jig/spec/suites/{suite_id}/spec.structured.yaml",
        ],
        summary=f"L3 brief committed for suite {suite_id}",
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": ticket_id,
                "phase": "sa",
                "suite_id": suite_id,
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=ticket_id,
        author=author,
    )
    return entry_id


__all__ = [
    "handle_l3_finalize",
    "render_suite_brief_md",
    "validate_capability_allowlist",
    "SuitesIndex",  # re-export for convenience
    "ProductNonGoal",
]
