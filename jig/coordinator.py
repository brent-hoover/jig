"""Coordinator PM — bones dispatch glue (Track F4, bones).

The v2 PM splits in two: a **Planner** (strategic, runs in passes — Track
F2/F3, *not built in bones*) and a **Coordinator** (tactical, continuous —
this module). For bones the synthetic operator hand-writes the build plan
via ``jig.spec_loader.write_build_plan``; the Coordinator's only job is to
materialize the bones-layer tickets into the live ``TicketStore`` so the
existing orchestrator dispatch loop can pick them up.

What this DOESN'T do (intentionally — see ``docs/implementation/v2-plan.md``
"Bones scope" Track F):

- Run as a continuous background loop. Bones invokes
  ``materialize_ready_tickets`` explicitly from the synthetic operator.
- Materialize MVP / Final layers. Bones is bones-layer only; the layer
  promotion logic that gates "all-bones-done before any-MVP" lands in
  F5/F10.
- Spawn agents directly. The Coordinator only seeds the store; the
  orchestrator's existing ``find_ready`` + ``_handle_schedule`` path
  handles dispatch.
- Auto-escalate, classify unstructured escalations, or detect
  cross-ticket patterns. Those are the LLM-thin Coordinator behaviors
  that land in F6/F7 (MVP).

See ``docs/pm-workflow/design.md`` §"Roles" for the Planner/Coordinator
distinction and §"Ticket structure (extensions)" for the v2 fields this
module populates from the build plan.
"""
from __future__ import annotations

from pathlib import Path

from jig.schemas.plan import BuildPlan, Epic, LayerName
from jig.spec_loader import load_build_plan
from jig.store.tickets import TicketStore
from jig.ticket import Size, Ticket, WorkType

__all__ = ["Coordinator", "DEFAULT_AUTHOR"]

# Author tag for tickets the Coordinator materializes. Distinct from
# ``"sa-v2"`` / ``"po-v2"`` so analytics + thread queries can attribute
# coordinator-created tickets to the PM lane.
DEFAULT_AUTHOR = "coordinator-v2"

# Bones default per design §"Bones tier defaults": SA-tier when shared
# shapes need authoring, senior when they exist, standard for genuine
# CRUD bones. The build plan doesn't yet carry per-ticket tier metadata
# (Planner emits it in MVP), so bones defaults to ``"standard"`` and the
# operator promotes via plan revision when needed.
BONES_DEFAULT_DEV_TIER = "standard"


class Coordinator:
    """Dispatch glue between the build plan and the ticket store.

    Bones scope: a thin helper, not a long-running service. The
    constructor takes the live ``TicketStore`` + project root; the
    only public method materializes the bones layer of every epic in
    the build plan as ``Ticket`` rows so the orchestrator's
    ``find_ready`` loop picks them up.
    """

    def __init__(self, *, tickets: TicketStore, project_root: Path) -> None:
        self._tickets = tickets
        self._project_root = project_root

    async def materialize_ready_tickets(self) -> list[str]:
        """Create ticket-store rows for every bones-layer ticket id.

        Idempotent: tickets that already exist in the store are left
        untouched. Returns the ids that were *newly created* in this
        call so callers (synthetic operator, future startup hook) can
        log + verify.

        Missing build plan returns an empty list rather than raising —
        a bones project may legitimately not have a plan yet (operator
        is mid-scenario). Coordinator-as-loop in MVP can treat absence
        as "wait for the Planner to land one"; bones treats it as
        "nothing to do yet."
        """
        try:
            plan = load_build_plan(self._project_root)
        except FileNotFoundError:
            return []
        return await self._materialize_bones(plan)

    async def _materialize_bones(self, plan: BuildPlan) -> list[str]:
        """Create one ``Ticket`` per id in each epic's bones layer."""
        created: list[str] = []
        for epic in plan.epics:
            for ticket_id in epic.layers.bones.tickets:
                if await self._tickets.get(ticket_id) is not None:
                    # Idempotent — re-running the Coordinator over an
                    # unchanged plan is a no-op. The plan may also
                    # reference tickets the Planner authored directly
                    # (MVP); leaving those alone is correct.
                    continue
                ticket = self._build_ticket(
                    ticket_id=ticket_id,
                    epic=epic,
                    layer=LayerName.BONES,
                )
                await self._tickets.create(ticket)
                created.append(ticket_id)
        return created

    @staticmethod
    def _build_ticket(
        *, ticket_id: str, epic: Epic, layer: LayerName
    ) -> Ticket:
        """Construct a ``Ticket`` from build-plan epic context.

        Per ``docs/pm-workflow/design.md`` §"Ticket structure (extensions)"
        the v2 ticket carries ``epic_id``, ``suite_id``, ``module_id``,
        ``layer``, ``dev_tier``, ``risks_addressed`` (and more, populated
        by the Planner in MVP). Bones populates only what the build plan
        already encodes; defaults stand in for Planner-authored fields:

        - ``module_id`` = epic.modules[0] when set, else None. Bones
          single-module epics are the common case; multi-module bones
          tickets get their primary module here and the Planner refines
          in MVP.
        - ``dev_tier`` = ``"standard"`` per ``BONES_DEFAULT_DEV_TIER``.
          MVP/Final tier selection happens during planning.
        - ``reviewer_set`` = empty. Track G selects per-ticket reviewers;
          bones gates on contract-compliance via the existing G2 path,
          not via reviewer_set wiring.
        - ``done_when`` = None. The Planner fills this in MVP from the
          integration AC; bones uses the title + description.

        ``work_type`` is ``FEATURE`` because bones tracer-bullets ship
        end-to-end behavior. ``WorkType.SPIKE`` exists for the Planner's
        spike tickets but bones has no spike support yet (Track C5).
        """
        module_id = epic.modules[0] if epic.modules else None
        return Ticket(
            id=ticket_id,
            work_type=WorkType.FEATURE,
            size=Size.M,
            title=f"{epic.title} — bones",
            description=epic.intent.problem,
            created_by=DEFAULT_AUTHOR,
            # v2 extension fields — see Ticket model for the full set.
            epic_id=epic.id,
            suite_id=epic.suite,
            module_id=module_id,
            layer=layer.value,
            dev_tier=BONES_DEFAULT_DEV_TIER,
            risks_addressed=list(epic.risks_addressed),
        )
