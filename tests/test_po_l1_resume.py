"""L1 discovery resume-from-state edge case handling (Track B Final).

Per ``docs/v2.0/multi-level-spec/design.md`` §"L1 conversation state and
resume": the L1 PO can resume mid-walk. Final hardens the edge cases:

- mid-Phase-3 (Walk) crash recovery — the partial_walk list of
  ``CapabilityCandidate`` records survives the crash;
- multi-persona checkpointing — resume continues at persona N+1 when
  N is fully completed;
- stale-journey detection — operator hand-edited ``discovery.md`` and
  removed the journey the state pointer references;
- concurrent-edit detection — ``discovery.md`` digest doesn't match
  what was stamped at last save;
- partial-walk-orphan — a ``CapabilityCandidate`` references a journey
  that's not in the doc.

Reconcile modes are operator-driven (auto / prompt / prefer-state /
prefer-doc / abandon-state). LLM-driven reconciliation is v2.x.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.po_l1_mcp import (
    ResumeResult,
    compute_discovery_doc_digest,
    handle_discovery_finalize,
    handle_discovery_resume,
    handle_discovery_set_partial_walk,
    validate_state_consistency,
)
from jig.schemas.po import (
    CapabilityCandidate,
    CapabilityRosterEntry,
    DiscoveryDoc,
    DiscoveryPhase,
    DiscoveryState,
    DiscoveryStatus,
    Journey,
    Persona,
    StateDivergence,
    StateDivergenceKind,
)
from jig.spec_loader import (
    discovery_path,
    load_discovery_state,
    save_discovery_state,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType
from jig.po_l1_mcp import L1_TICKET_ID


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
            id=L1_TICKET_ID,
            work_type=WorkType.BRIEF,
            title="L1 discovery",
            created_by="cli",
        )
    )
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


def _doc(*, journeys: list[Journey] | None = None) -> DiscoveryDoc:
    journeys = journeys or [
        Journey(
            id="j-merchant-onboarding",
            persona_id="merchant",
            title="Merchant onboarding",
            narrative="walks the merchant through OAuth.",
            capability_ids=["shopify-connect"],
        )
    ]
    return DiscoveryDoc(
        project_name="resume-test",
        personas=[Persona(id="merchant", description="merchant who integrates jig")],
        journeys=journeys,
        capability_roster=[
            CapabilityRosterEntry(
                id="shopify-connect",
                description="Connect Shopify via OAuth",
                journey_ids=["j-merchant-onboarding"],
            )
        ],
    )


async def _seed_finalized_doc(wired):
    """Use the production handler to commit a doc, then return its digest."""
    doc = _doc()
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name=doc.project_name,
        personas=doc.personas,
        journeys=doc.journeys,
        capability_roster=doc.capability_roster,
        author="po-l1",
    )
    return compute_discovery_doc_digest(wired["project_path"])


# ---- compute_discovery_doc_digest ----------------------------------------


def test_digest_empty_when_no_doc(tmp_path: Path):
    assert compute_discovery_doc_digest(tmp_path) == ""


@pytest.mark.asyncio
async def test_digest_changes_when_doc_edited(wired):
    digest = await _seed_finalized_doc(wired)
    assert digest != ""
    # Operator hand-edits the file.
    p = discovery_path(wired["project_path"])
    p.write_text(p.read_text() + "\n<!-- operator hand-edit -->\n")
    new_digest = compute_discovery_doc_digest(wired["project_path"])
    assert new_digest != digest


# ---- validate_state_consistency: each divergence kind --------------------


def test_validate_clean_state_returns_no_divergence():
    """In-flight state with no doc + no digest: no divergence."""
    state = DiscoveryState(
        current=DiscoveryPhase(persona_id="merchant", journey_id=None, phase=1),
    )
    out = validate_state_consistency(state, None, on_disk_digest="")
    assert out == []


def test_validate_detects_stale_journey():
    """state.current.journey_id refers to a journey absent from the doc."""
    state = DiscoveryState(
        current=DiscoveryPhase(
            persona_id="merchant",
            journey_id="j-vanished",
            phase=3,
            step=1,
        ),
    )
    doc = _doc()
    out = validate_state_consistency(state, doc, on_disk_digest="abc")
    kinds = [d.kind for d in out]
    assert StateDivergenceKind.STALE_JOURNEY in kinds
    stale = next(d for d in out if d.kind == StateDivergenceKind.STALE_JOURNEY)
    assert "j-vanished" in stale.detail


def test_validate_detects_concurrent_edit():
    """saved digest != on-disk digest → concurrent-edit."""
    state = DiscoveryState(discovery_doc_digest="DEADBEEF" * 8)
    doc = _doc()
    out = validate_state_consistency(state, doc, on_disk_digest="CAFEBABE" * 8)
    kinds = [d.kind for d in out]
    assert StateDivergenceKind.CONCURRENT_EDIT in kinds


def test_validate_detects_partial_walk_orphan():
    """A CapabilityCandidate whose journey_id is not in the doc."""
    state = DiscoveryState(
        partial_walk=[
            CapabilityCandidate(
                id="fast-pay",
                description="One-click fast pay",
                journey_id="j-vanished",
                confirmed=False,
            )
        ],
    )
    doc = _doc()
    out = validate_state_consistency(state, doc, on_disk_digest="")
    kinds = [d.kind for d in out]
    assert StateDivergenceKind.PARTIAL_WALK_ORPHAN in kinds


def test_validate_consistent_state_no_divergences():
    """Aligned digest + valid journey_id + valid candidate journey_ids."""
    doc = _doc()
    state = DiscoveryState(
        current=DiscoveryPhase(
            persona_id="merchant",
            journey_id="j-merchant-onboarding",
            phase=3,
            step=1,
        ),
        partial_walk=[
            CapabilityCandidate(
                id="fast-pay",
                description="One-click fast pay",
                journey_id="j-merchant-onboarding",
                confirmed=True,
            )
        ],
        discovery_doc_digest="abc",
    )
    out = validate_state_consistency(state, doc, on_disk_digest="abc")
    assert out == []


# ---- handle_discovery_resume: mode behaviors -----------------------------


@pytest.mark.asyncio
async def test_resume_returns_empty_when_no_state_file(tmp_path: Path):
    out = await handle_discovery_resume(project_path=tmp_path)
    assert isinstance(out, ResumeResult)
    assert out.divergences == []
    assert out.state is None


@pytest.mark.asyncio
async def test_resume_clean_state_no_divergences(wired):
    digest = await _seed_finalized_doc(wired)
    # The finalize handler clears in-flight state and stamps the digest;
    # a follow-up resume call sees no divergences.
    out = await handle_discovery_resume(project_path=wired["project_path"])
    assert out.divergences == []
    assert out.state is not None
    assert out.state.discovery_doc_digest == digest


@pytest.mark.asyncio
async def test_resume_auto_reanchors_stale_journey(wired):
    await _seed_finalized_doc(wired)
    state = load_discovery_state(wired["project_path"])
    state.status = DiscoveryStatus.IN_PROGRESS
    state.current = DiscoveryPhase(
        persona_id="merchant", journey_id="j-vanished", phase=3, step=1
    )
    save_discovery_state(wired["project_path"], state)

    out = await handle_discovery_resume(
        project_path=wired["project_path"],
        reconcile_mode="auto",
    )
    kinds = [d.kind for d in out.divergences]
    assert StateDivergenceKind.STALE_JOURNEY in kinds
    # Auto applies suggested_resolution=reanchor → current cleared.
    assert out.state is not None
    assert out.state.current is None
    assert any("reanchored" in a for a in out.actions)


@pytest.mark.asyncio
async def test_resume_prefer_doc_refreshes_digest_on_concurrent_edit(wired):
    digest = await _seed_finalized_doc(wired)
    # Operator hand-edits discovery.md outside the L1 PO flow.
    p = discovery_path(wired["project_path"])
    p.write_text(p.read_text() + "\n<!-- operator note -->\n")
    new_digest = compute_discovery_doc_digest(wired["project_path"])
    assert new_digest != digest

    out = await handle_discovery_resume(
        project_path=wired["project_path"],
        reconcile_mode="prefer-doc",
    )
    kinds = [d.kind for d in out.divergences]
    assert StateDivergenceKind.CONCURRENT_EDIT in kinds
    assert out.state is not None
    assert out.state.discovery_doc_digest == new_digest


@pytest.mark.asyncio
async def test_resume_abandon_state_resets(wired):
    await _seed_finalized_doc(wired)
    state = load_discovery_state(wired["project_path"])
    state.status = DiscoveryStatus.IN_PROGRESS
    state.current = DiscoveryPhase(
        persona_id="merchant", journey_id="j-vanished", phase=3, step=1
    )
    state.partial_walk = [
        CapabilityCandidate(
            id="orphan-cap",
            description="orphan",
            journey_id="j-vanished",
            confirmed=False,
        )
    ]
    save_discovery_state(wired["project_path"], state)

    out = await handle_discovery_resume(
        project_path=wired["project_path"],
        reconcile_mode="abandon-state",
    )
    assert out.state is not None
    assert out.state.current is None
    assert out.state.partial_walk == []
    assert any("abandoned" in a for a in out.actions)


@pytest.mark.asyncio
async def test_resume_prompt_returns_divergences_without_applying(wired):
    await _seed_finalized_doc(wired)
    state = load_discovery_state(wired["project_path"])
    state.status = DiscoveryStatus.IN_PROGRESS
    state.current = DiscoveryPhase(
        persona_id="merchant", journey_id="j-vanished", phase=3, step=1
    )
    save_discovery_state(wired["project_path"], state)

    out = await handle_discovery_resume(
        project_path=wired["project_path"],
        reconcile_mode="prompt",
    )
    assert out.applied == "prompt"
    assert out.divergences  # at least one
    # State NOT mutated yet — caller must re-invoke with concrete mode.
    state_after = load_discovery_state(wired["project_path"])
    assert state_after.current is not None
    assert state_after.current.journey_id == "j-vanished"


@pytest.mark.asyncio
async def test_resume_drops_orphan_partial_walk(wired):
    await _seed_finalized_doc(wired)
    state = load_discovery_state(wired["project_path"])
    state.status = DiscoveryStatus.IN_PROGRESS
    state.partial_walk = [
        CapabilityCandidate(
            id="orphan-cap",
            description="orphan",
            journey_id="j-vanished",
            confirmed=False,
        ),
        CapabilityCandidate(
            id="ok-cap",
            description="lives",
            journey_id="j-merchant-onboarding",
            confirmed=True,
        ),
    ]
    save_discovery_state(wired["project_path"], state)

    out = await handle_discovery_resume(
        project_path=wired["project_path"],
        reconcile_mode="auto",
    )
    assert out.state is not None
    surviving_ids = {c.id for c in out.state.partial_walk}
    assert "orphan-cap" not in surviving_ids
    assert "ok-cap" in surviving_ids


# ---- handle_discovery_set_partial_walk -----------------------------------


@pytest.mark.asyncio
async def test_set_partial_walk_persists(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    await handle_discovery_set_partial_walk(
        project_path=tmp_path,
        candidates=[
            {
                "id": "fast-pay",
                "description": "one-click fast pay",
                "journey_id": "j-merchant-onboarding",
                "confirmed": False,
            }
        ],
    )
    state = load_discovery_state(tmp_path)
    assert len(state.partial_walk) == 1
    assert state.partial_walk[0].id == "fast-pay"
    assert state.partial_walk[0].confirmed is False


@pytest.mark.asyncio
async def test_set_partial_walk_replaces_prior(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    await handle_discovery_set_partial_walk(
        project_path=tmp_path,
        candidates=[
            {
                "id": "first",
                "description": "first",
                "journey_id": "j-x",
                "confirmed": False,
            }
        ],
    )
    await handle_discovery_set_partial_walk(
        project_path=tmp_path,
        candidates=[
            {
                "id": "second",
                "description": "second",
                "journey_id": "j-x",
                "confirmed": True,
            }
        ],
    )
    state = load_discovery_state(tmp_path)
    assert [c.id for c in state.partial_walk] == ["second"]


@pytest.mark.asyncio
async def test_set_partial_walk_rejects_invalid_entry(tmp_path: Path):
    (tmp_path / ".jig" / "spec").mkdir(parents=True)
    with pytest.raises(ValueError):
        await handle_discovery_set_partial_walk(
            project_path=tmp_path,
            candidates=[{"id": "BAD ID", "description": "x", "journey_id": "j-x"}],
        )


# ---- finalize stamps the digest -------------------------------------------


@pytest.mark.asyncio
async def test_finalize_stamps_digest_on_state(wired):
    await _seed_finalized_doc(wired)
    state = load_discovery_state(wired["project_path"])
    assert state.status == DiscoveryStatus.FINALIZED
    assert state.discovery_doc_digest != ""
    assert state.discovery_doc_digest == compute_discovery_doc_digest(
        wired["project_path"]
    )


# ---- state-divergence schema invariants ----------------------------------


def test_state_divergence_rejects_unknown_kind():
    with pytest.raises(Exception):
        StateDivergence(
            kind="totally-not-a-kind",
            detail="x",
            suggested_resolution="reanchor",
        )


def test_state_divergence_round_trips():
    d = StateDivergence(
        kind=StateDivergenceKind.STALE_JOURNEY,
        detail="x",
        suggested_resolution="reanchor",
    )
    payload = d.model_dump(mode="json")
    redo = StateDivergence.model_validate(payload)
    assert redo == d
