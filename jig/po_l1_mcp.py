"""MCP tool handlers for the L1 PO (Track B MVP).

The L1 Discovery PO walks the operator through the 5-phase
journey-walk pattern (Frame / Elicit / Walk / Probe / Playback) per
``docs/multi-level-spec/design.md`` §"L1 PO behavior". Per-journey
playbacks land under ``.jig/spec/discovery/playbacks/`` for audit
trail; the synthesized doc lives at ``.jig/spec/discovery.md`` and
in-flight conversation state at ``.jig/spec/discovery.state.yaml``.

State machinery is split from artifact authoring so the L1 PO can
checkpoint mid-walk without touching the committed doc:

- ``discovery_set_phase`` / ``discovery_set_next_question`` /
  ``discovery_stash_pending_capability`` / ``discovery_clear_pending`` /
  ``discovery_load_state`` — all mutate ``discovery.state.yaml``.
- ``discovery_set_intro`` / ``discovery_add_journey`` /
  ``discovery_add_capability`` / ``discovery_set_playback`` /
  ``discovery_finalize`` — author the markdown + per-journey playbacks.

For tests, the synthetic operator one-shots ``discovery_finalize`` with
a complete ``DiscoveryDoc`` payload (the full multi-turn conversation
only fires in real-mode runs); the per-tool handlers are still tested
directly so the LLM-driven path is covered piece-by-piece.

Distinct from ``po_l0_mcp.py`` (L0 pitch) and ``po_l3_mcp.py`` (L3
suite brief). All three coexist behind allowed_tools gating.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jig.atomic import atomic_write_text
from jig.handoff_resolve import resolve_after_handoff
from jig.schemas.po import (
    CapabilityRosterEntry,
    DiscoveryDoc,
    DiscoveryPhase,
    DiscoveryState,
    DiscoveryStatus,
    Journey,
    PendingCapability,
    Persona,
)
from jig.spec_loader import (
    discovery_path,
    discovery_playback_path,
    load_discovery_state,
    save_discovery_state,
)
from jig.store.bus import Message, MessageBus, MessageType
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff

# The ticket the L1 PO works on. Convention mirrors the L0 PO's
# ``"project"`` ticket — one well-known id per phase so the synthetic
# operator (and the TUI's `/init` flow) can deterministically pre-create
# it before spawning the agent.
L1_TICKET_ID = "discovery"


__all__ = [
    "L1_TICKET_ID",
    "handle_discovery_set_intro",
    "handle_discovery_add_persona",
    "handle_discovery_set_phase",
    "handle_discovery_set_next_question",
    "handle_discovery_add_journey",
    "handle_discovery_add_capability",
    "handle_discovery_stash_pending_capability",
    "handle_discovery_clear_pending",
    "handle_discovery_set_playback",
    "handle_discovery_load_state",
    "handle_discovery_finalize",
    "render_discovery_md",
]


# ---- markdown rendering ---------------------------------------------------


def render_discovery_md(doc: DiscoveryDoc) -> str:
    """Render a ``DiscoveryDoc`` to the markdown form per design.md.

    Format (per design.md §"L1 — Discovery", "Discovery artifact"):

        # <project name> — Discovery

        <intro paragraph if any>

        ## Personas

        - {#<id>} <description>
        ...

        ## Journeys

        ### <title> {#<journey-id>} (persona: <persona-id>)

        <narrative>

        Capabilities implied:
        - {#<cap-id>} <description from roster>
        ...

        ## Capability roster

        - {#<cap-id>} <description> (journeys: <j-id>, ...)
    """
    cap_by_id = {c.id: c for c in doc.capability_roster}
    lines: list[str] = []
    lines.append(f"# {doc.project_name} — Discovery")
    lines.append("")
    if doc.intro.strip():
        lines.append(doc.intro.rstrip())
        lines.append("")

    # Personas
    lines.append("## Personas")
    lines.append("")
    for p in doc.personas:
        lines.append(f"- {{#{p.id}}} {p.description}")
    lines.append("")

    # Journeys
    lines.append("## Journeys")
    lines.append("")
    for j in doc.journeys:
        lines.append(f"### {j.title} {{#{j.id}}} (persona: {j.persona_id})")
        lines.append("")
        lines.append(j.narrative.rstrip())
        lines.append("")
        if j.capability_ids:
            lines.append("Capabilities implied:")
            for cap_id in j.capability_ids:
                # Use the roster's description when present; fall back
                # to the bare id so a malformed doc still renders rather
                # than crashing the writer (validator catches the gap).
                desc = (
                    cap_by_id[cap_id].description
                    if cap_id in cap_by_id
                    else cap_id
                )
                lines.append(f"- {{#{cap_id}}} {desc}")
            lines.append("")

    # Capability roster
    lines.append("## Capability roster")
    lines.append("")
    for c in doc.capability_roster:
        if c.journey_ids:
            journeys = ", ".join(c.journey_ids)
            lines.append(f"- {{#{c.id}}} {c.description} (journeys: {journeys})")
        else:
            lines.append(f"- {{#{c.id}}} {c.description}")

    return "\n".join(lines).rstrip() + "\n"


# ---- state helpers --------------------------------------------------------


def _load_or_init_state(project_path: Path) -> DiscoveryState:
    """Read in-flight state or return a fresh one if absent.

    The L1 PO's state-tracking tools all share this preamble — they
    must work on the first call (no state file yet) and update an
    existing file on subsequent calls. Centralized so the lazy-init
    behavior stays uniform.
    """
    try:
        return load_discovery_state(project_path)
    except FileNotFoundError:
        return DiscoveryState()


# ---- intro handler --------------------------------------------------------


def _intro_cache_path(project_path: Path) -> Path:
    """Sidecar that holds the intro until ``discovery_finalize`` runs.

    The L1 PO captures the intro early in the conversation but the
    rendered ``discovery.md`` only lands at finalize time. We stash
    the intro in a tiny sidecar so the operator can pause + resume
    without losing the captured text. ``discovery_finalize`` reads
    this when no explicit ``intro`` arg is passed.
    """
    return project_path / ".jig" / "spec" / "discovery.intro.txt"


async def handle_discovery_set_intro(
    *,
    project_path: Path,
    intro: str,
) -> None:
    """Stash the intro paragraph for inclusion in the finalized doc.

    Writes a sidecar file under ``.jig/spec/`` so resume can recover
    it. ``discovery_finalize`` consumes (and clears) this when no
    explicit ``intro`` is passed at finalize time. Empty / whitespace
    rejects rather than writing an empty sidecar.
    """
    if not intro.strip():
        raise ValueError("discovery intro must not be empty")
    atomic_write_text(_intro_cache_path(project_path), intro.rstrip() + "\n")


# ---- persona append (state YAML side, staged for finalize) ----------------


async def handle_discovery_add_persona(
    *,
    project_path: Path,
    persona_id: str,
    description: str,
) -> None:
    """Stage a committed persona — appended (or replaced by id) in the sidecar.

    Like ``add_journey``, this doesn't write ``discovery.md`` directly;
    the doc is rendered atomically at finalize. The handler validates
    via the ``Persona`` schema so kebab-case + non-empty-description
    rules fire immediately, not at finalize time.
    """
    persona = Persona(id=persona_id, description=description)
    _append_staged_persona(project_path, persona)


# ---- state-tracking handlers ----------------------------------------------


async def handle_discovery_set_phase(
    *,
    project_path: Path,
    persona_id: str | None,
    journey_id: str | None,
    phase: int,
    step: int = 0,
) -> None:
    """Record the L1 PO's current position in the 5-phase walk."""
    state = _load_or_init_state(project_path)
    state.current = DiscoveryPhase(
        persona_id=persona_id,
        journey_id=journey_id,
        phase=phase,
        step=step,
    )
    save_discovery_state(project_path, state)


async def handle_discovery_set_next_question(
    *,
    project_path: Path,
    question: str,
) -> None:
    """Capture the question the L1 PO is about to ask.

    Per design.md §"L1 conversation state and resume": resume must
    replay the exact thread, not re-derive (and possibly drift). Empty
    questions are rejected so a typo can't blank the state.
    """
    if not question.strip():
        raise ValueError("discovery next_question must not be empty")
    state = _load_or_init_state(project_path)
    state.next_question = question.rstrip()
    save_discovery_state(project_path, state)


async def handle_discovery_stash_pending_capability(
    *,
    project_path: Path,
    capability_id: str,
    description: str,
    journey_id: str,
) -> None:
    """Bookmark a capability extracted mid-walk before Phase-5 commit.

    Dedupes by (capability_id, journey_id) so re-stashing the same
    extraction is idempotent — the L1 PO may re-mention a capability
    across a Phase-3/Phase-4 exchange and we don't want phantom dupes.
    """
    pending = PendingCapability(
        id=capability_id,
        description=description,
        journey_id=journey_id,
    )
    state = _load_or_init_state(project_path)
    if any(
        p.id == pending.id and p.journey_id == pending.journey_id
        for p in state.pending_capabilities
    ):
        return  # already stashed; idempotent.
    state.pending_capabilities.append(pending)
    save_discovery_state(project_path, state)


async def handle_discovery_clear_pending(
    *,
    project_path: Path,
    journey_id: str,
) -> None:
    """Drop pending capabilities for a journey — call after it commits."""
    state = _load_or_init_state(project_path)
    state.pending_capabilities = [
        p for p in state.pending_capabilities if p.journey_id != journey_id
    ]
    save_discovery_state(project_path, state)


async def handle_discovery_load_state(*, project_path: Path) -> dict[str, Any]:
    """Read ``discovery.state.yaml`` for the L1 PO's resume greeting.

    Returns a JSON-friendly dict (the MCP tool wraps it in a text
    payload). Absent state surfaces as ``None`` rather than raising —
    a fresh project must not break the resume entrypoint.
    """
    try:
        state = load_discovery_state(project_path)
    except FileNotFoundError:
        return {"state": None}
    return {"state": state.model_dump(mode="json")}


# ---- per-journey playback (also used inline by add_journey) ---------------


async def handle_discovery_set_playback(
    *,
    project_path: Path,
    journey_id: str,
    playback_text: str,
) -> None:
    """Write the per-journey playback markdown.

    The audit trail of what the L1 PO read back to the operator at
    Phase 5. ``discovery_add_journey`` takes an optional
    ``playback_text`` so the L1 PO can commit the playback alongside
    the journey atomically; this entrypoint exists for the case where
    the playback is captured separately or revised post-hoc.
    """
    if not playback_text.strip():
        raise ValueError("discovery playback_text must not be empty")
    if not journey_id.strip():
        raise ValueError("discovery playback journey_id must not be empty")
    atomic_write_text(
        discovery_playback_path(project_path, journey_id),
        playback_text.rstrip() + "\n",
    )


# ---- in-flight journey + capability append (sidecar side) -----------------


async def handle_discovery_add_journey(
    *,
    project_path: Path,
    persona_id: str,
    journey_id: str,
    title: str,
    narrative: str,
    capability_ids: list[str] | None = None,
    playback_text: str | None = None,
) -> None:
    """Stage a committed journey + (optionally) write its playback.

    Bones-MVP scope: this handler doesn't append to ``discovery.md``
    immediately — the doc is rendered atomically by ``discovery_finalize``
    from the full structured payload. This keeps the markdown writer in
    one place (preventing partial-write artifacts that don't validate
    as a whole). The handler validates the journey shape here so the
    PO gets immediate feedback instead of waiting for finalize, and
    persists the staged entry to a sidecar YAML so resume preserves it.

    ``playback_text`` is optional — when present, writes to
    ``discovery/playbacks/<journey_id>.md`` in the same call so an
    LLM-driven Phase-5 commit is one tool invocation.
    """
    journey = Journey(
        id=journey_id,
        persona_id=persona_id,
        title=title,
        narrative=narrative,
        capability_ids=list(capability_ids or []),
    )
    _append_staged_journey(project_path, journey)
    if playback_text is not None:
        await handle_discovery_set_playback(
            project_path=project_path,
            journey_id=journey_id,
            playback_text=playback_text,
        )


async def handle_discovery_add_capability(
    *,
    project_path: Path,
    capability_id: str,
    description: str,
    journey_ids: list[str] | None = None,
) -> None:
    """Stage (or merge) a capability roster entry.

    Merge-on-id behavior per design.md: if the capability already
    exists, the new ``journey_ids`` are union-merged into the existing
    entry's list (deduped, order-preserving). The description is
    preserved from the first-mention call — operators reword the same
    capability across journeys; the first version is what makes it into
    the L2 conversation.
    """
    entry = CapabilityRosterEntry(
        id=capability_id,
        description=description,
        journey_ids=list(journey_ids or []),
    )
    _merge_staged_capability(project_path, entry)


# ---- sidecar persistence for staged journeys + roster ---------------------


def _staged_journeys_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "discovery.journeys.yaml"


def _staged_roster_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "discovery.roster.yaml"


def _staged_personas_path(project_path: Path) -> Path:
    return project_path / ".jig" / "spec" / "discovery.personas.yaml"


def _read_staged(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    data = yaml.safe_load(path.read_text()) or []
    if not isinstance(data, list):
        raise ValueError(
            f"staged discovery file {path} corrupt: expected a list"
        )
    return data


def _write_staged(path: Path, items: list[dict[str, Any]]) -> None:
    atomic_write_text(path, yaml.safe_dump(items, sort_keys=False))


def _append_staged_journey(project_path: Path, journey: Journey) -> None:
    """Append (or replace by id) a journey in the staged sidecar."""
    path = _staged_journeys_path(project_path)
    items = _read_staged(path)
    items = [j for j in items if j.get("id") != journey.id]
    items.append(journey.model_dump(mode="json"))
    _write_staged(path, items)


def _merge_staged_capability(
    project_path: Path, entry: CapabilityRosterEntry
) -> None:
    """Merge by id into the staged roster sidecar."""
    path = _staged_roster_path(project_path)
    items = _read_staged(path)
    for existing in items:
        if existing.get("id") == entry.id:
            # Union-merge journey_ids while preserving first-mention order.
            existing_journeys = list(existing.get("journey_ids", []))
            for j in entry.journey_ids:
                if j not in existing_journeys:
                    existing_journeys.append(j)
            existing["journey_ids"] = existing_journeys
            _write_staged(path, items)
            return
    items.append(entry.model_dump(mode="json"))
    _write_staged(path, items)


def _append_staged_persona(project_path: Path, persona: Persona) -> None:
    """Append (or replace by id) a persona in the staged sidecar."""
    path = _staged_personas_path(project_path)
    items = _read_staged(path)
    items = [p for p in items if p.get("id") != persona.id]
    items.append(persona.model_dump(mode="json"))
    _write_staged(path, items)


def _drain_sidecars(project_path: Path) -> None:
    """Remove all staged sidecars after a successful finalize.

    Keeping the sidecars around after ``discovery.md`` is committed
    would let a subsequent re-finalize accidentally pick up stale
    staged entries. Best-effort unlink — missing files are fine.
    """
    for path in (
        _staged_journeys_path(project_path),
        _staged_roster_path(project_path),
        _staged_personas_path(project_path),
        _intro_cache_path(project_path),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


# ---- input coercion -------------------------------------------------------


def _coerce_personas(raw: list[Any]) -> list[Persona]:
    out: list[Persona] = []
    for entry in raw:
        if isinstance(entry, Persona):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"persona entry must be a dict, got {type(entry).__name__}"
            )
        try:
            out.append(Persona.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid persona entry: {e}") from e
    return out


def _coerce_journeys(raw: list[Any]) -> list[Journey]:
    out: list[Journey] = []
    for entry in raw:
        if isinstance(entry, Journey):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"journey entry must be a dict, got {type(entry).__name__}"
            )
        try:
            out.append(Journey.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid journey entry: {e}") from e
    return out


def _coerce_roster(raw: list[Any]) -> list[CapabilityRosterEntry]:
    out: list[CapabilityRosterEntry] = []
    for entry in raw:
        if isinstance(entry, CapabilityRosterEntry):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            raise ValueError(
                f"roster entry must be a dict, got {type(entry).__name__}"
            )
        try:
            out.append(CapabilityRosterEntry.model_validate(entry))
        except ValidationError as e:
            raise ValueError(f"invalid roster entry: {e}") from e
    return out


# ---- the finalize handler -------------------------------------------------


def _validate_doc_invariants(doc: DiscoveryDoc) -> None:
    """Enforce design.md §"L1 PO behavior" finalize rules.

    - Every persona has at least one journey.
    - Every journey extracts at least one capability.
    - All capability ids are kebab-case and unique.
    - No two journeys have the same id.
    - Every journey's persona_id is a known persona.
    - Every journey's capability_ids appear in the roster.
    """
    persona_ids = [p.id for p in doc.personas]
    if len(persona_ids) != len(set(persona_ids)):
        raise ValueError("discovery doc has duplicate persona ids")
    journey_ids = [j.id for j in doc.journeys]
    if len(journey_ids) != len(set(journey_ids)):
        raise ValueError("discovery doc has duplicate journey ids")
    roster_ids = [c.id for c in doc.capability_roster]
    if len(roster_ids) != len(set(roster_ids)):
        raise ValueError("discovery doc has duplicate capability ids")

    # Per-journey invariants first — surfacing them before the
    # persona-coverage check makes diagnostic messages point at the
    # actual broken record (an unknown-persona journey shouldn't read
    # as "the persona has no journey").
    persona_id_set = set(persona_ids)
    roster_id_set = set(roster_ids)
    for j in doc.journeys:
        if not j.capability_ids:
            raise ValueError(
                f"journey {j.id!r} has no capabilities — every journey "
                "must extract at least one"
            )
        if j.persona_id not in persona_id_set:
            raise ValueError(
                f"journey {j.id!r} references unknown persona "
                f"{j.persona_id!r}"
            )
        for cap_id in j.capability_ids:
            if cap_id not in roster_id_set:
                raise ValueError(
                    f"journey {j.id!r} cites capability {cap_id!r} "
                    "which is not in the capability roster"
                )

    # Personas must each have a journey — finalize gate per design.
    journeys_by_persona: dict[str, list[Journey]] = {}
    for j in doc.journeys:
        journeys_by_persona.setdefault(j.persona_id, []).append(j)
    for p in doc.personas:
        if not journeys_by_persona.get(p.id):
            raise ValueError(
                f"persona {p.id!r} has no journeys — every persona needs at "
                "least one walked-and-committed journey before finalize"
            )


def _read_intro_cache(project_path: Path) -> str:
    """Read the sidecar intro if present, else empty string."""
    p = _intro_cache_path(project_path)
    if not p.is_file():
        return ""
    return p.read_text().rstrip()


def _read_staged_doc(
    project_path: Path,
    *,
    project_name: str,
    intro: str,
    personas_override: list[Persona] | None,
    journeys_override: list[Journey] | None,
    roster_override: list[CapabilityRosterEntry] | None,
) -> DiscoveryDoc:
    """Assemble a ``DiscoveryDoc`` from explicit args + staged sidecars.

    The agent-driven path stages personas/journeys/capabilities turn by
    turn via the per-tool handlers; the synthetic-operator path
    one-shots everything via ``discovery_finalize`` kwargs. This helper
    favors explicit args when provided, falling back to staged sidecars
    otherwise.
    """
    personas = (
        personas_override
        if personas_override is not None
        else _coerce_personas(_read_staged(_staged_personas_path(project_path)))
    )
    journeys = (
        journeys_override
        if journeys_override is not None
        else _coerce_journeys(_read_staged(_staged_journeys_path(project_path)))
    )
    roster = (
        roster_override
        if roster_override is not None
        else _coerce_roster(_read_staged(_staged_roster_path(project_path)))
    )
    return DiscoveryDoc(
        project_name=project_name,
        intro=intro,
        personas=personas,
        journeys=journeys,
        capability_roster=roster,
        generated_at=datetime.now(timezone.utc),
    )


async def handle_discovery_finalize(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    bus: MessageBus,
    project_path: Path,
    project_name: str,
    intro: str | None = None,
    personas: list[Any] | None = None,
    journeys: list[Any] | None = None,
    capability_roster: list[Any] | None = None,
    author: str,
) -> str:
    """Synthesize the discovery doc, clear state, hand off to L2 PO.

    Returns the Handoff entry id. Validation rules enforce the design's
    finalize gates (every persona has a journey; every journey extracts
    a capability; ids unique + kebab-case; cross-references resolve).

    Atomic on the artifact side — discovery.md is rewritten only when
    validation passes; staged sidecars only drain after a successful
    write.
    """
    if not project_name.strip():
        raise ValueError("discovery finalize requires a non-empty project_name")

    resolved_intro = (
        intro.rstrip()
        if (intro is not None and intro.strip())
        else _read_intro_cache(project_path)
    )

    doc = _read_staged_doc(
        project_path,
        project_name=project_name,
        intro=resolved_intro,
        personas_override=(
            _coerce_personas(personas) if personas is not None else None
        ),
        journeys_override=(
            _coerce_journeys(journeys) if journeys is not None else None
        ),
        roster_override=(
            _coerce_roster(capability_roster)
            if capability_roster is not None
            else None
        ),
    )
    _validate_doc_invariants(doc)

    md = render_discovery_md(doc)
    atomic_write_text(discovery_path(project_path), md)

    # Cache the structured projection so downstream loaders + tests can
    # read the doc back without a markdown parser. Bones doesn't ship
    # the parser yet — see jig.spec_loader.load_discovery.
    cache_yaml = yaml.safe_dump(doc.model_dump(mode="json"), sort_keys=False)
    atomic_write_text(
        project_path / ".jig" / "spec" / "discovery.structured.yaml",
        cache_yaml,
    )

    # Clear in-flight state — finalize is the terminal transition.
    final_state = DiscoveryState(status=DiscoveryStatus.FINALIZED)
    save_discovery_state(project_path, final_state)
    _drain_sidecars(project_path)

    handoff = Handoff(
        ticket_id=L1_TICKET_ID,
        author=author,
        phase="po-l2",
        outputs=[
            ".jig/spec/discovery.md",
            ".jig/spec/discovery.structured.yaml",
        ],
        summary=(
            f"L1 discovery committed: {len(doc.personas)} personas, "
            f"{len(doc.journeys)} journeys, "
            f"{len(doc.capability_roster)} capabilities"
        ),
    )
    entry_id = await threads.post(handoff)
    await bus.publish(
        Message(
            sender=author,
            to="orchestrator",
            type=MessageType.CONTEXT_UPDATE,
            payload={
                "kind": "handoff_posted",
                "ticket_id": L1_TICKET_ID,
                "phase": "po-l2",
            },
            topic="orchestrator",
        )
    )
    await resolve_after_handoff(
        tickets=tickets,
        threads=threads,
        bus=bus,
        ticket_id=L1_TICKET_ID,
        author=author,
    )
    return entry_id
