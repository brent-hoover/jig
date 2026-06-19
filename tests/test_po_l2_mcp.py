"""L2 PO MCP tool handlers + capability-coverage validator (Track B4 MVP)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.atomic import atomic_write_text
from jig.po_l2_mcp import (
    L2_TICKET_ID,
    SOFT_MAX_CAPS_PER_SUITE,
    SOFT_MIN_CAPS_PER_SUITE,
    compute_size_warnings,
    handle_l2_finalize,
    validate_capability_coverage,
)
from jig.schemas.po import (
    CapabilityRosterEntry,
    DiscoveryDoc,
    Journey,
    Persona,
    Suite,
    SuitesIndex,
)
from jig.spec_loader import suites_index_path
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


# ---- helpers --------------------------------------------------------------


def _make_discovery(capability_ids: list[str]) -> DiscoveryDoc:
    """Build a minimal but valid DiscoveryDoc with the given capabilities.

    Keeps tests focused on L2's coverage rule — the L1 invariants
    (every persona has a journey, etc.) are L1's responsibility, but
    we still need a parseable doc so ``load_discovery`` can return.
    """
    return DiscoveryDoc(
        project_name="l2-test",
        intro="",
        personas=[Persona(id="merchant", description="merchant persona")],
        journeys=[
            Journey(
                id="j-merchant-onboarding",
                persona_id="merchant",
                title="Merchant onboarding",
                narrative="signs up + connects + indexes",
                capability_ids=capability_ids,
            )
        ],
        capability_roster=[
            CapabilityRosterEntry(
                id=cid,
                description=f"capability {cid}",
                journey_ids=["j-merchant-onboarding"],
            )
            for cid in capability_ids
        ],
    )


def _write_discovery(project_path: Path, doc: DiscoveryDoc) -> None:
    """Persist a DiscoveryDoc to ``.jig/spec/discovery.structured.yaml``.

    The L2 finalize handler reads via ``load_discovery``, which expects
    the structured cache that the L1 PO writes at finalize time. Tests
    skip the L1 finalize path and seed this file directly.
    """
    cache = project_path / ".jig" / "spec" / "discovery.structured.yaml"
    cache.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        cache,
        yaml.safe_dump(doc.model_dump(mode="json"), sort_keys=False),
    )


def _suite(
    *,
    sid: str = "catalog",
    title: str | None = None,
    summary: str = "ingestion + normalization",
    capabilities: list[str] | None = None,
) -> Suite:
    return Suite(
        id=sid,
        title=title or sid.title(),
        summary=summary,
        capabilities=list(capabilities or []),
    )


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    suites_ticket = Ticket(
        id=L2_TICKET_ID,
        work_type=WorkType.BRIEF,
        title="L2 suite organization",
        created_by="cli",
    )
    await tickets.create(suites_ticket)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
        "spec_dir": spec_dir,
    }


# ---- coverage validator --------------------------------------------------


def test_validate_coverage_accepts_complete_partition():
    discovery = _make_discovery(["a", "b", "c", "d"])
    suites = [
        _suite(sid="onboarding", capabilities=["a", "b"]),
        _suite(sid="catalog", capabilities=["c", "d"]),
    ]
    validate_capability_coverage(suites=suites, discovery=discovery)


def test_validate_coverage_rejects_missing_roster_capability():
    discovery = _make_discovery(["a", "b", "c"])
    suites = [_suite(capabilities=["a", "b"])]  # missing 'c'
    with pytest.raises(ValueError, match="not in any suite"):
        validate_capability_coverage(suites=suites, discovery=discovery)


def test_validate_coverage_rejects_extra_unknown_capability():
    """An L2 grouping that invents an id not in L1's roster is a gap."""
    discovery = _make_discovery(["a", "b"])
    suites = [_suite(capabilities=["a", "b", "c-rogue"])]
    with pytest.raises(ValueError, match="not in L1 roster"):
        validate_capability_coverage(suites=suites, discovery=discovery)


def test_validate_coverage_rejects_capability_in_two_suites():
    """A capability belongs to exactly one suite — design rule."""
    discovery = _make_discovery(["a", "b"])
    suites = [
        _suite(sid="s1", capabilities=["a", "b"]),
        _suite(sid="s2", capabilities=["b"]),  # 'b' is in both
    ]
    with pytest.raises(ValueError, match="assigned to >1 suite"):
        validate_capability_coverage(suites=suites, discovery=discovery)


def test_validate_coverage_rejects_duplicate_suite_ids():
    discovery = _make_discovery(["a", "b"])
    suites = [
        _suite(sid="dupe", capabilities=["a"]),
        _suite(sid="dupe", capabilities=["b"]),
    ]
    with pytest.raises(ValueError, match="duplicate suite ids"):
        validate_capability_coverage(suites=suites, discovery=discovery)


def test_validate_coverage_surfaces_all_problems_in_one_message():
    """One error string carries every diagnostic — the LLM doesn't have
    to re-attempt three times to see the full picture."""
    discovery = _make_discovery(["a", "b", "c"])
    suites = [
        _suite(sid="s1", capabilities=["a", "rogue"]),  # extra
        _suite(sid="s2", capabilities=["a"]),  # duplicate of 'a'
        # Missing 'b' and 'c'.
    ]
    with pytest.raises(ValueError) as exc:
        validate_capability_coverage(suites=suites, discovery=discovery)
    msg = str(exc.value)
    assert "not in any suite" in msg
    assert "not in L1 roster" in msg
    assert "assigned to >1 suite" in msg


# ---- size warnings --------------------------------------------------------


def test_size_warnings_quiet_within_target():
    """Suites with 3-5 capabilities produce no warnings."""
    suites = [_suite(sid="s", capabilities=["a", "b", "c"])]
    assert compute_size_warnings(suites) == []


def test_size_warnings_too_few():
    suites = [_suite(sid="thin", capabilities=["a"])]
    warnings = compute_size_warnings(suites)
    assert len(warnings) == 1
    assert "thin" in warnings[0]
    assert "merging" in warnings[0]


def test_size_warnings_too_many():
    suites = [_suite(sid="fat", capabilities=list("abcdefg"))]  # 7
    warnings = compute_size_warnings(suites)
    assert len(warnings) == 1
    assert "fat" in warnings[0]
    assert "splitting" in warnings[0]


def test_size_warnings_assert_soft_constants():
    """Sanity: the constants the design names match what we ship."""
    assert SOFT_MIN_CAPS_PER_SUITE == 3
    assert SOFT_MAX_CAPS_PER_SUITE == 5


# ---- l2_finalize ---------------------------------------------------------


@pytest.mark.asyncio
async def test_l2_finalize_writes_suites_yaml(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a", "b", "c"]))
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[
            {
                "id": "onboarding",
                "title": "Onboarding",
                "summary": "x",
                "capabilities": ["a"],
            },
            {
                "id": "catalog",
                "title": "Catalog",
                "summary": "y",
                "capabilities": ["b", "c"],
            },
        ],
        author="po-l2",
    )
    yaml_path = suites_index_path(wired["project_path"])
    assert yaml_path.is_file()
    data = yaml.safe_load(yaml_path.read_text())
    # Order is preserved — operator-readable form mirrors what the LLM
    # proposed.
    assert [s["id"] for s in data["suites"]] == ["onboarding", "catalog"]
    assert data["spec_version"] == 2
    # Round-trips through SuitesIndex.
    SuitesIndex.model_validate(data)


@pytest.mark.asyncio
async def test_l2_finalize_resolves_ticket(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a", "b", "c"]))
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[
            _suite(sid="s", capabilities=["a", "b", "c"]),
        ],
        author="po-l2",
    )
    t = await wired["tickets"].get(L2_TICKET_ID)
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_l2_finalize_emits_handoff_to_l3(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a", "b", "c"]))
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[_suite(capabilities=["a", "b", "c"])],
        author="po-l2",
    )
    entries = await wired["threads"].for_ticket(L2_TICKET_ID)
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    h = handoffs[0]
    # Per design.md §"L2 — Suite organization", L2 hands off to L3.
    assert h.phase == "po-l3"
    assert ".jig/spec/suites.yaml" in h.outputs


@pytest.mark.asyncio
async def test_l2_finalize_summary_includes_warnings_for_thin_suites(wired):
    """Soft target violations surface in the handoff summary, not as errors."""
    _write_discovery(wired["project_path"], _make_discovery(["a"]))
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[_suite(sid="lonely", capabilities=["a"])],  # 1 cap → warn
        author="po-l2",
    )
    entries = await wired["threads"].for_ticket(L2_TICKET_ID)
    h = next(e for e in entries if e.kind == "handoff")
    assert "warnings" in h.summary
    assert "lonely" in h.summary


@pytest.mark.asyncio
async def test_l2_finalize_rejects_empty_suites(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a"]))
    with pytest.raises(ValueError, match="at least one suite"):
        await handle_l2_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suites=[],
            author="po-l2",
        )


@pytest.mark.asyncio
async def test_l2_finalize_rejects_missing_capability(wired):
    """The coverage validator's missing-id branch fires on real input."""
    _write_discovery(wired["project_path"], _make_discovery(["a", "b"]))
    with pytest.raises(ValueError, match="not in any suite"):
        await handle_l2_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suites=[_suite(sid="s", capabilities=["a"])],
            author="po-l2",
        )
    # Atomic-ish: rejection must NOT have written suites.yaml.
    assert not suites_index_path(wired["project_path"]).exists()


@pytest.mark.asyncio
async def test_l2_finalize_rejects_capability_outside_roster(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a", "b"]))
    with pytest.raises(ValueError, match="not in L1 roster"):
        await handle_l2_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suites=[_suite(capabilities=["a", "b", "rogue"])],
            author="po-l2",
        )


@pytest.mark.asyncio
async def test_l2_finalize_rejects_capability_in_two_suites(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a", "b"]))
    with pytest.raises(ValueError, match="assigned to >1 suite"):
        await handle_l2_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suites=[
                _suite(sid="s1", capabilities=["a", "b"]),
                _suite(sid="s2", capabilities=["b"]),
            ],
            author="po-l2",
        )


@pytest.mark.asyncio
async def test_l2_finalize_missing_discovery_raises(wired):
    """No discovery → can't validate coverage → fail loudly with FileNotFoundError.

    Bones expects L1 to have finalized first. Silent default to "no
    roster" would let any grouping through.
    """
    # No discovery cache written.
    with pytest.raises(FileNotFoundError):
        await handle_l2_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suites=[_suite(capabilities=["a"])],
            author="po-l2",
        )


@pytest.mark.asyncio
async def test_l2_finalize_rejects_invalid_suite_dict(wired):
    """Pydantic schema errors propagate as ValueError with friendly text."""
    _write_discovery(wired["project_path"], _make_discovery(["a"]))
    with pytest.raises(ValueError, match="invalid suite entry"):
        await handle_l2_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            # Missing required ``title`` + ``summary``.
            suites=[{"id": "s", "capabilities": ["a"]}],
            author="po-l2",
        )


@pytest.mark.asyncio
async def test_l2_finalize_carries_crosscutting_non_goals(wired):
    _write_discovery(wired["project_path"], _make_discovery(["a"]))
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[_suite(capabilities=["a"])],
        crosscutting_non_goals=[
            {"id": "no-cms", "text": "We will not build a CMS"},
        ],
        author="po-l2",
    )
    data = yaml.safe_load(suites_index_path(wired["project_path"]).read_text())
    assert data["crosscutting_non_goals"] == [
        {"id": "no-cms", "text": "We will not build a CMS", "rationale": None}
    ]


@pytest.mark.asyncio
async def test_l2_finalize_idempotent_overwrite(wired):
    """Re-running L2 PO replaces suites.yaml cleanly."""
    _write_discovery(wired["project_path"], _make_discovery(["a", "b"]))
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[_suite(sid="first", capabilities=["a", "b"])],
        author="po-l2",
    )
    # Reactivate the ticket so the second resolve-after-handoff doesn't no-op.
    await wired["tickets"].update(L2_TICKET_ID, status=TicketStatus.IN_PROGRESS)
    await handle_l2_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suites=[
            _suite(sid="onboarding", capabilities=["a"]),
            _suite(sid="catalog", capabilities=["b"]),
        ],
        author="po-l2",
    )
    data = yaml.safe_load(suites_index_path(wired["project_path"]).read_text())
    ids = [s["id"] for s in data["suites"]]
    assert ids == ["onboarding", "catalog"]
    assert "first" not in ids
