"""Cascade failure-mode mitigations (Track C Final).

Per ``docs/v2.0/sa-architecture/design.md`` §"Failure modes and mitigations":
the MVP cascade-after-impossible workflow lacks four mitigations the
Final scope adds:

1. Rejected-cascade audit-flagging — operator rejects → structured
   audit entry under ``.jig/arch/cascades/audit.jsonl`` so cross-project
   analytics can flag patterns.
2. Cascade staging for huge cascades — when N+ contracts in one cascade,
   the operator gets per-stage approval rather than all-or-nothing.
3. ``mitigated_with_constraints`` risk state — spike returns "depends on
   constraint X"; the cascade fires conditionally on the constraint.
4. Serialization of concurrent overlapping cascades — two cascades that
   touch overlapping contract URIs serialize via a ``holding_for`` link.

Each mitigation has its own narrow handler in
``jig.sa_incremental_mcp``; this module is the regression suite.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from jig.analytics.emitter import EventEmitter
from jig.analytics.store import AnalyticsStore
from jig.sa_incremental_mcp import (
    handle_arch_approve_cascade_stage,
    handle_arch_complete_spike,
    handle_arch_propose_spike,
    handle_arch_reject_cascade,
    handle_arch_set_risk,
    handle_arch_stage_cascade,
)
from jig.sa_mcp import SA_TICKET_ID
from jig.schemas.arch import (
    CascadeAuditEntry,
    CascadeProposal,
    CascadeState,
    RiskStatus,
)
from jig.spec_loader import (
    cascade_audit_path,
    cascades_dir,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
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


_DEPENDENTS_TWO = [
    "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
    "project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect",
]

_DEPENDENTS_OVERLAP = [
    # Overlaps with _DEPENDENTS_TWO[0] for the concurrent-hold test.
    "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
    "project://arch/modules/categorization/contracts#integration_ac/dedupe",
]


async def _seed_cascade(
    wired: dict,
    *,
    risk_id: str = "r-shopify-delta",
    dependents: list[str] | None = None,
    finding: str = "Shopify API does not expose reliable delta sync.",
) -> tuple[str, Path]:
    """Author risk + propose spike + complete confirmed_impossible.

    Returns ``(spike_id, cascade_yaml_path)``. Used as a one-liner in
    every test that needs an existing cascade to act on.
    """
    deps = list(dependents) if dependents is not None else list(_DEPENDENTS_TWO)
    await handle_arch_set_risk(
        project_path=wired["project_path"],
        risk={
            "id": risk_id,
            "text": "Risk seed for cascade mitigation tests.",
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
        risk_id=risk_id,
        summary="Spike to validate the assumption.",
        dependent_contracts=deps,
        author="sa-mvp",
    )
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding=finding,
        status="confirmed_impossible",
        author="sa-mvp",
        emitter=wired["emitter"],
    )
    target_dir = cascades_dir(wired["project_path"])
    proposals = sorted(target_dir.glob(f"{risk_id}-*.yaml"))
    assert proposals, "expected one cascade proposal for the seeded risk"
    return spike_id, proposals[-1]


def _load_proposal(path: Path) -> CascadeProposal:
    return CascadeProposal.model_validate(yaml.safe_load(path.read_text()))


def _read_audit(project_path: Path) -> list[CascadeAuditEntry]:
    audit = cascade_audit_path(project_path)
    if not audit.is_file():
        return []
    out: list[CascadeAuditEntry] = []
    for line in audit.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        out.append(CascadeAuditEntry.model_validate(json.loads(line)))
    return out


# ---- mitigation #1: rejected-cascade audit-flagging -----------------------


@pytest.mark.asyncio
async def test_proposed_cascade_appends_audit_entry(wired):
    """A new cascade always gets a ``proposed`` audit entry."""
    _spike_id, path = await _seed_cascade(wired)
    entries = _read_audit(wired["project_path"])
    proposed = [e for e in entries if e.action == "proposed"]
    assert len(proposed) == 1
    proposal = _load_proposal(path)
    assert proposed[0].cascade_id == proposal.cascade_id
    assert proposed[0].risk_id == proposal.risk_id


@pytest.mark.asyncio
async def test_reject_cascade_writes_audit_entry_and_flips_state(wired):
    _spike_id, path = await _seed_cascade(wired)
    proposal = _load_proposal(path)
    audit_entry = await handle_arch_reject_cascade(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        reason="contract_already_rewritten",
        actor="operator",
    )
    assert isinstance(audit_entry, CascadeAuditEntry)
    assert audit_entry.action == "rejected"
    assert audit_entry.reason == "contract_already_rewritten"
    # Proposal on disk reflects the new state + reason.
    refreshed = _load_proposal(path)
    assert refreshed.state == CascadeState.REJECTED
    assert refreshed.rejected_reason == "contract_already_rewritten"
    # Audit log carries both the proposed entry and the rejection.
    actions = [e.action for e in _read_audit(wired["project_path"])]
    assert actions == ["proposed", "rejected"]


@pytest.mark.asyncio
async def test_reject_cascade_unknown_id_raises(wired):
    await _seed_cascade(wired)
    with pytest.raises(KeyError):
        await handle_arch_reject_cascade(
            project_path=wired["project_path"],
            cascade_id="nonexistent-cascade-12345",
            reason="typo",
            actor="operator",
        )


# ---- mitigation #2: cascade staging ---------------------------------------


@pytest.mark.asyncio
async def test_stage_cascade_splits_into_chunks(wired):
    """A cascade with N contracts split with chunk_size=1 → N stages."""
    _spike_id, path = await _seed_cascade(
        wired,
        dependents=[
            "project://arch/modules/m/contracts#a/x",
            "project://arch/modules/m/contracts#a/y",
            "project://arch/modules/m/contracts#a/z",
        ],
    )
    proposal = _load_proposal(path)
    stages = await handle_arch_stage_cascade(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        chunk_size=1,
        actor="operator",
    )
    assert len(stages) == 3
    # Each stage carries exactly one contract (chunk_size=1).
    for s in stages:
        assert len(s.contracts) == 1
    # Stage ids are stable + ordered for deterministic operator UX.
    assert [s.stage_id for s in stages] == [
        f"{proposal.cascade_id}-stage-1",
        f"{proposal.cascade_id}-stage-2",
        f"{proposal.cascade_id}-stage-3",
    ]
    refreshed = _load_proposal(path)
    assert refreshed.state == CascadeState.STAGED
    assert len(refreshed.stages) == 3
    # Audit trail captures the staging action.
    actions = [e.action for e in _read_audit(wired["project_path"])]
    assert "staged" in actions


@pytest.mark.asyncio
async def test_stage_cascade_default_chunk_size_is_5(wired):
    """Default chunk_size=5 per the task spec."""
    _spike_id, path = await _seed_cascade(
        wired,
        dependents=[
            f"project://arch/modules/m/contracts#a/x{i}" for i in range(12)
        ],
    )
    proposal = _load_proposal(path)
    stages = await handle_arch_stage_cascade(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        actor="operator",
    )
    # 12 contracts / 5 per stage → 5, 5, 2.
    assert [len(s.contracts) for s in stages] == [5, 5, 2]


@pytest.mark.asyncio
async def test_approve_stage_marks_stage_only(wired):
    _spike_id, path = await _seed_cascade(
        wired,
        dependents=[
            "project://arch/modules/m/contracts#a/x",
            "project://arch/modules/m/contracts#a/y",
        ],
    )
    proposal = _load_proposal(path)
    stages = await handle_arch_stage_cascade(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        chunk_size=1,
        actor="operator",
    )
    first = await handle_arch_approve_cascade_stage(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        stage_id=stages[0].stage_id,
        actor="operator",
    )
    assert first.approved is True
    assert first.approved_by == "operator"
    refreshed = _load_proposal(path)
    # Other stage is still un-approved; cascade state remains staged.
    other = next(s for s in refreshed.stages if s.stage_id != stages[0].stage_id)
    assert other.approved is False
    assert refreshed.state == CascadeState.STAGED
    actions = [e.action for e in _read_audit(wired["project_path"])]
    assert actions.count("stage_approved") == 1


@pytest.mark.asyncio
async def test_approve_all_stages_resolves_cascade(wired):
    _spike_id, path = await _seed_cascade(
        wired,
        dependents=[
            "project://arch/modules/m/contracts#a/x",
            "project://arch/modules/m/contracts#a/y",
        ],
    )
    proposal = _load_proposal(path)
    stages = await handle_arch_stage_cascade(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        chunk_size=1,
        actor="operator",
    )
    for s in stages:
        await handle_arch_approve_cascade_stage(
            project_path=wired["project_path"],
            cascade_id=proposal.cascade_id,
            stage_id=s.stage_id,
            actor="operator",
        )
    refreshed = _load_proposal(path)
    assert refreshed.state == CascadeState.RESOLVED
    actions = [e.action for e in _read_audit(wired["project_path"])]
    assert actions.count("resolved") == 1


@pytest.mark.asyncio
async def test_approve_stage_unknown_stage_raises(wired):
    _spike_id, path = await _seed_cascade(
        wired,
        dependents=["project://arch/modules/m/contracts#a/x"],
    )
    proposal = _load_proposal(path)
    await handle_arch_stage_cascade(
        project_path=wired["project_path"],
        cascade_id=proposal.cascade_id,
        chunk_size=1,
        actor="operator",
    )
    with pytest.raises(KeyError):
        await handle_arch_approve_cascade_stage(
            project_path=wired["project_path"],
            cascade_id=proposal.cascade_id,
            stage_id="bogus-stage-id",
            actor="operator",
        )


# ---- mitigation #3: mitigated_with_constraints ---------------------------


@pytest.mark.asyncio
async def test_mitigated_with_constraints_emits_constraint_cascade(wired):
    """Spike with status=mitigated_with_constraints + constraint → cascade
    artifact carries the constraint clause and risk transitions to the
    new state."""
    await handle_arch_set_risk(
        project_path=wired["project_path"],
        risk={
            "id": "r-shopify-shop-size",
            "text": "Delta sync may degrade above 10K SKUs.",
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
        risk_id="r-shopify-shop-size",
        summary="Validate delta sync at scale.",
        dependent_contracts=[
            "project://arch/modules/catalog-ingest/contracts#external_dependencies/shopify-api",
        ],
        author="sa-mvp",
    )
    await handle_arch_complete_spike(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        spike_ticket_id=spike_id,
        finding="Works for shop_size < 10K SKUs; degrades above.",
        status="mitigated_with_constraints",
        author="sa-mvp",
        emitter=wired["emitter"],
        constraint="shop_size < 10000",
    )
    target_dir = cascades_dir(wired["project_path"])
    proposals = sorted(target_dir.glob("r-shopify-shop-size-*.yaml"))
    assert proposals, "expected a constraint-cascade artifact"
    proposal = _load_proposal(proposals[-1])
    assert proposal.constraint == "shop_size < 10000"
    # Risk in arch.yaml moved to the new state.
    from jig.spec_loader import load_architecture

    arch = load_architecture(wired["project_path"])
    risk = next(r for r in arch.risks if r.id == "r-shopify-shop-size")
    assert risk.status == RiskStatus.MITIGATED_WITH_CONSTRAINTS


# ---- mitigation #4: serialization of concurrent overlapping cascades ----


@pytest.mark.asyncio
async def test_overlapping_cascade_held_for_first(wired):
    """Two cascades touching the same contract URI: second is held for
    the first (failure mode #4 mitigation)."""
    # First cascade — resolves to confirmed_impossible, lands as pending.
    _first_spike, first_path = await _seed_cascade(
        wired,
        risk_id="r-first",
        dependents=_DEPENDENTS_TWO,
    )
    first = _load_proposal(first_path)
    assert first.state == CascadeState.PENDING
    # Second cascade — overlaps with the first via shopify-api URI.
    _second_spike, second_path = await _seed_cascade(
        wired,
        risk_id="r-second",
        dependents=_DEPENDENTS_OVERLAP,
    )
    second = _load_proposal(second_path)
    assert second.state == CascadeState.HOLDING
    assert second.holding_for == first.cascade_id
    actions = [e.action for e in _read_audit(wired["project_path"])]
    # Second cascade gets a "holding" audit entry on top of "proposed".
    assert "holding" in actions


@pytest.mark.asyncio
async def test_non_overlapping_cascades_both_pending(wired):
    """Two cascades with disjoint dependent_contracts both stay pending."""
    _first_spike, first_path = await _seed_cascade(
        wired,
        risk_id="r-first",
        dependents=[
            "project://arch/modules/a/contracts#x/1",
        ],
    )
    _second_spike, second_path = await _seed_cascade(
        wired,
        risk_id="r-second",
        dependents=[
            "project://arch/modules/b/contracts#y/2",
        ],
    )
    first = _load_proposal(first_path)
    second = _load_proposal(second_path)
    assert first.state == CascadeState.PENDING
    assert second.state == CascadeState.PENDING
    assert second.holding_for is None


@pytest.mark.asyncio
async def test_resolving_first_cascade_does_not_auto_release_held(wired):
    """Resolving the held-for cascade is enough to make the held one
    actionable, but auto-release is out of Final scope — the operator
    re-runs whichever step they need next. Verify the second cascade
    still carries its holding_for link until acted on (so a viewer can
    show 'released — was holding for X' once the first lands).

    This is a 'documents the contract' test: failure mode #4's mitigation
    is the hold itself; the release plumbing is v2.x.
    """
    _first_spike, first_path = await _seed_cascade(
        wired,
        risk_id="r-first",
        dependents=_DEPENDENTS_TWO,
    )
    first = _load_proposal(first_path)
    _second_spike, second_path = await _seed_cascade(
        wired,
        risk_id="r-second",
        dependents=_DEPENDENTS_OVERLAP,
    )
    # Reject the first.
    await handle_arch_reject_cascade(
        project_path=wired["project_path"],
        cascade_id=first.cascade_id,
        reason="not_relevant",
        actor="operator",
    )
    # Second still references the first via holding_for.
    second_after = _load_proposal(second_path)
    assert second_after.holding_for == first.cascade_id
