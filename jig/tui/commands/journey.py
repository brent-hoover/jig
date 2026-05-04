"""``/journey`` slash command — append + list captured journeys.

Per ``docs/multi-level-spec/design.md`` §"Workflow integration" Track B
Final scope: the operator can extend the discovery doc post-author by
adding a journey under an existing persona.

Subcommands:

- ``/journey list`` — list every captured journey + its playback path.
- ``/journey add <persona>`` — register a new journey under an existing
  persona. Bones-Final scope: stages a journey scaffold (kebab-case id,
  empty narrative + capability list) so the operator can fill it in via
  follow-up MCP calls or a hand-edit pass; spawning the L1 PO agent
  for the new journey only is wired through the existing agent
  dispatcher and is out of this command's scope.

The ``add`` form returns the staged journey id so the TUI can echo
"journey staged: j-{persona}-{N}" for the operator to follow up on.
"""
from __future__ import annotations

from typing import Any

from jig.tui.commands import register


@register("journey")
async def cmd_journey(
    *,
    args: list[str],
    orch,
    project_path,
    **_kwargs,
) -> dict[str, Any]:
    if not args:
        return {"ok": False, "error": "/journey needs a subcommand (list, add)"}
    sub = args[0]
    rest = args[1:]
    if sub == "list":
        return await _journey_list(project_path)
    if sub == "add":
        if not rest:
            return {
                "ok": False,
                "error": "/journey add requires a persona id (e.g. /journey add merchant)",
            }
        persona_id = rest[0]
        return await _journey_add(project_path, persona_id)
    return {"ok": False, "error": f"unknown /journey subcommand: {sub}"}


async def _journey_list(project_path) -> dict[str, Any]:
    """Read the staged + committed journeys and return a summary list."""
    if project_path is None:
        return {"ok": False, "error": "/journey list requires a project_path"}

    from jig.po_l1_mcp import _read_staged, _staged_journeys_path
    from jig.spec_loader import discovery_playback_path, load_discovery

    journeys: list[dict[str, str]] = []

    # Committed journeys (post-finalize) live in the structured cache.
    try:
        doc = load_discovery(project_path)
    except FileNotFoundError:
        doc = None
    if doc is not None:
        for j in doc.journeys:
            journeys.append(
                {
                    "id": j.id,
                    "persona_id": j.persona_id,
                    "title": j.title,
                    "playback": str(
                        discovery_playback_path(project_path, j.id).relative_to(
                            project_path
                        )
                    ),
                    "status": "committed",
                }
            )

    # Staged journeys (pre-finalize) live in the sidecar.
    staged_raw = _read_staged(_staged_journeys_path(project_path))
    for entry in staged_raw:
        if any(j["id"] == entry.get("id") for j in journeys):
            continue
        journeys.append(
            {
                "id": entry.get("id", ""),
                "persona_id": entry.get("persona_id", ""),
                "title": entry.get("title", ""),
                "playback": str(
                    discovery_playback_path(
                        project_path, entry.get("id", "")
                    ).relative_to(project_path)
                ),
                "status": "staged",
            }
        )

    return {"ok": True, "data": {"journeys": journeys}}


async def _journey_add(project_path, persona_id: str) -> dict[str, Any]:
    """Stage a new journey scaffold under ``persona_id``.

    Validates that the persona exists (in the committed doc or staged
    sidecar), then writes a placeholder journey to the staging sidecar
    so the L1 PO can pick it up on its next dispatch. The staged entry
    has an auto-numbered id ``j-{persona}-{N}`` where N is the smallest
    integer not already in use under that persona.
    """
    if project_path is None:
        return {"ok": False, "error": "/journey add requires a project_path"}

    from jig.po_l1_mcp import (
        _append_staged_journey,
        _read_staged,
        _staged_journeys_path,
        _staged_personas_path,
    )
    from jig.schemas.po import Journey

    # Persona must exist (committed or staged).
    persona_ids: set[str] = set()
    try:
        from jig.spec_loader import load_discovery

        doc = load_discovery(project_path)
        persona_ids.update(p.id for p in doc.personas)
    except FileNotFoundError:
        pass
    persona_ids.update(
        p.get("id", "") for p in _read_staged(_staged_personas_path(project_path))
    )
    if persona_id not in persona_ids:
        return {
            "ok": False,
            "error": (
                f"persona {persona_id!r} not found in discovery doc or "
                f"staged personas (known: {sorted(persona_ids)})"
            ),
        }

    # Pick an auto-numbered id under the persona.
    existing = _read_staged(_staged_journeys_path(project_path))
    existing_ids = {e.get("id", "") for e in existing}
    n = 1
    base = f"j-{persona_id}-"
    while f"{base}{n}" in existing_ids:
        n += 1
    journey_id = f"{base}{n}"

    journey = Journey(
        id=journey_id,
        persona_id=persona_id,
        title=f"Journey {n} for {persona_id}",
        narrative=(
            "Placeholder journey staged via /journey add. Operator (or "
            "the L1 PO on next dispatch) fills in the narrative + "
            "capabilities."
        ),
        capability_ids=[],
    )
    _append_staged_journey(project_path, journey)
    return {
        "ok": True,
        "data": {"journey_id": journey_id, "persona_id": persona_id},
    }
