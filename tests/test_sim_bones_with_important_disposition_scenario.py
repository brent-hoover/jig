"""Bones-with-important-disposition scenario end-to-end (mock mode).

Exercises the important→BLOCKED branch of the severity-tier
disposition policy through the synthetic operator. The bones spine
runs to a resolved ticket, then a synthesized important comment
routes through ``apply_severity_disposition`` which posts a
``Handoff(phase="sa-consult")`` on the thread.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jig.sim.driver import Driver
from jig.sim.scenario import load_scenario
from jig.store.threads import ThreadStore
from jig.thread import Handoff


pytestmark = pytest.mark.sim_smoke


SCENARIO_PATH = (
    Path(__file__).parent
    / "scenarios"
    / "bones-with-important-disposition.scenario.yaml"
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
async def test_bones_with_important_disposition_passes_in_mock_mode(
    tmp_path: Path,
) -> None:
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    driver = Driver()
    report = await driver.run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()


@pytest.mark.asyncio
async def test_sa_consult_handoff_posted_on_important(tmp_path: Path) -> None:
    """The disposition step should leave a sa-consult Handoff on the
    thread. Spot-check the JSONL ended up with one such entry."""
    _seed_repo(tmp_path)
    scenario = load_scenario(SCENARIO_PATH)
    report = await Driver().run(scenario, project_root=tmp_path)
    assert report.passed, report.failure_summary()

    threads = ThreadStore(tmp_path / ".jig" / "store" / "comments.jsonl")
    await threads.load()
    entries = await threads.for_ticket("tb-catalog-ingest")
    handoffs = [e for e in entries if isinstance(e, Handoff)]
    sa_consults = [h for h in handoffs if h.phase == "sa-consult"]
    assert sa_consults, (
        f"expected sa-consult Handoff on tb-catalog-ingest, "
        f"got handoff phases: {[h.phase for h in handoffs]}"
    )
