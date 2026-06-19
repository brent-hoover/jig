"""TUI screens for the multi-level PO (Track B Final).

Smoke tests for the Discovery / Suites / Ontology screens — each
screen renders read-only on-disk artifacts via spec_loader. Tests use
Textual's ``App.run_test()`` to mount the app and assert on the
visible Static widgets.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.po_l1_mcp import handle_discovery_finalize
from jig.po_ontology_mcp import handle_ontology_add_term
from jig.schemas.po import (
    CapabilityRosterEntry,
    DiscoveryDoc,
    Journey,
    Persona,
)
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType
from jig.tui.app import JigApp
from jig.tui.screens.discovery import DiscoveryScreen
from jig.tui.screens.ontology import OntologyScreen
from jig.tui.screens.suites import SuitesScreen


async def _seed_discovery_and_suites(tmp_path: Path) -> None:
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True, exist_ok=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    await tickets.create(
        Ticket(
            id="discovery",
            work_type=WorkType.BRIEF,
            title="L1 discovery",
            created_by="cli",
        )
    )
    doc = DiscoveryDoc(
        project_name="screen-test",
        personas=[Persona(id="merchant", description="merchant")],
        journeys=[
            Journey(
                id="j-merchant-onboarding",
                persona_id="merchant",
                title="Merchant onboarding",
                narrative="walks OAuth.",
                capability_ids=["shopify-connect"],
            )
        ],
        capability_roster=[
            CapabilityRosterEntry(
                id="shopify-connect",
                description="OAuth connect",
                journey_ids=["j-merchant-onboarding"],
            )
        ],
    )
    await handle_discovery_finalize(
        tickets=tickets,
        threads=threads,
        bus=bus,
        project_path=tmp_path,
        project_name=doc.project_name,
        personas=doc.personas,
        journeys=doc.journeys,
        capability_roster=doc.capability_roster,
        author="po-l1",
    )
    # L2 suites + an L3 brief.
    suites_yaml = {
        "spec_version": 2,
        "suites": [
            {
                "id": "catalog",
                "title": "Catalog",
                "summary": "ingestion",
                "capabilities": ["shopify-connect"],
            }
        ],
        "crosscutting_non_goals": [],
    }
    (spec_dir / "suites.yaml").write_text(yaml.safe_dump(suites_yaml))
    brief = spec_dir / "suites" / "catalog" / "brief.md"
    brief.parent.mkdir(parents=True)
    brief.write_text("# catalog\n")


# ---- discovery ------------------------------------------------------------


@pytest.mark.asyncio
async def test_discovery_screen_renders_post_finalize(tmp_path: Path):
    from textual.widgets import Static

    await _seed_discovery_and_suites(tmp_path)
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        await pilot.press("ctrl+1")
        # Switch to discovery pane.
        from textual.widgets import TabbedContent

        tabs = app.query_one(TabbedContent)
        tabs.active = "discovery-pane"
        await pilot.pause(0.05)
        screen = app.query_one(DiscoveryScreen)
        screen.set_project_path(tmp_path)
        await pilot.pause(0.05)
        header_text = str(app.query_one("#discovery-header", Static).render())
        assert "Discovery" in header_text
        personas_text = str(app.query_one("#discovery-personas", Static).render())
        assert "merchant" in personas_text
        journeys_text = str(app.query_one("#discovery-journeys", Static).render())
        assert "j-merchant-onboarding" in journeys_text


@pytest.mark.asyncio
async def test_discovery_screen_handles_no_state(tmp_path: Path):
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        screen = app.query_one(DiscoveryScreen)
        screen.set_project_path(tmp_path)
        await pilot.pause(0.05)
        header_text = str(app.query_one("#discovery-header", Static).render())
        assert "Discovery" in header_text


# ---- suites ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_suites_screen_lists_suites(tmp_path: Path):
    from textual.widgets import Static, TabbedContent

    await _seed_discovery_and_suites(tmp_path)
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        tabs.active = "suites-pane"
        await pilot.pause(0.05)
        screen = app.query_one(SuitesScreen)
        screen.set_project_path(tmp_path)
        await pilot.pause(0.05)
        header_text = str(app.query_one("#suites-header", Static).render())
        assert "Suites" in header_text
        assert "1 total" in header_text
        list_text = str(app.query_one("#suites-list", Static).render())
        assert "catalog" in list_text
        assert "brief_ready" in list_text


@pytest.mark.asyncio
async def test_suites_screen_handles_no_index(tmp_path: Path):
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        screen = app.query_one(SuitesScreen)
        screen.set_project_path(tmp_path)
        await pilot.pause(0.05)
        header_text = str(app.query_one("#suites-header", Static).render())
        assert "no suites.yaml" in header_text


# ---- ontology -------------------------------------------------------------


@pytest.mark.asyncio
async def test_ontology_screen_lists_terms(tmp_path: Path):
    from textual.widgets import Static, TabbedContent

    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="Something preventing progress.",
        examples=["CI is down"],
    )
    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        tabs.active = "ontology-pane"
        await pilot.pause(0.05)
        screen = app.query_one(OntologyScreen)
        screen.set_project_path(tmp_path)
        await pilot.pause(0.05)
        terms_text = str(app.query_one("#ontology-terms", Static).render())
        assert "blocker" in terms_text
        assert "CI is down" in terms_text


@pytest.mark.asyncio
async def test_ontology_screen_handles_missing_file(tmp_path: Path):
    from textual.widgets import Static

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        screen = app.query_one(OntologyScreen)
        screen.set_project_path(tmp_path)
        await pilot.pause(0.05)
        header_text = str(app.query_one("#ontology-header", Static).render())
        assert "no ontology.md" in header_text


# ---- App composition ------------------------------------------------------


@pytest.mark.asyncio
async def test_app_includes_new_panes(tmp_path: Path):
    """Smoke check that the three new panes are part of the TabbedContent."""
    from textual.widgets import TabbedContent

    app = JigApp(project_path=tmp_path)
    async with app.run_test() as pilot:
        tabs = app.query_one(TabbedContent)
        # Switch to each new pane to verify it exists.
        for pane_id in ("discovery-pane", "suites-pane", "ontology-pane"):
            tabs.active = pane_id
            await pilot.pause(0.02)
            assert tabs.active == pane_id
