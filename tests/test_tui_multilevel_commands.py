"""TUI slash commands for the multi-level PO (Track B Final).

Per ``docs/multi-level-spec/design.md`` §"Workflow integration": the
operator can drive the L1 / L2 / L3 PO from the TUI via slash
commands. Tests cover dispatch parsing + the daemon-side handler
behavior with mocked stores.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from jig.po_l1_mcp import handle_discovery_finalize
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
from jig.tui.commands import get_handler, known_commands
from jig.tui.slash import parse_slash


# ---- registry -------------------------------------------------------------


def test_journey_command_registered():
    assert "journey" in known_commands()
    assert get_handler("journey") is not None


def test_suite_command_registered():
    assert "suite" in known_commands()
    assert get_handler("suite") is not None


def test_spec_command_registered():
    assert "spec" in known_commands()
    assert get_handler("spec") is not None


# ---- parsing --------------------------------------------------------------


def test_parse_journey_add():
    parsed = parse_slash("/journey add merchant")
    assert parsed.name == "journey"
    assert parsed.args == ["add", "merchant"]


def test_parse_suite_init():
    parsed = parse_slash("/suite init catalog")
    assert parsed.name == "suite"
    assert parsed.args == ["init", "catalog"]


def test_parse_spec_capabilities():
    parsed = parse_slash("/spec capabilities --suite catalog")
    assert parsed.name == "spec"
    assert parsed.args == ["capabilities", "--suite", "catalog"]


# ---- /journey list / add fixtures ----------------------------------------


@pytest.fixture
async def wired(tmp_path: Path):
    """Wire up stores + a finalized discovery doc for journey commands."""
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
    }


async def _seed_discovery(wired):
    doc = DiscoveryDoc(
        project_name="multilevel-test",
        personas=[Persona(id="merchant", description="merchant who integrates jig")],
        journeys=[
            Journey(
                id="j-merchant-onboarding",
                persona_id="merchant",
                title="Merchant onboarding",
                narrative="walks through OAuth.",
                capability_ids=["shopify-connect"],
            )
        ],
        capability_roster=[
            CapabilityRosterEntry(
                id="shopify-connect",
                description="Connect via OAuth",
                journey_ids=["j-merchant-onboarding"],
            )
        ],
    )
    # Use the production handler so the structured cache is written.
    await wired["tickets"].create(
        Ticket(
            id="discovery",
            work_type=WorkType.BRIEF,
            title="L1 discovery",
            created_by="cli",
        )
    )
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


# ---- /journey -------------------------------------------------------------


@pytest.mark.asyncio
async def test_journey_list_returns_committed_journeys(wired):
    await _seed_discovery(wired)
    handler = get_handler("journey")
    out = await handler(
        args=["list"],
        orch=None,
        project_path=wired["project_path"],
    )
    assert out["ok"] is True
    journeys = out["data"]["journeys"]
    assert len(journeys) == 1
    assert journeys[0]["id"] == "j-merchant-onboarding"
    assert journeys[0]["status"] == "committed"


@pytest.mark.asyncio
async def test_journey_list_returns_empty_when_no_doc(tmp_path: Path):
    handler = get_handler("journey")
    out = await handler(
        args=["list"],
        orch=None,
        project_path=tmp_path,
    )
    assert out["ok"] is True
    assert out["data"]["journeys"] == []


@pytest.mark.asyncio
async def test_journey_add_stages_new_journey(wired):
    await _seed_discovery(wired)
    handler = get_handler("journey")
    out = await handler(
        args=["add", "merchant"],
        orch=None,
        project_path=wired["project_path"],
    )
    assert out["ok"] is True, out
    assert out["data"]["persona_id"] == "merchant"
    assert out["data"]["journey_id"].startswith("j-merchant-")


@pytest.mark.asyncio
async def test_journey_add_rejects_unknown_persona(wired):
    await _seed_discovery(wired)
    handler = get_handler("journey")
    out = await handler(
        args=["add", "ghost"],
        orch=None,
        project_path=wired["project_path"],
    )
    assert out["ok"] is False
    assert "persona" in out["error"]


@pytest.mark.asyncio
async def test_journey_missing_subcommand(tmp_path: Path):
    handler = get_handler("journey")
    out = await handler(args=[], orch=None, project_path=tmp_path)
    assert out["ok"] is False


# ---- /suite ---------------------------------------------------------------


def _seed_suites_yaml(tmp_path: Path) -> None:
    suites = {
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
    p = tmp_path / ".jig" / "spec" / "suites.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(suites))


@pytest.mark.asyncio
async def test_suite_list_returns_no_index_when_absent(tmp_path: Path):
    handler = get_handler("suite")
    out = await handler(
        args=["list"], orch=None, project_path=tmp_path
    )
    assert out["ok"] is True
    assert out["data"]["status"] == "no-suite-index"


@pytest.mark.asyncio
async def test_suite_list_pending_when_no_brief(tmp_path: Path):
    _seed_suites_yaml(tmp_path)
    handler = get_handler("suite")
    out = await handler(
        args=["list"], orch=None, project_path=tmp_path
    )
    assert out["ok"] is True
    assert len(out["data"]["suites"]) == 1
    assert out["data"]["suites"][0]["status"] == "pending"


@pytest.mark.asyncio
async def test_suite_list_brief_ready_when_brief_on_disk(tmp_path: Path):
    _seed_suites_yaml(tmp_path)
    brief = tmp_path / ".jig" / "spec" / "suites" / "catalog" / "brief.md"
    brief.parent.mkdir(parents=True, exist_ok=True)
    brief.write_text("# brief\n")
    handler = get_handler("suite")
    out = await handler(
        args=["list"], orch=None, project_path=tmp_path
    )
    assert out["data"]["suites"][0]["status"] == "brief_ready"


@pytest.mark.asyncio
async def test_suite_init_creates_ticket(tmp_path: Path):
    _seed_suites_yaml(tmp_path)
    orch = MagicMock()
    orch.tickets = MagicMock()
    orch.tickets.get = AsyncMock(return_value=None)
    orch.tickets.create = AsyncMock()
    orch.tickets.update = AsyncMock()
    handler = get_handler("suite")
    out = await handler(
        args=["init", "catalog"],
        orch=orch,
        project_path=tmp_path,
    )
    assert out["ok"] is True, out
    assert out["data"]["action"] == "created"
    assert out["data"]["ticket_id"] == "suite-catalog"
    orch.tickets.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_suite_refresh_reopens_existing_ticket(tmp_path: Path):
    from jig.ticket import TicketStatus

    _seed_suites_yaml(tmp_path)
    orch = MagicMock()
    orch.tickets = MagicMock()
    existing = MagicMock()
    existing.status = TicketStatus.RESOLVED
    orch.tickets.get = AsyncMock(return_value=existing)
    orch.tickets.update = AsyncMock()
    orch.tickets.create = AsyncMock()
    handler = get_handler("suite")
    out = await handler(
        args=["refresh", "catalog"],
        orch=orch,
        project_path=tmp_path,
    )
    assert out["ok"] is True
    assert out["data"]["action"] == "reopened"
    orch.tickets.update.assert_awaited_once()


@pytest.mark.asyncio
async def test_suite_init_unknown_id_errors(tmp_path: Path):
    _seed_suites_yaml(tmp_path)
    orch = MagicMock()
    orch.tickets = MagicMock()
    handler = get_handler("suite")
    out = await handler(
        args=["init", "ghost"],
        orch=orch,
        project_path=tmp_path,
    )
    assert out["ok"] is False
    assert "ghost" in out["error"]


# ---- /spec capabilities ---------------------------------------------------


@pytest.mark.asyncio
async def test_spec_capabilities_no_suite_index(tmp_path: Path):
    handler = get_handler("spec")
    out = await handler(
        args=["capabilities", "--suite", "catalog"],
        orch=None,
        project_path=tmp_path,
    )
    assert out["ok"] is True
    assert out["data"]["status"] == "no-suite-index"


@pytest.mark.asyncio
async def test_spec_capabilities_no_brief(tmp_path: Path):
    _seed_suites_yaml(tmp_path)
    handler = get_handler("spec")
    out = await handler(
        args=["capabilities", "--suite", "catalog"],
        orch=None,
        project_path=tmp_path,
    )
    assert out["ok"] is True
    assert out["data"]["status"] == "no-brief"
    assert out["data"]["capabilities"] == []


@pytest.mark.asyncio
async def test_spec_capabilities_lists_from_brief(tmp_path: Path):
    _seed_suites_yaml(tmp_path)
    cache = (
        tmp_path / ".jig" / "spec" / "suites" / "catalog" / "spec.structured.yaml"
    )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        yaml.safe_dump(
            {
                "spec_version": 1,
                "name": "catalog",
                "summary": "ingestion",
                "capabilities": [
                    {
                        "id": "shopify-connect",
                        "title": "Connect Shopify",
                        "state": "planned",
                        "summary": "OAuth handshake",
                        "behaviors": [],
                        "acceptance_criteria": [],
                        "excluded": [],
                        "open_questions": [],
                        "tickets": [],
                        "aliases": [],
                    }
                ],
                "non_goals": [],
            }
        )
    )
    handler = get_handler("spec")
    out = await handler(
        args=["capabilities", "--suite", "catalog"],
        orch=None,
        project_path=tmp_path,
    )
    assert out["ok"] is True, out
    assert out["data"]["status"] == "ok"
    assert out["data"]["capabilities"][0]["id"] == "shopify-connect"


@pytest.mark.asyncio
async def test_spec_capabilities_requires_suite_flag(tmp_path: Path):
    handler = get_handler("spec")
    out = await handler(
        args=["capabilities"],
        orch=None,
        project_path=tmp_path,
    )
    assert out["ok"] is False
    assert "--suite" in out["error"]


# ---- /init --proceed ------------------------------------------------------


@pytest.mark.asyncio
async def test_init_proceed_requires_l0(tmp_path: Path):
    handler = get_handler("init")
    out = await handler(
        args=["--proceed"],
        orch=MagicMock(),
        project_path=tmp_path,
        prompt_registry=None,
        emitter=None,
    )
    assert out["ok"] is False
    assert "L0" in out["error"]


@pytest.mark.asyncio
async def test_init_proceed_advances_to_l1_when_l0_present(tmp_path: Path):
    # Seed an L0 project.md so the L0 gate passes.
    p = tmp_path / ".jig" / "spec" / "project.md"
    p.parent.mkdir(parents=True)
    p.write_text("# project\n")
    orch = MagicMock()
    orch.tickets = MagicMock()
    orch.tickets.get = AsyncMock(return_value=None)
    orch.tickets.create = AsyncMock()
    orch.tickets.update = AsyncMock()

    handler = get_handler("init")
    out = await handler(
        args=["--proceed"],
        orch=orch,
        project_path=tmp_path,
        prompt_registry=None,
        emitter=None,
    )
    assert out["ok"] is True
    assert out["data"]["level"] == "po-l1"
    assert out["data"]["ticket_id"] == "discovery"
    orch.tickets.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_proceed_advances_to_l2_when_l1_present(tmp_path: Path):
    p = tmp_path / ".jig" / "spec"
    p.mkdir(parents=True)
    (p / "project.md").write_text("# project\n")
    (p / "discovery.md").write_text("# discovery\n")
    orch = MagicMock()
    orch.tickets = MagicMock()
    orch.tickets.get = AsyncMock(return_value=None)
    orch.tickets.create = AsyncMock()
    orch.tickets.update = AsyncMock()

    handler = get_handler("init")
    out = await handler(
        args=["--proceed"],
        orch=orch,
        project_path=tmp_path,
        prompt_registry=None,
        emitter=None,
    )
    assert out["ok"] is True
    assert out["data"]["level"] == "po-l2"
