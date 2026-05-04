"""Bones-with-cascade scenario end-to-end (Track C MVP follow-on, mock mode).

Exercises the cascade-after-confirmed-impossible-spike workflow per
``docs/sa-architecture/design.md`` §"Cascade after confirmed-impossible
spike". The scenario completes a spike with ``confirmed_impossible``
which triggers cascade-proposal generation; this test verifies the
artifact landed under ``.jig/arch/cascades/`` and that the architecture
ticket carries the cascade Handoff for operator review.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.schemas.arch import CascadeProposal
from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario
from jig.spec_loader import cascades_dir
from jig.store.threads import ThreadStore
from jig.thread import Handoff


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-cascade.scenario.yaml"
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
async def test_bones_with_cascade_passes_in_mock_mode(tmp_path: Path):
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_bones_with_cascade_writes_cascade_proposal_artifact(tmp_path: Path):
    """``confirmed_impossible`` lands the cascade-proposal YAML on disk."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    cdir = cascades_dir(tmp_path)
    assert cdir.is_dir(), f"cascades dir missing at {cdir}"
    proposals = sorted(cdir.glob("r-shopify-delta-*.yaml"))
    assert len(proposals) == 1, (
        f"expected exactly one cascade proposal, found {[p.name for p in proposals]}"
    )

    raw = yaml.safe_load(proposals[0].read_text())
    proposal = CascadeProposal.model_validate(raw)
    assert proposal.risk_id == "r-shopify-delta"
    assert proposal.spike_ticket_id == "spike-r-shopify-delta"
    # Both dependent contracts captured with starting "still_holds"
    # disposition (operator hand-edits to flip).
    assert len(proposal.contracts) == 2
    for c in proposal.contracts:
        assert c.proposed_disposition == "still_holds"


@pytest.mark.asyncio
async def test_bones_with_cascade_posts_operator_handoff(tmp_path: Path):
    """Architecture ticket carries the operator-cascade-confirm Handoff."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await threads.load()
    entries = await threads.for_ticket("architecture")
    cascade_handoffs = [
        e
        for e in entries
        if isinstance(e, Handoff) and e.phase == "operator-cascade-confirm"
    ]
    assert len(cascade_handoffs) == 1
    # The handoff cites the cascade-proposal artifact path.
    handoff = cascade_handoffs[0]
    assert any(".jig/arch/cascades/" in o for o in handoff.outputs)
    # Summary references the risk id so the operator's view names it.
    assert "r-shopify-delta" in handoff.summary
