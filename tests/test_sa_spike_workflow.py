"""Spike workflow MCP handlers (Track C MVP follow-on commit 2).

Per ``docs/v2.0/sa-architecture/design.md`` §"Risk identification and spikes":
a spike is a ``work_type: SPIKE`` ticket whose narrow scope is to verify
whether a risky architectural assumption holds. ``arch_propose_spike``
creates the spike ticket, links it to the risk, transitions risk status
to ``spike_proposed``. ``arch_complete_spike`` records the finding,
transitions the risk's status to one of the three outcomes (mitigated /
accepted / confirmed_impossible), resolves the spike ticket.

Cascade-after-impossible behavior is covered by ``test_sa_cascade.py``
(commit 3); the spike-completion handler delegates the cascade
generation when status == confirmed_impossible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.sa_incremental_mcp import (
    handle_arch_complete_spike,
    handle_arch_propose_spike,
    handle_arch_set_risk,
)
from jig.sa_mcp import SA_TICKET_ID
from jig.spec_loader import load_architecture
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.thread import Note
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- fixtures -------------------------------------------------------------


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    await tickets.create(
        Ticket(
            id=SA_TICKET_ID,
            work_type=WorkType.BRIEF,
            title="SA — architecture",
            created_by="cli",
        )
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
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


async def _seed_risk(
    wired: dict,
    *,
    risk_id: str = "r-shopify-delta",
    status: str = "open",
    dependent_contracts: list[str] | None = None,
) -> str:
    payload = {
        "id": risk_id,
        "text": "Unclear whether Shopify's API supports clean delta sync.",
        "impact": "medium",
        "likelihood": "medium",
        "status": status,
    }
    if status != "open":
        payload["intent"] = _intent_dict()
        payload["dependent_contracts"] = dependent_contracts or [
            "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
        ]
    elif dependent_contracts is not None:
        payload["dependent_contracts"] = dependent_contracts
    return await handle_arch_set_risk(project_path=wired["project_path"], risk=payload)


# ---- arch_propose_spike --------------------------------------------------


@pytest.mark.asyncio
async def test_arch_propose_spike_creates_spike_ticket(wired):
    await _seed_risk(wired)
    spike_id = await handle_arch_propose_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        risk_id="r-shopify-delta",
        summary="Try Shopify's products endpoint with since_id and confirm coverage.",
        dependent_contracts=[
            "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
            "project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect",
        ],
        author="sa-mvp",
    )
    spike = await wired["tickets"].get(spike_id)
    assert spike is not None
    assert spike.work_type == WorkType.SPIKE
    assert spike.title.startswith("spike:")
    assert spike.description.startswith("Try Shopify")
    # The spike's derived_from cites the risk URI so reviewers + the
    # cascade-generator can trace lineage back to the originating risk.
    assert "r-shopify-delta" in (spike.derived_from or "")
    # risks_addressed is the operational reverse-link reviewer agents
    # use to gate PRs on still-open risks.
    assert spike.risks_addressed == ["r-shopify-delta"]


@pytest.mark.asyncio
async def test_arch_propose_spike_writes_back_into_risk(wired):
    """Risk gains a spike_ticket pointer + transitions to spike_proposed."""
    await _seed_risk(wired)
    spike_id = await handle_arch_propose_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        risk_id="r-shopify-delta",
        summary="Spike summary",
        dependent_contracts=[
            "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
        ],
        author="sa-mvp",
    )
    arch = load_architecture(wired["project_path"])
    risk = next(r for r in arch.risks if r.id == "r-shopify-delta")
    assert risk.spike_ticket == spike_id
    assert risk.status.value == "spike_proposed"
    # The intent layer was populated automatically from the spike
    # summary so the risk passes the cascade-prep gate going forward.
    assert risk.intent is not None
    assert risk.dependent_contracts == [
        "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
    ]


@pytest.mark.asyncio
async def test_arch_propose_spike_raises_on_unknown_risk(wired):
    """Missing risk is a programmer error — never silent-create."""
    with pytest.raises(KeyError, match="r-missing"):
        await handle_arch_propose_spike(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            risk_id="r-missing",
            summary="...",
            dependent_contracts=["project://arch/x"],
            author="sa-mvp",
        )


# ---- arch_complete_spike --------------------------------------------------


async def _seed_spike(
    wired: dict, *, finding_status_label: str = "neutral"
) -> tuple[str, str]:
    """Seed a risk + propose a spike; return (risk_id, spike_ticket_id)."""
    risk_id = await _seed_risk(wired)
    spike_id = await handle_arch_propose_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        risk_id=risk_id,
        summary=f"Spike for {finding_status_label}",
        dependent_contracts=[
            "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
        ],
        author="sa-mvp",
    )
    return risk_id, spike_id


@pytest.mark.asyncio
async def test_arch_complete_spike_mitigated_updates_risk(wired):
    risk_id, spike_id = await _seed_spike(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Shopify's API does support since_id-based delta sync.",
        status="mitigated",
        author="sa-mvp",
    )
    arch = load_architecture(wired["project_path"])
    risk = next(r for r in arch.risks if r.id == risk_id)
    assert risk.status.value == "mitigated"


@pytest.mark.asyncio
async def test_arch_complete_spike_accepted_updates_risk(wired):
    risk_id, spike_id = await _seed_spike(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync works for shops < 10K SKUs; SLA reflects the cap.",
        status="accepted",
        author="sa-mvp",
    )
    arch = load_architecture(wired["project_path"])
    risk = next(r for r in arch.risks if r.id == risk_id)
    assert risk.status.value == "accepted"


@pytest.mark.asyncio
async def test_arch_complete_spike_confirmed_impossible_updates_risk(wired):
    risk_id, spike_id = await _seed_spike(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Delta sync impossible — full re-fetch every poll.",
        status="confirmed_impossible",
        author="sa-mvp",
    )
    arch = load_architecture(wired["project_path"])
    risk = next(r for r in arch.risks if r.id == risk_id)
    assert risk.status.value == "confirmed_impossible"


@pytest.mark.asyncio
async def test_arch_complete_spike_posts_finding_as_note(wired):
    """The finding is captured as a Note thread entry on the spike ticket."""
    _risk_id, spike_id = await _seed_spike(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Detailed structured finding here.",
        status="mitigated",
        author="sa-mvp",
    )
    entries = await wired["threads"].for_ticket(spike_id)
    notes = [e for e in entries if isinstance(e, Note)]
    assert len(notes) >= 1
    assert any("Detailed structured finding" in n.text for n in notes)


@pytest.mark.asyncio
async def test_arch_complete_spike_resolves_spike_ticket(wired):
    _risk_id, spike_id = await _seed_spike(wired)
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="ok",
        status="accepted",
        author="sa-mvp",
    )
    spike = await wired["tickets"].get(spike_id)
    assert spike.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_arch_complete_spike_rejects_non_spike_ticket(wired):
    """Only spike-typed tickets can be completed via this handler."""
    await wired["tickets"].create(
        Ticket(
            id="not-a-spike",
            work_type=WorkType.FEATURE,
            title="not a spike",
            created_by="test",
            description=TICKET_AC_PLACEHOLDER,
        )
    )
    with pytest.raises(ValueError, match="not a spike"):
        await handle_arch_complete_spike(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            spike_ticket_id="not-a-spike",
            finding="...",
            status="mitigated",
            author="sa-mvp",
        )


@pytest.mark.asyncio
async def test_arch_complete_spike_rejects_invalid_status(wired):
    _risk_id, spike_id = await _seed_spike(wired)
    with pytest.raises(ValueError, match="status"):
        await handle_arch_complete_spike(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            spike_ticket_id=spike_id,
            finding="...",
            status="unknown_outcome",
            author="sa-mvp",
        )


@pytest.mark.asyncio
async def test_arch_complete_spike_raises_when_risk_link_missing(wired):
    """A spike ticket without a backing risk in the register is unrecoverable."""
    # Create a synthetic spike ticket with derived_from pointing at
    # a risk id that doesn't exist in the architecture.
    await wired["tickets"].create(
        Ticket(
            id="orphan-spike",
            work_type=WorkType.SPIKE,
            title="spike: orphan",
            description="orphan" + "\n" + TICKET_AC_PLACEHOLDER,
            derived_from="project://arch/risks/r-not-there",
            risks_addressed=["r-not-there"],
            created_by="test",
        )
    )
    with pytest.raises(KeyError, match="r-not-there"):
        await handle_arch_complete_spike(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            spike_ticket_id="orphan-spike",
            finding="...",
            status="mitigated",
            author="sa-mvp",
        )
