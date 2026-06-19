"""Risk register MCP tool (Track C MVP follow-on commit 1).

``arch_set_risk`` mirrors the existing upsert pattern but adds two
non-trivial validation rules per ``docs/v2.0/sa-architecture/design.md``
§"Risk schema requires `dependent_contracts`":

- ``dependent_contracts`` MUST be non-empty when ``status >=
  spike_proposed``. Without dependents the cascade workflow can't
  enumerate what changes when a spike confirms an assumption is wrong.
- ``intent`` MUST be set when ``status >= spike_proposed`` for the same
  reason every authored v2 artifact carries an intent layer — agents
  default to terse and skip it without an enforced gate.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.sa_incremental_mcp import handle_arch_set_risk
from jig.sa_mcp import SA_TICKET_ID
from jig.spec_loader import load_architecture
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


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


def _full_risk(
    *,
    status: str = "spike_proposed",
    intent: dict | None = None,
    dependent_contracts: list[str] | None = None,
) -> dict:
    """Risk dict with every required field populated for status>=spike_proposed."""
    return {
        "id": "r-shopify-delta",
        "text": "Unclear whether Shopify's API supports clean delta sync.",
        "impact": "medium",
        "likelihood": "medium",
        "status": status,
        "dependent_contracts": (
            dependent_contracts
            if dependent_contracts is not None
            else [
                "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
            ]
        ),
        "intent": intent if intent is not None else _intent_dict(),
    }


# ---- happy path -----------------------------------------------------------


@pytest.mark.asyncio
async def test_arch_set_risk_writes_open_risk_without_intent(wired):
    """``open`` status doesn't trigger the cascade-prep gates.

    Open is the SA's "noted, but not committed to a spike yet" state —
    no dependents required, no intent required. Forcing intent on every
    risk would double-cost early authoring; the gate fires when the SA
    proposes a spike.
    """
    rid = await handle_arch_set_risk(
        project_path=wired["project_path"],
        risk={
            "id": "r-undecided",
            "text": "Catalog cardinality unknown at scale.",
            "impact": "low",
            "likelihood": "low",
            "status": "open",
        },
    )
    assert rid == "r-undecided"
    arch = load_architecture(wired["project_path"])
    assert len(arch.risks) == 1
    assert arch.risks[0].id == "r-undecided"


@pytest.mark.asyncio
async def test_arch_set_risk_writes_spike_proposed_risk(wired):
    rid = await handle_arch_set_risk(
        project_path=wired["project_path"],
        risk=_full_risk(),
    )
    assert rid == "r-shopify-delta"
    arch = load_architecture(wired["project_path"])
    risk = arch.risks[0]
    assert risk.status.value == "spike_proposed"
    assert len(risk.dependent_contracts) == 1
    assert risk.intent is not None


# ---- idempotent + accumulation -------------------------------------------


@pytest.mark.asyncio
async def test_arch_set_risk_idempotent_replace(wired):
    await handle_arch_set_risk(project_path=wired["project_path"], risk=_full_risk())
    revised = _full_risk()
    revised["text"] = "Confirmed: delta sync impossible."
    revised["status"] = "spike_running"
    await handle_arch_set_risk(project_path=wired["project_path"], risk=revised)
    arch = load_architecture(wired["project_path"])
    assert len(arch.risks) == 1
    assert arch.risks[0].text.startswith("Confirmed")
    assert arch.risks[0].status.value == "spike_running"


@pytest.mark.asyncio
async def test_arch_set_risk_multi_id_accumulates(wired):
    await handle_arch_set_risk(project_path=wired["project_path"], risk=_full_risk())
    second = _full_risk()
    second["id"] = "r-second"
    await handle_arch_set_risk(project_path=wired["project_path"], risk=second)
    arch = load_architecture(wired["project_path"])
    ids = [r.id for r in arch.risks]
    assert sorted(ids) == ["r-second", "r-shopify-delta"]


# ---- validation: dependent_contracts gate --------------------------------


@pytest.mark.asyncio
async def test_arch_set_risk_rejects_spike_proposed_without_dependents(wired):
    bad = _full_risk(dependent_contracts=[])
    with pytest.raises(ValueError, match="dependent_contracts"):
        await handle_arch_set_risk(project_path=wired["project_path"], risk=bad)


@pytest.mark.asyncio
async def test_arch_set_risk_rejects_spike_running_without_dependents(wired):
    bad = _full_risk(status="spike_running", dependent_contracts=[])
    with pytest.raises(ValueError, match="dependent_contracts"):
        await handle_arch_set_risk(project_path=wired["project_path"], risk=bad)


@pytest.mark.asyncio
async def test_arch_set_risk_rejects_confirmed_impossible_without_dependents(wired):
    bad = _full_risk(status="confirmed_impossible", dependent_contracts=[])
    with pytest.raises(ValueError, match="dependent_contracts"):
        await handle_arch_set_risk(project_path=wired["project_path"], risk=bad)


# ---- validation: intent gate ---------------------------------------------


@pytest.mark.asyncio
async def test_arch_set_risk_rejects_spike_proposed_without_intent(wired):
    bad = _full_risk(intent=None)
    # Drop intent entirely so Pydantic accepts the partial then our
    # gate fires.
    bad.pop("intent")
    with pytest.raises(ValueError, match="intent"):
        await handle_arch_set_risk(project_path=wired["project_path"], risk=bad)


@pytest.mark.asyncio
async def test_arch_set_risk_rejects_mitigated_without_intent(wired):
    bad = _full_risk()
    bad["status"] = "mitigated"
    bad.pop("intent")
    with pytest.raises(ValueError, match="intent"):
        await handle_arch_set_risk(project_path=wired["project_path"], risk=bad)


# ---- validation: open-status laxness ----------------------------------


@pytest.mark.asyncio
async def test_arch_set_risk_open_status_does_not_require_dependents(wired):
    """``open`` is the noted-but-not-committed state — no gate fires."""
    await handle_arch_set_risk(
        project_path=wired["project_path"],
        risk={
            "id": "r-open-only",
            "text": "Some uncertainty",
            "impact": "low",
            "likelihood": "low",
            "status": "open",
            # No dependent_contracts; no intent.
        },
    )
    # No exception → success.
    arch = load_architecture(wired["project_path"])
    assert arch.risks[0].id == "r-open-only"


# ---- validation: schema mismatch surfaces uniformly --------------------


@pytest.mark.asyncio
async def test_arch_set_risk_rejects_invalid_payload_shape(wired):
    """Missing required field surfaces as ValueError on the boundary."""
    with pytest.raises(ValueError, match="risk does not validate"):
        await handle_arch_set_risk(
            project_path=wired["project_path"],
            risk={
                "id": "r-bad",
                # Missing required text/impact/likelihood/status.
            },
        )
