"""L1 PO MCP tool handlers + discovery doc renderer (Track B MVP)."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.po_l1_mcp import (
    L1_TICKET_ID,
    handle_discovery_add_capability,
    handle_discovery_add_journey,
    handle_discovery_add_persona,
    handle_discovery_clear_pending,
    handle_discovery_finalize,
    handle_discovery_load_state,
    handle_discovery_set_intro,
    handle_discovery_set_next_question,
    handle_discovery_set_phase,
    handle_discovery_set_playback,
    handle_discovery_stash_pending_capability,
    render_discovery_md,
)
from jig.schemas.po import (
    CapabilityRosterEntry,
    DiscoveryDoc,
    DiscoveryStatus,
    Journey,
    Persona,
)
from jig.spec_loader import (
    discovery_path,
    discovery_playback_path,
    discovery_state_path,
    load_discovery_state,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


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
    discovery_ticket = Ticket(
        id=L1_TICKET_ID,
        work_type=WorkType.BRIEF,
        title="L1 discovery — personas + journeys",
        created_by="cli",
    )
    await tickets.create(discovery_ticket)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
        "spec_dir": spec_dir,
    }


def _persona(pid: str = "merchant", desc: str = "merchant who integrates jig"):
    return Persona(id=pid, description=desc)


_DEFAULT_CAPS = ["self-serve-signup"]


def _journey(
    *,
    jid: str = "j-merchant-onboarding",
    persona_id: str = "merchant",
    title: str = "Merchant onboarding",
    narrative: str = "Merchant signs up, uploads catalog, installs snippet.",
    capability_ids: list[str] | None = None,
) -> Journey:
    # ``None`` means "use default"; the empty-list value is intentional
    # for tests that exercise the "journey has no capabilities" gate.
    caps = list(_DEFAULT_CAPS) if capability_ids is None else list(capability_ids)
    return Journey(
        id=jid,
        persona_id=persona_id,
        title=title,
        narrative=narrative,
        capability_ids=caps,
    )


def _roster_entry(
    *,
    cid: str = "self-serve-signup",
    desc: str = "Self-serve account creation with email",
    journey_ids: list[str] | None = None,
) -> CapabilityRosterEntry:
    return CapabilityRosterEntry(
        id=cid,
        description=desc,
        journey_ids=journey_ids or ["j-merchant-onboarding"],
    )


# ---- renderer -------------------------------------------------------------


def test_render_discovery_md_full_doc():
    doc = DiscoveryDoc(
        project_name="jig-search",
        intro="Discovery walk for jig-search.",
        personas=[
            _persona("merchant", "merchant who integrates jig"),
            _persona("customer", "shopper hitting the storefront"),
        ],
        journeys=[
            _journey(
                jid="j-merchant-onboarding",
                persona_id="merchant",
                title="Merchant onboarding",
                narrative=(
                    "Merchant signs up, uploads catalog via Shopify or "
                    "CSV, installs the JS snippet."
                ),
                capability_ids=["self-serve-signup", "shopify-connect"],
            ),
        ],
        capability_roster=[
            _roster_entry(
                cid="self-serve-signup",
                desc="Self-serve account creation with email",
                journey_ids=["j-merchant-onboarding"],
            ),
            _roster_entry(
                cid="shopify-connect",
                desc="Connect Shopify store via OAuth",
                journey_ids=["j-merchant-onboarding"],
            ),
        ],
    )
    md = render_discovery_md(doc)
    # Title shape per design.md.
    assert md.startswith("# jig-search — Discovery\n")
    assert "Discovery walk for jig-search." in md
    # Personas section + entries.
    assert "## Personas" in md
    assert "- {#merchant} merchant who integrates jig" in md
    assert "- {#customer} shopper hitting the storefront" in md
    # Journeys section + entry shape.
    assert "## Journeys" in md
    assert (
        "### Merchant onboarding {#j-merchant-onboarding} (persona: merchant)"
        in md
    )
    assert "Merchant signs up, uploads catalog" in md
    assert "Capabilities implied:" in md
    assert "- {#self-serve-signup} Self-serve account creation with email" in md
    # Roster lists journey citations.
    assert "## Capability roster" in md
    assert (
        "- {#shopify-connect} Connect Shopify store via OAuth "
        "(journeys: j-merchant-onboarding)" in md
    )
    # Trailing newline.
    assert md.endswith("\n")


def test_render_discovery_md_skips_intro_when_empty():
    doc = DiscoveryDoc(
        project_name="x",
        personas=[_persona()],
        journeys=[_journey()],
        capability_roster=[_roster_entry()],
    )
    md = render_discovery_md(doc)
    # Empty intro: don't render a blank paragraph between title and Personas.
    assert "# x — Discovery\n" in md
    head = md.split("## Personas", 1)[0]
    assert head.strip() == "# x — Discovery"


# ---- intro handler --------------------------------------------------------


@pytest.mark.asyncio
async def test_set_intro_writes_sidecar(wired):
    await handle_discovery_set_intro(
        project_path=wired["project_path"],
        intro="An intro paragraph for the discovery doc.",
    )
    sidecar = wired["spec_dir"] / "discovery.intro.txt"
    assert sidecar.is_file()
    assert "An intro paragraph" in sidecar.read_text()


@pytest.mark.asyncio
async def test_set_intro_rejects_blank(wired):
    with pytest.raises(ValueError, match="must not be empty"):
        await handle_discovery_set_intro(
            project_path=wired["project_path"],
            intro="   ",
        )


# ---- state handlers -------------------------------------------------------


@pytest.mark.asyncio
async def test_set_phase_writes_state_file(wired):
    await handle_discovery_set_phase(
        project_path=wired["project_path"],
        persona_id="merchant",
        journey_id="j-merchant-onboarding",
        phase=3,
        step=4,
    )
    state = load_discovery_state(wired["project_path"])
    assert state.current is not None
    assert state.current.persona_id == "merchant"
    assert state.current.journey_id == "j-merchant-onboarding"
    assert state.current.phase == 3
    assert state.current.step == 4
    assert state.status == DiscoveryStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_set_phase_handles_phase_one_without_persona(wired):
    """Phase 1 (frame) runs before the first persona is locked in."""
    await handle_discovery_set_phase(
        project_path=wired["project_path"],
        persona_id=None,
        journey_id=None,
        phase=1,
    )
    state = load_discovery_state(wired["project_path"])
    assert state.current is not None
    assert state.current.persona_id is None
    assert state.current.phase == 1


@pytest.mark.asyncio
async def test_set_next_question_persists(wired):
    await handle_discovery_set_next_question(
        project_path=wired["project_path"],
        question="What triggers her to open the app?",
    )
    state = load_discovery_state(wired["project_path"])
    assert state.next_question == "What triggers her to open the app?"


@pytest.mark.asyncio
async def test_set_next_question_rejects_blank(wired):
    with pytest.raises(ValueError, match="must not be empty"):
        await handle_discovery_set_next_question(
            project_path=wired["project_path"],
            question="",
        )


@pytest.mark.asyncio
async def test_stash_pending_capability_idempotent(wired):
    """Re-stashing the same (id, journey) is a no-op."""
    for _ in range(3):
        await handle_discovery_stash_pending_capability(
            project_path=wired["project_path"],
            capability_id="scan-summary",
            description="Scan summary for 'looks off' signals",
            journey_id="j-merchant-tuesday",
        )
    state = load_discovery_state(wired["project_path"])
    assert len(state.pending_capabilities) == 1
    assert state.pending_capabilities[0].id == "scan-summary"


@pytest.mark.asyncio
async def test_clear_pending_drops_only_that_journey(wired):
    await handle_discovery_stash_pending_capability(
        project_path=wired["project_path"],
        capability_id="cap-a",
        description="x",
        journey_id="j-one",
    )
    await handle_discovery_stash_pending_capability(
        project_path=wired["project_path"],
        capability_id="cap-b",
        description="x",
        journey_id="j-two",
    )
    await handle_discovery_clear_pending(
        project_path=wired["project_path"],
        journey_id="j-one",
    )
    state = load_discovery_state(wired["project_path"])
    assert [p.id for p in state.pending_capabilities] == ["cap-b"]


@pytest.mark.asyncio
async def test_load_state_returns_none_when_absent(wired):
    out = await handle_discovery_load_state(project_path=wired["project_path"])
    assert out == {"state": None}


@pytest.mark.asyncio
async def test_load_state_returns_dump(wired):
    await handle_discovery_set_phase(
        project_path=wired["project_path"],
        persona_id="merchant",
        journey_id="j-x",
        phase=2,
    )
    out = await handle_discovery_load_state(project_path=wired["project_path"])
    assert out["state"] is not None
    assert out["state"]["current"]["phase"] == 2


# ---- playback handler -----------------------------------------------------


@pytest.mark.asyncio
async def test_set_playback_writes_per_journey_md(wired):
    await handle_discovery_set_playback(
        project_path=wired["project_path"],
        journey_id="j-merchant-onboarding",
        playback_text=(
            "1. 9am Slack summary lands\n"
            "2. Manager scans for 'looks off' signals\n"
            "3. Clicks through if something needs action\n"
        ),
    )
    pb = discovery_playback_path(wired["project_path"], "j-merchant-onboarding")
    assert pb.is_file()
    body = pb.read_text()
    assert "9am Slack summary" in body
    assert "looks off" in body


@pytest.mark.asyncio
async def test_set_playback_rejects_blank_text(wired):
    with pytest.raises(ValueError):
        await handle_discovery_set_playback(
            project_path=wired["project_path"],
            journey_id="j-x",
            playback_text="   ",
        )


# ---- staged add handlers --------------------------------------------------


@pytest.mark.asyncio
async def test_add_persona_validates_kebab(wired):
    """Schema-level validation fires immediately, not at finalize time."""
    with pytest.raises(Exception):
        await handle_discovery_add_persona(
            project_path=wired["project_path"],
            persona_id="Bad ID",
            description="x",
        )


@pytest.mark.asyncio
async def test_add_journey_writes_inline_playback(wired):
    await handle_discovery_add_journey(
        project_path=wired["project_path"],
        persona_id="merchant",
        journey_id="j-merchant-onboarding",
        title="Merchant onboarding",
        narrative="Merchant signs up.",
        capability_ids=["self-serve-signup"],
        playback_text="1. Sign up\n2. Upload catalog\n",
    )
    pb = discovery_playback_path(wired["project_path"], "j-merchant-onboarding")
    assert pb.is_file()
    assert "Sign up" in pb.read_text()


@pytest.mark.asyncio
async def test_add_capability_merges_journey_ids(wired):
    """Re-calling with the same id union-merges journey citations."""
    await handle_discovery_add_capability(
        project_path=wired["project_path"],
        capability_id="self-serve-signup",
        description="Self-serve account creation",
        journey_ids=["j-merchant-onboarding"],
    )
    await handle_discovery_add_capability(
        project_path=wired["project_path"],
        capability_id="self-serve-signup",
        description="DIFFERENT — first wins",
        journey_ids=["j-merchant-second-thoughts"],
    )
    roster_path = wired["spec_dir"] / "discovery.roster.yaml"
    items = yaml.safe_load(roster_path.read_text())
    assert len(items) == 1
    entry = items[0]
    # First-mention description wins.
    assert entry["description"] == "Self-serve account creation"
    # Journey ids are unioned in first-mention order.
    assert entry["journey_ids"] == [
        "j-merchant-onboarding",
        "j-merchant-second-thoughts",
    ]


# ---- discovery_finalize ---------------------------------------------------


@pytest.mark.asyncio
async def test_finalize_writes_discovery_md(wired):
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="jig-search",
        intro="Discovery walk for jig-search.",
        personas=[_persona().model_dump(mode="json")],
        journeys=[_journey().model_dump(mode="json")],
        capability_roster=[_roster_entry().model_dump(mode="json")],
        author="po-l1",
    )
    md_path = discovery_path(wired["project_path"])
    assert md_path.is_file()
    body = md_path.read_text()
    assert "# jig-search — Discovery" in body
    assert "## Personas" in body
    assert "## Journeys" in body
    assert "## Capability roster" in body
    assert "j-merchant-onboarding" in body


@pytest.mark.asyncio
async def test_finalize_writes_structured_cache(wired):
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="jig-search",
        personas=[_persona().model_dump(mode="json")],
        journeys=[_journey().model_dump(mode="json")],
        capability_roster=[_roster_entry().model_dump(mode="json")],
        author="po-l1",
    )
    cache = wired["spec_dir"] / "discovery.structured.yaml"
    assert cache.is_file()
    data = yaml.safe_load(cache.read_text())
    DiscoveryDoc.model_validate(data)
    assert data["project_name"] == "jig-search"


@pytest.mark.asyncio
async def test_finalize_resolves_ticket(wired):
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="x",
        personas=[_persona().model_dump(mode="json")],
        journeys=[_journey().model_dump(mode="json")],
        capability_roster=[_roster_entry().model_dump(mode="json")],
        author="po-l1",
    )
    t = await wired["tickets"].get(L1_TICKET_ID)
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_finalize_emits_handoff_to_l2(wired):
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="x",
        personas=[_persona().model_dump(mode="json")],
        journeys=[_journey().model_dump(mode="json")],
        capability_roster=[_roster_entry().model_dump(mode="json")],
        author="po-l1",
    )
    entries = await wired["threads"].for_ticket(L1_TICKET_ID)
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    h = handoffs[0]
    # Per design.md §"L1 — Discovery" → L2 PO is the next phase.
    assert h.phase == "po-l2"
    assert ".jig/spec/discovery.md" in h.outputs


@pytest.mark.asyncio
async def test_finalize_marks_state_finalized(wired):
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="x",
        personas=[_persona().model_dump(mode="json")],
        journeys=[_journey().model_dump(mode="json")],
        capability_roster=[_roster_entry().model_dump(mode="json")],
        author="po-l1",
    )
    state = load_discovery_state(wired["project_path"])
    assert state.status == DiscoveryStatus.FINALIZED


@pytest.mark.asyncio
async def test_finalize_drains_sidecars(wired):
    """After finalize, staged sidecars + intro cache must be gone.

    Otherwise a subsequent re-finalize would silently re-pick stale
    staged entries the operator may have abandoned.
    """
    await handle_discovery_set_intro(
        project_path=wired["project_path"],
        intro="will be drained",
    )
    await handle_discovery_add_persona(
        project_path=wired["project_path"],
        persona_id="merchant",
        description="staged",
    )
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="x",
        personas=[_persona().model_dump(mode="json")],
        journeys=[_journey().model_dump(mode="json")],
        capability_roster=[_roster_entry().model_dump(mode="json")],
        author="po-l1",
    )
    spec_dir = wired["spec_dir"]
    assert not (spec_dir / "discovery.intro.txt").exists()
    assert not (spec_dir / "discovery.personas.yaml").exists()
    assert not (spec_dir / "discovery.journeys.yaml").exists()
    assert not (spec_dir / "discovery.roster.yaml").exists()


@pytest.mark.asyncio
async def test_finalize_consumes_staged_when_no_explicit_payload(wired):
    """LLM-driven path: per-tool stages, then finalize with no kwargs."""
    await handle_discovery_set_intro(
        project_path=wired["project_path"],
        intro="From staged calls.",
    )
    await handle_discovery_add_persona(
        project_path=wired["project_path"],
        persona_id="merchant",
        description="merchant who integrates jig",
    )
    await handle_discovery_add_journey(
        project_path=wired["project_path"],
        persona_id="merchant",
        journey_id="j-merchant-onboarding",
        title="Merchant onboarding",
        narrative="Merchant signs up.",
        capability_ids=["self-serve-signup"],
    )
    await handle_discovery_add_capability(
        project_path=wired["project_path"],
        capability_id="self-serve-signup",
        description="Self-serve account creation",
        journey_ids=["j-merchant-onboarding"],
    )
    await handle_discovery_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        project_name="jig-search",
        author="po-l1",
    )
    body = discovery_path(wired["project_path"]).read_text()
    assert "From staged calls." in body
    assert "{#merchant}" in body
    assert "{#j-merchant-onboarding}" in body
    assert "{#self-serve-signup}" in body


@pytest.mark.asyncio
async def test_finalize_rejects_persona_without_journey(wired):
    """Design gate: every persona needs at least one journey."""
    with pytest.raises(ValueError, match="no journeys"):
        await handle_discovery_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            project_name="x",
            personas=[
                _persona().model_dump(mode="json"),
                _persona("customer", "shopper").model_dump(mode="json"),
            ],
            journeys=[_journey().model_dump(mode="json")],  # only merchant
            capability_roster=[_roster_entry().model_dump(mode="json")],
            author="po-l1",
        )


@pytest.mark.asyncio
async def test_finalize_rejects_journey_without_capability(wired):
    """Design gate: every journey extracts at least one capability."""
    bad = _journey(capability_ids=[])
    with pytest.raises(ValueError, match="no capabilities"):
        await handle_discovery_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            project_name="x",
            personas=[_persona().model_dump(mode="json")],
            journeys=[bad.model_dump(mode="json")],
            capability_roster=[],
            author="po-l1",
        )


@pytest.mark.asyncio
async def test_finalize_rejects_journey_with_unknown_persona(wired):
    bad = _journey(persona_id="ghost")
    with pytest.raises(ValueError, match="unknown persona"):
        await handle_discovery_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            project_name="x",
            personas=[_persona().model_dump(mode="json")],
            journeys=[bad.model_dump(mode="json")],
            capability_roster=[_roster_entry().model_dump(mode="json")],
            author="po-l1",
        )


@pytest.mark.asyncio
async def test_finalize_rejects_journey_capability_not_in_roster(wired):
    j = _journey(capability_ids=["mystery-cap"])
    with pytest.raises(ValueError, match="not in the capability roster"):
        await handle_discovery_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            project_name="x",
            personas=[_persona().model_dump(mode="json")],
            journeys=[j.model_dump(mode="json")],
            capability_roster=[_roster_entry().model_dump(mode="json")],
            author="po-l1",
        )


@pytest.mark.asyncio
async def test_finalize_rejects_blank_project_name(wired):
    with pytest.raises(ValueError):
        await handle_discovery_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            project_name="   ",
            personas=[_persona().model_dump(mode="json")],
            journeys=[_journey().model_dump(mode="json")],
            capability_roster=[_roster_entry().model_dump(mode="json")],
            author="po-l1",
        )


@pytest.mark.asyncio
async def test_finalize_does_not_write_discovery_md_on_validation_failure(
    wired,
):
    """Atomic-ish: validation failure leaves discovery.md untouched."""
    with pytest.raises(ValueError):
        await handle_discovery_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            project_name="x",
            personas=[_persona().model_dump(mode="json")],
            journeys=[],  # zero journeys → persona has no journey
            capability_roster=[],
            author="po-l1",
        )
    assert not discovery_path(wired["project_path"]).exists()
    # The state file should NOT have been flipped to FINALIZED.
    assert not discovery_state_path(wired["project_path"]).exists()
