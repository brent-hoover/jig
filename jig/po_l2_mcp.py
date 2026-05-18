"""MCP tool handlers for the L2 PO (Track B4 MVP).

The L2 Suite Organizer reads ``.jig/spec/discovery.structured.yaml``
(the L1 capability roster) and authors ``.jig/spec/suites.yaml``
grouping the capabilities into 4-6 suites. Per
``docs/v2.0/multi-level-spec/design.md`` §"L2 — Suite organization" + §"L2
PO behavior" + §"L2 PO tools".

For MVP scope this surfaces ONE handler — ``l2_finalize`` — that
accepts a complete ``SuitesIndex`` payload one-shot. The multi-turn
proposing UX (real-mode driven by the LLM) happens via the role prompt;
the synthetic-operator path (mock mode) one-shots the finalize.

Validation rules:
- The union of all suites' ``capabilities`` MUST equal the discovery
  roster — every L1 capability appears in exactly one suite (no
  missing, no duplicated, no extras).
- Soft target of 3-5 capabilities per suite — surfaced as a warning
  in the handoff Note (not a rejection).

Distinct from ``po_l0_mcp.py`` (L0 pitch), ``po_l1_mcp.py`` (L1
discovery), and ``po_l3_mcp.py`` (L3 suite brief) — all four coexist
behind allowed_tools gating.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff
from jig.schemas.po import (
    DiscoveryDoc,
    ProductNonGoal,
    Suite,
    SuitesIndex,
)
from jig.spec_loader import (
    load_discovery,
    suites_index_path,
)
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff

# Convention mirrors L0 (``"project"``) / L1 (``"discovery"``) — one
# well-known id per phase so the synthetic operator (and the TUI's
# `/init` flow) can deterministically pre-create it before spawning
# the agent.
L2_TICKET_ID = "suites"

# Soft target for capabilities-per-suite. Outside this range surfaces a
# Note in the handoff payload but does NOT reject the finalize — per
# design.md §"L2 PO behavior" the operator's product genuinely may have
# fewer/more suites; the heuristic is a warning, not a rule.
SOFT_MIN_CAPS_PER_SUITE = 3
SOFT_MAX_CAPS_PER_SUITE = 5


__all__ = [
    "L2_TICKET_ID",
    "SOFT_MIN_CAPS_PER_SUITE",
    "SOFT_MAX_CAPS_PER_SUITE",
    "handle_l2_finalize",
    "validate_capability_coverage",
    "compute_size_warnings",
]


# ---- input coercion -------------------------------------------------------


def _coerce_suites(raw: list[Any]) -> list[Suite]:
    """Validate and construct ``Suite`` objects from MCP input.

    Mirrors the L3 PO's ``_coerce_capabilities`` shape — accept either
    pre-built ``Suite`` instances (test path) or dicts (MCP path); fail
    loudly on anything else so a typo in the scenario YAML doesn't slip
    through as a silent empty list.
    """
    out: list[Suite] = []
    for entry in raw:
        if isinstance(entry, Suite):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(f"suite entry must be a dict, got {type(entry).__name__}")
        try:
            out.append(Suite.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid suite entry: {e}") from e
    return out


def _coerce_non_goals(raw: list[Any]) -> list[ProductNonGoal]:
    out: list[ProductNonGoal] = []
    for entry in raw:
        if isinstance(entry, ProductNonGoal):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"crosscutting_non_goal entry must be a dict, got "
                f"{type(entry).__name__}"
            )
        try:
            out.append(ProductNonGoal.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid crosscutting_non_goal entry: {e}") from e
    return out


# ---- validation -----------------------------------------------------------


def validate_capability_coverage(
    *,
    suites: list[Suite],
    discovery: DiscoveryDoc,
) -> None:
    """Enforce design.md §"L2 — Suite organization" coverage rule.

    Every L1 capability appears in exactly one suite. We surface three
    diff diagnostics so the LLM can fix the proposal in the next turn
    without guessing what we rejected:

    - ``missing`` — roster ids not in any suite (the operator dropped
      them; usually the bug)
    - ``extras`` — suite ids not in the roster (the operator invented a
      capability; that needs to go back to L1)
    - ``duplicates`` — ids that appear in more than one suite (an L1
      capability can't belong to two suites — design rule)

    Suite ids themselves must also be unique — the L3 PO indexes by id
    so a duplicate would mask one suite from the rest of the pipeline.
    """
    suite_ids = [s.id for s in suites]
    if len(suite_ids) != len(set(suite_ids)):
        dupes = sorted({sid for sid in suite_ids if suite_ids.count(sid) > 1})
        raise ValueError(
            f"suites.yaml has duplicate suite ids {dupes!r} — every "
            "suite must have a unique kebab-case id"
        )

    roster_ids = {c.id for c in discovery.capability_roster}
    seen: dict[str, list[str]] = {}
    for s in suites:
        for cap_id in s.capabilities:
            seen.setdefault(cap_id, []).append(s.id)

    missing = sorted(roster_ids - set(seen))
    extras = sorted(set(seen) - roster_ids)
    duplicates = sorted(
        {cap: suites_for for cap, suites_for in seen.items() if len(suites_for) > 1}
    )

    if missing or extras or duplicates:
        # One ValueError surfacing all three problems — the LLM
        # benefits from seeing the full picture in one rejection
        # rather than having to re-attempt three times.
        parts: list[str] = []
        if missing:
            parts.append(f"capabilities in roster but not in any suite: {missing!r}")
        if extras:
            parts.append(
                f"capabilities in suites but not in L1 roster: {extras!r} "
                "(add them to L1 first via /journey add)"
            )
        if duplicates:
            dup_detail = sorted(f"{cap}→{seen[cap]!r}" for cap in duplicates)
            parts.append(
                f"capabilities assigned to >1 suite: {dup_detail!r} "
                "(L1 capability belongs to exactly one suite)"
            )
        raise ValueError("suites coverage invalid — " + "; ".join(parts))


def compute_size_warnings(
    suites: list[Suite],
    *,
    soft_min: int = SOFT_MIN_CAPS_PER_SUITE,
    soft_max: int = SOFT_MAX_CAPS_PER_SUITE,
) -> list[str]:
    """List one warning per suite whose capability count is out of range.

    Warnings are advisory — design.md says the 3-5 target is a
    heuristic, not a rule. We surface them so the operator sees them
    in the handoff payload (and the L3 PO can read them on resume) but
    we don't block.
    """
    out: list[str] = []
    for s in suites:
        n = len(s.capabilities)
        if n < soft_min:
            out.append(
                f"suite {s.id!r} has {n} capabilities "
                f"(soft target ≥{soft_min}) — consider merging with another suite"
            )
        elif n > soft_max:
            out.append(
                f"suite {s.id!r} has {n} capabilities "
                f"(soft target ≤{soft_max}) — consider splitting"
            )
    return out


# ---- the finalize handler -------------------------------------------------


async def handle_l2_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    suites: list[Any],
    crosscutting_non_goals: list[Any] | None = None,
    author: str,
) -> str:
    """Author ``suites.yaml`` from the L2 PO's grouping + hand off to L3.

    Returns the Handoff entry id. Raises ``ValueError`` when:

    - ``suites`` is empty (L2 with zero suites isn't a valid handoff —
      L3 has nothing to read)
    - any suite entry doesn't validate against the ``Suite`` schema
    - the capability union doesn't equal the L1 roster (see
      ``validate_capability_coverage``)

    Atomic on the artifact side — ``suites.yaml`` is rewritten only
    when validation passes; a partial proposal that fails the coverage
    check leaves the prior file (if any) untouched.

    Soft warnings (suites outside [3, 5] capability target) surface in
    the handoff summary but do NOT reject the call.
    """
    if not suites:
        raise ValueError(
            "L2 finalize requires at least one suite — an empty index "
            "leaves L3 with nothing to read"
        )

    # discovery.structured.yaml is the load-bearing input. If L1 hasn't
    # finalized we can't validate coverage; surface that as
    # FileNotFoundError so the operator knows to run L1 first rather
    # than getting a misleading "missing capabilities" diff.
    discovery = load_discovery(project_path)

    suite_objs = _coerce_suites(suites)
    ng_objs = _coerce_non_goals(crosscutting_non_goals or [])

    validate_capability_coverage(suites=suite_objs, discovery=discovery)

    # Build + write the SuitesIndex. Use ``sort_keys=False`` so the
    # operator-readable on-disk form preserves the proposal's order
    # (the L2 PO orders suites intentionally — alphabetizing would
    # destroy that signal).
    index = SuitesIndex(
        spec_version=2,
        suites=suite_objs,
        crosscutting_non_goals=ng_objs,
    )
    yaml_text = yaml.safe_dump(index.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(suites_index_path(project_path), yaml_text)

    warnings = compute_size_warnings(suite_objs)
    summary_parts = [
        f"L2 suites committed: {len(suite_objs)} suites covering "
        f"{len(discovery.capability_roster)} capabilities"
    ]
    if warnings:
        # One Note line per warning so the L3 PO can see them in
        # ``read_comments`` without re-running the validator.
        summary_parts.append("warnings: " + " | ".join(warnings))
    summary = "; ".join(summary_parts)

    handoff = Handoff(
        ticket_id=L2_TICKET_ID,
        author=author,
        phase="po-l3",
        outputs=[".jig/spec/suites.yaml"],
        summary=summary,
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": L2_TICKET_ID,
                "phase": "po-l3",
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=L2_TICKET_ID,
        author=author,
    )
    return entry_id
