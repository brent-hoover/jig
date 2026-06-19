"""Bones-with-scope-creeper scenario (Track H Final).

Verifies the spine resolves cleanly with the scope-creeper persona's
"extras stay deferred" payload AND that the L3 PO's allowlist
validator rejects unauthorized capability additions (the persona's
attempt to push scope past the L2 allowlist).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.po_l3_mcp import handle_l3_finalize
from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario
from jig.spec_loader import suites_index_path
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent / "scenarios" / "bones-with-scope-creeper.scenario.yaml"
)


def _seed_repo(root: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True
    )
    for k, v in (
        ("user.email", "test@example.com"),
        ("user.name", "Test"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", k, v], cwd=root, check=True, capture_output=True
        )
    (root / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "seed"], cwd=root, check=True, capture_output=True
    )


@pytest.mark.asyncio
async def test_scope_creeper_scenario_passes(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_l3_allowlist_rejects_scope_creeper_extra(tmp_path: Path):
    """The L3 PO's allowlist validator rejects capability ids beyond L2."""
    # Set up the minimum store + suites.yaml the L3 handler needs.
    store_dir = tmp_path / ".jig" / "store"
    spec_dir = tmp_path / ".jig" / "spec"
    store_dir.mkdir(parents=True)
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(store_dir / "tickets.jsonl")
    threads = ThreadStore(store_dir / "comments.jsonl")
    bus = MessageBus(store_dir / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    await tickets.create(
        Ticket(
            id="suite-catalog",
            work_type=WorkType.BRIEF,
            title="L3 brief — catalog",
            created_by="test",
        )
    )

    # Suite allowlist contains only shopify-connect.
    suites_index_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    suites_index_path(tmp_path).write_text(
        yaml.safe_dump(
            {
                "spec_version": 2,
                "suites": [
                    {
                        "id": "catalog",
                        "title": "Catalog",
                        "summary": "ingest",
                        "capabilities": ["shopify-connect"],
                    }
                ],
                "crosscutting_non_goals": [],
            }
        )
    )

    # The scope-creeper persona attempts to add ``email-notifications`` —
    # not in the L2 allowlist. The validator must reject.
    with pytest.raises(ValueError, match="not listed"):
        await handle_l3_finalize(
            tickets=tickets,
            threads=threads,
            bus=bus,
            project_path=tmp_path,
            suite_id="catalog",
            intro="catalog ingest",
            capabilities=[
                {
                    "id": "shopify-connect",
                    "title": "Connect Shopify",
                    "state": "planned",
                    "summary": "OAuth + pull",
                    "behaviors": [
                        {
                            "id": "pull-catalog",
                            "description": "After OAuth completes we pull",
                            "acceptance_criteria": ["Catalog rows appear in products"],
                        }
                    ],
                },
                {
                    "id": "email-notifications",
                    "title": "Email notifications",
                    "state": "planned",
                    "summary": "scope creep!",
                    "behaviors": [
                        {
                            "id": "send-emails",
                            "description": "send notification emails",
                            "acceptance_criteria": ["emails sent"],
                        }
                    ],
                },
            ],
            non_goals=[],
            author="po-l3",
        )
