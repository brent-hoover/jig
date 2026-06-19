"""L3 PO MCP tool handlers + suite-brief renderer (Track B5, bones)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from jig.po_l3_mcp import (
    handle_l3_finalize,
    render_suite_brief_md,
    validate_capability_allowlist,
)
from jig.schemas.po import Suite, SuitesIndex
from jig.spec_loader import (
    suite_brief_path,
    suite_structured_path,
)
from jig.spec_schema import (
    Behavior,
    Capability,
    CapabilityState,
    NonGoal,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


# ---- fixtures -------------------------------------------------------------


def _suites_yaml(suite_id: str = "catalog", capabilities=None) -> str:
    """Render a minimal suites.yaml with one suite + its capability allowlist.

    Default mirrors the design.md §"L2 — Suite organization" example so
    the bones tests look like the canonical scenario.
    """
    if capabilities is None:
        capabilities = ["shopify-connect"]
    return yaml.safe_dump(
        {
            "spec_version": 2,
            "suites": [
                {
                    "id": suite_id,
                    "title": suite_id.title(),
                    "summary": "ingestion, normalization, delta updates",
                    "capabilities": capabilities,
                }
            ],
        },
        sort_keys=False,
    )


@pytest.fixture
async def wired(tmp_path: Path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    # Hand-written suites.yaml — for bones, the synthetic operator
    # populates this directly because L2 authoring is a later track.
    (spec_dir / "suites.yaml").write_text(_suites_yaml())
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    suite_ticket = Ticket(
        id="suite-catalog",
        work_type=WorkType.BRIEF,
        title="L3 brief — catalog suite",
        created_by="cli",
    )
    await tickets.create(suite_ticket)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
        "spec_dir": spec_dir,
    }


def _capability(
    *,
    cap_id: str = "shopify-connect",
    title: str = "Connect Shopify store via OAuth",
) -> dict:
    """Build a minimal valid capability dict — one behavior, one AC."""
    return {
        "id": cap_id,
        "title": title,
        "state": "planned",
        "summary": "Merchant authorizes their Shopify store; we pull the catalog.",
        "behaviors": [
            {
                "id": "begin-oauth",
                "description": "Start the OAuth flow from the merchant's account page",
                "acceptance_criteria": [
                    "Clicking 'Connect Shopify' redirects to Shopify's OAuth consent screen",
                ],
            }
        ],
    }


# ---- renderer -------------------------------------------------------------


def test_render_suite_brief_minimum():
    suite = Suite(
        id="catalog",
        title="Catalog",
        summary="ingestion, normalization, delta updates",
        capabilities=["shopify-connect"],
    )
    cap = Capability(
        id="shopify-connect",
        title="Connect Shopify store via OAuth",
        state=CapabilityState.PLANNED,
        summary="Merchant authorizes their store.",
        behaviors=[
            Behavior(
                id="begin-oauth",
                description="Start the OAuth flow",
                acceptance_criteria=["Clicking redirects to Shopify"],
            )
        ],
        created_at=datetime.now(timezone.utc),
        last_updated=datetime.now(timezone.utc),
        state_changed_at=datetime.now(timezone.utc),
    )
    md = render_suite_brief_md(
        suite=suite,
        intro="Catalog onboarding flows live in this suite.",
        capabilities=[cap],
        non_goals=[],
    )
    # Top-level title is the suite, not the project — L3 is suite-scoped.
    assert md.startswith("# Catalog\n")
    assert "Catalog onboarding flows live in this suite." in md
    # Standard v1-shaped section headings.
    assert "## Built" in md
    assert "## Planned (committed)" in md
    assert "## Non-goals (suite-level)" in md
    # The capability lands in Planned (committed) per bones bucketing.
    assert "### Connect Shopify store via OAuth {#shopify-connect}" in md
    # Behaviors render with {#id} anchors.
    assert "- {#begin-oauth} Start the OAuth flow" in md
    # AC bullets reference the behavior with [id].
    assert "- [begin-oauth] Clicking redirects to Shopify" in md


def test_render_suite_brief_with_non_goals():
    suite = Suite(id="catalog", title="Catalog", summary="x", capabilities=[])
    md = render_suite_brief_md(
        suite=suite,
        intro="x",
        capabilities=[],
        non_goals=[
            NonGoal(
                id="no-bulk-edit", text="No in-app bulk edit", rationale="defer to v2"
            ),
            NonGoal(id="no-csv", text="No CSV export"),
        ],
    )
    # Per-suite non-goal section uses suite-level scope so it doesn't
    # collide with crosscutting non_goals at the project level.
    assert "## Non-goals (suite-level)" in md
    assert "- {#no-bulk-edit} No in-app bulk edit — defer to v2" in md
    # Without rationale: no em-dash trailer.
    assert "- {#no-csv} No CSV export\n" in md


# ---- allowlist validator --------------------------------------------------


def _make_cap(cap_id: str) -> Capability:
    now = datetime.now(timezone.utc)
    return Capability(
        id=cap_id,
        title=cap_id.title(),
        state=CapabilityState.PLANNED,
        summary="x",
        acceptance_criteria=["x is checkable"],
        created_at=now,
        last_updated=now,
        state_changed_at=now,
    )


def test_validate_allowlist_accepts_listed_caps():
    suite = Suite(id="s", title="S", summary="s", capabilities=["a", "b"])
    # Subset of the allowlist is fine — L3 doesn't require every
    # capability to be elaborated in one session.
    validate_capability_allowlist(
        suite=suite,
        capabilities=[_make_cap("a")],
    )


def test_validate_allowlist_rejects_unlisted_cap():
    suite = Suite(id="s", title="S", summary="s", capabilities=["a"])
    with pytest.raises(ValueError, match="not listed in suite"):
        validate_capability_allowlist(
            suite=suite,
            capabilities=[_make_cap("a"), _make_cap("rogue")],
        )


def test_validate_allowlist_empty_caps_is_fine():
    """An L3 PO that produces no capabilities is degenerate but legal —
    nothing in scope to validate."""
    suite = Suite(id="s", title="S", summary="s", capabilities=["a"])
    validate_capability_allowlist(suite=suite, capabilities=[])


# ---- l3_finalize ----------------------------------------------------------


@pytest.mark.asyncio
async def test_l3_finalize_writes_brief_md(wired):
    await handle_l3_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suite_id="catalog",
        intro="Catalog handles ingestion + onboarding.",
        capabilities=[_capability()],
        author="po-l3",
    )
    md_path = suite_brief_path(wired["project_path"], "catalog")
    assert md_path.is_file()
    body = md_path.read_text()
    assert "# Catalog" in body
    assert "Catalog handles ingestion + onboarding." in body
    assert "### Connect Shopify store via OAuth {#shopify-connect}" in body
    assert "- {#begin-oauth} Start the OAuth flow" in body


@pytest.mark.asyncio
async def test_l3_finalize_writes_structured_yaml(wired):
    await handle_l3_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suite_id="catalog",
        intro="x",
        capabilities=[_capability()],
        author="po-l3",
    )
    yaml_path = suite_structured_path(wired["project_path"], "catalog")
    assert yaml_path.is_file()
    data = yaml.safe_load(yaml_path.read_text())
    # The structured spec name reflects the suite, not the project —
    # it's a suite-scoped projection.
    assert data["name"] == "Catalog"
    assert data["summary"] == "ingestion, normalization, delta updates"
    assert len(data["capabilities"]) == 1
    cap = data["capabilities"][0]
    assert cap["id"] == "shopify-connect"
    assert cap["state"] == "planned"
    assert cap["behaviors"][0]["id"] == "begin-oauth"
    # AC must be present for an elaborated state — covered by schema.
    assert cap["behaviors"][0]["acceptance_criteria"]


@pytest.mark.asyncio
async def test_l3_finalize_resolves_ticket(wired):
    await handle_l3_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suite_id="catalog",
        intro="x",
        capabilities=[_capability()],
        author="po-l3",
    )
    t = await wired["tickets"].get("suite-catalog")
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_l3_finalize_emits_handoff_to_sa(wired):
    await handle_l3_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suite_id="catalog",
        intro="x",
        capabilities=[_capability()],
        author="po-l3",
    )
    entries = await wired["threads"].for_ticket("suite-catalog")
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    h = handoffs[0]
    # Per design.md §"L3 — Suite brief", L3 hands off to SA next.
    assert h.phase == "sa"
    assert ".jig/spec/suites/catalog/brief.md" in h.outputs
    assert ".jig/spec/suites/catalog/spec.structured.yaml" in h.outputs


@pytest.mark.asyncio
async def test_l3_finalize_rejects_capability_outside_allowlist(wired):
    """Bones gap-handling: refuse rather than loop back to L1.

    Per design.md, adding a capability not in suites.yaml is a gap that
    must go back to L1 first. For bones, the L3 PO simply refuses; the
    operator must edit suites.yaml manually before retrying.
    """
    bad = _capability(cap_id="rogue-cap", title="Not in allowlist")
    with pytest.raises(ValueError, match="not listed in suite"):
        await handle_l3_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suite_id="catalog",
            intro="x",
            capabilities=[_capability(), bad],
            author="po-l3",
        )
    # Brief and structured spec must NOT have been written when the
    # validator rejects — atomic-ish behavior at the handler level.
    assert not suite_brief_path(wired["project_path"], "catalog").exists()
    assert not suite_structured_path(wired["project_path"], "catalog").exists()


@pytest.mark.asyncio
async def test_l3_finalize_rejects_unknown_suite_id(wired):
    with pytest.raises(ValueError, match="not found in suites.yaml"):
        await handle_l3_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suite_id="ghost-suite",
            intro="x",
            capabilities=[],
            author="po-l3",
        )


@pytest.mark.asyncio
async def test_l3_finalize_rejects_blank_intro(wired):
    with pytest.raises(ValueError, match="intro must not be empty"):
        await handle_l3_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suite_id="catalog",
            intro="   ",
            capabilities=[_capability()],
            author="po-l3",
        )


@pytest.mark.asyncio
async def test_l3_finalize_rejects_capability_missing_ac(wired):
    """Schema-level: planned capabilities require at least one AC.

    The PO can't slip an elaborated-state capability through without
    AC — the StructuredSpec validator catches it.
    """
    bad_cap = {
        "id": "shopify-connect",
        "title": "x",
        "state": "planned",
        "summary": "x",
        # No behaviors and no capability-level AC.
    }
    with pytest.raises(ValueError):
        await handle_l3_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            suite_id="catalog",
            intro="x",
            capabilities=[bad_cap],
            author="po-l3",
        )


@pytest.mark.asyncio
async def test_l3_finalize_idempotent_overwrite(wired):
    """Re-running L3 PO replaces both per-suite artifacts cleanly.

    Same bones case as L0: the operator may revise the brief and
    re-finalize. The artifacts must reflect the latest call.
    """
    await handle_l3_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suite_id="catalog",
        intro="first intro",
        capabilities=[_capability()],
        author="po-l3",
    )
    # Reactivate the ticket so the second call's resolve-after-handoff
    # doesn't no-op.
    await wired["tickets"].update(
        "suite-catalog",
        status=TicketStatus.IN_PROGRESS,
    )
    second_cap = _capability()
    second_cap["summary"] = "Second pass — refined wording."
    await handle_l3_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        suite_id="catalog",
        intro="second intro",
        capabilities=[second_cap],
        author="po-l3",
    )
    md = suite_brief_path(wired["project_path"], "catalog").read_text()
    assert "second intro" in md
    assert "first intro" not in md
    assert "Second pass" in md
    data = yaml.safe_load(
        suite_structured_path(wired["project_path"], "catalog").read_text()
    )
    assert data["capabilities"][0]["summary"] == "Second pass — refined wording."


@pytest.mark.asyncio
async def test_l3_finalize_missing_suites_yaml_raises(tmp_path: Path):
    """No suites.yaml → can't validate the allowlist → fail loudly.

    Bones expects the synthetic operator (or the L2 PO at MVP) to have
    written suites.yaml first. Silent default to "no allowlist" would
    let any capability through.
    """
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    with pytest.raises(FileNotFoundError):
        await handle_l3_finalize(
            tickets=tickets,
            threads=threads,
            bus=bus,
            project_path=tmp_path,
            suite_id="catalog",
            intro="x",
            capabilities=[],
            author="po-l3",
        )


# ---- SuitesIndex schema ---------------------------------------------------


def test_suites_index_round_trip():
    """Sanity: the L2 schema parses + dumps the canonical example."""
    raw = {
        "spec_version": 2,
        "suites": [
            {
                "id": "catalog",
                "title": "Catalog",
                "summary": "ingestion, normalization, delta updates",
                "capabilities": ["shopify-connect", "csv-upload"],
            }
        ],
        "crosscutting_non_goals": [
            {"id": "no-cms", "text": "No CMS"},
        ],
    }
    idx = SuitesIndex.model_validate(raw)
    assert idx.spec_version == 2
    assert len(idx.suites) == 1
    cat = idx.suite_by_id("catalog")
    assert cat is not None
    assert cat.capabilities == ["shopify-connect", "csv-upload"]
    assert idx.suite_by_id("missing") is None
    # Dump round-trips through the schema.
    SuitesIndex.model_validate(idx.model_dump(mode="json"))


def test_suites_index_load_helper(tmp_path: Path):
    """Convenience: load_suites_index reads + parses the on-disk file."""
    from jig.spec_loader import load_suites_index

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "suites.yaml").write_text(_suites_yaml())
    idx = load_suites_index(tmp_path)
    assert idx.suite_by_id("catalog") is not None


def test_suites_index_path_helper(tmp_path: Path):
    """Path helper points at the v2 layout location."""
    from jig.spec_loader import suites_index_path

    p = suites_index_path(tmp_path)
    assert p == tmp_path / ".jig" / "spec" / "suites.yaml"
