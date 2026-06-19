"""Cascade-after-confirmed-impossible workflow (Track C MVP follow-on commit 3).

Per ``docs/v2.0/sa-architecture/design.md`` §"Cascade after confirmed-impossible
spike": when ``arch_complete_spike`` lands with ``status='confirmed_impossible'``
the system MUST:

1. Compute the cascade scope by reading the risk's ``dependent_contracts``
   URIs.
2. Write ``.jig/arch/cascades/<risk-id>-<timestamp>.yaml`` enumerating
   each impacted contract + a starting "still_holds" disposition the
   operator edits to ``invalidated`` / ``needs_revision``.
3. Post a Handoff on the architecture ticket targeting phase
   ``operator-cascade-confirm`` so the cascade surfaces for review.
4. Emit a ``RiskStatusChanged`` analytics event with the cascade-proposal
   path in the payload.

For MVP scope per ``docs/v2.0/implementation/v2-plan.md`` Track C row, the
operator-confirm step is NOT built — they hand-edit the cascade YAML
and re-run the SA agent. The full transactional confirmation +
failure-mode mitigations land in Final.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.analytics.emitter import EventEmitter
from jig.analytics.store import AnalyticsStore
from jig.sa_incremental_mcp import (
    handle_arch_complete_spike,
    handle_arch_propose_spike,
    handle_arch_set_risk,
)
from jig.sa_mcp import SA_TICKET_ID
from jig.schemas.arch import CascadeProposal
from jig.spec_loader import (
    cascade_proposal_path,
    cascades_dir,
    load_architecture,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Handoff
from jig.ticket import Ticket, WorkType


# ---- fixtures -------------------------------------------------------------


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    analytics = AnalyticsStore(tmp_path / "analytics.jsonl")
    for s in (tickets, threads, bus, analytics):
        await s.load()
    await tickets.create(
        Ticket(
            id=SA_TICKET_ID,
            work_type=WorkType.BRIEF,
            title="SA — architecture",
            created_by="cli",
        )
    )
    emitter = EventEmitter(analytics)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "analytics": analytics,
        "emitter": emitter,
        "project_path": tmp_path,
    }


def _intent_dict() -> dict:
    return {
        "problem": "Validate Shopify supports clean delta sync.",
        "simplest_solution": "Spike a one-shop call against the API.",
        "complications_considered": {
            "scale": None,
            "concurrency": None,
            "failure_modes": None,
            "cross_cutting": None,
        },
    }


_DEPENDENTS = [
    "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
    "project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect",
    "project://arch/modules/categorization/contracts#integration_ac/dedupe-categories",
]


async def _seed_spike_with_dependents(wired: dict) -> tuple[str, str]:
    """Author a risk + propose a spike with rich dependents; return (risk_id, spike_id)."""
    await handle_arch_set_risk(
        project_path=wired["project_path"],
        risk={
            "id": "r-shopify-delta",
            "text": "Unclear whether Shopify's API supports clean delta sync.",
            "impact": "medium",
            "likelihood": "medium",
            "status": "open",
        },
    )
    spike_id = await handle_arch_propose_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        risk_id="r-shopify-delta",
        summary="Try Shopify's products endpoint with since_id.",
        dependent_contracts=_DEPENDENTS,
        author="sa-mvp",
    )
    return "r-shopify-delta", spike_id


# ---- happy path: cascade proposal lands on disk + handoff posted ----------


@pytest.mark.asyncio
async def test_confirmed_impossible_writes_cascade_proposal(wired):
    risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Shopify API does not expose reliable delta sync.",
        status="confirmed_impossible",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    # The proposal lives under .jig/arch/cascades/.
    target_dir = cascades_dir(wired["project_path"])
    assert target_dir.is_dir()
    proposals = sorted(target_dir.glob(f"{risk_id}-*.yaml"))
    assert len(proposals) == 1


@pytest.mark.asyncio
async def test_cascade_proposal_enumerates_all_dependents(wired):
    risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync impossible.",
        status="confirmed_impossible",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    target_dir = cascades_dir(wired["project_path"])
    proposal_path = next(iter(target_dir.glob(f"{risk_id}-*.yaml")))
    raw = yaml.safe_load(proposal_path.read_text())
    proposal = CascadeProposal.model_validate(raw)
    assert proposal.risk_id == risk_id
    assert proposal.spike_ticket_id == spike_id
    assert proposal.finding.startswith("Delta sync impossible")
    assert len(proposal.contracts) == len(_DEPENDENTS)
    uris = [c.uri for c in proposal.contracts]
    for d in _DEPENDENTS:
        assert d in uris
    # Default disposition is "still_holds" — operator edits to flip.
    for c in proposal.contracts:
        assert c.proposed_disposition == "still_holds"


@pytest.mark.asyncio
async def test_cascade_proposal_posts_handoff_on_architecture(wired):
    _risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync impossible.",
        status="confirmed_impossible",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    entries = await wired["threads"].for_ticket(SA_TICKET_ID)
    handoffs = [e for e in entries if isinstance(e, Handoff)]
    cascade_handoff = next(
        (h for h in handoffs if h.phase == "operator-cascade-confirm"),
        None,
    )
    assert cascade_handoff is not None
    # Outputs cite the cascade-proposal path so the operator finds it.
    assert any(".jig/arch/cascades/" in o for o in cascade_handoff.outputs)


@pytest.mark.asyncio
async def test_cascade_emits_risk_status_changed_with_proposal_path(wired):
    risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync impossible.",
        status="confirmed_impossible",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    await wired["emitter"].drain()
    events = await wired["analytics"].by_kind("risk_status_changed")
    assert any(
        ev.risk_id == risk_id and ev.to_status == "confirmed_impossible"
        for ev in events
    )


@pytest.mark.asyncio
async def test_no_cascade_for_mitigated_outcome(wired):
    """Only ``confirmed_impossible`` triggers the cascade artifact + handoff."""
    _risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync works fine.",
        status="mitigated",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    target_dir = cascades_dir(wired["project_path"])
    if target_dir.exists():
        assert not list(target_dir.glob("*.yaml"))
    entries = await wired["threads"].for_ticket(SA_TICKET_ID)
    handoffs = [e for e in entries if isinstance(e, Handoff)]
    assert not any(h.phase == "operator-cascade-confirm" for h in handoffs)


@pytest.mark.asyncio
async def test_cascade_proposal_path_helper_round_trips(wired, tmp_path):
    """``cascade_proposal_path`` produces the same path the writer uses."""
    risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync impossible.",
        status="confirmed_impossible",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    target_dir = cascades_dir(wired["project_path"])
    actual = next(iter(target_dir.glob(f"{risk_id}-*.yaml")))
    # Reconstructed path must match: extract the timestamp from
    # the filename and round-trip via the helper.
    ts = actual.stem.removeprefix(f"{risk_id}-")
    reconstructed = cascade_proposal_path(wired["project_path"], risk_id, ts)
    assert reconstructed == actual


@pytest.mark.asyncio
async def test_emitter_optional_for_non_cascade_outcomes(wired):
    """``emitter`` is optional; only the cascade branch needs it for the event.

    Other outcomes (mitigated, accepted) skip the analytics emission
    so the handler stays callable without an emitter — back-compat with
    the existing spike-workflow tests that don't wire one.
    """
    _risk_id, spike_id = await _seed_spike_with_dependents(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="ok",
        status="accepted",
        author="sa-mvp",
        # emitter omitted on purpose
    )
    arch = load_architecture(wired["project_path"])
    risk = next(r for r in arch.risks if r.id == "r-shopify-delta")
    assert risk.status.value == "accepted"
