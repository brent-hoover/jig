"""L0 PO MCP tool handlers + markdown renderer (Track B1, bones)."""

from __future__ import annotations

import pytest
import yaml

from jig.po_l0_mcp import (
    handle_l0_finalize,
    render_project_md,
)
from jig.schemas.po import ProductNonGoal, Project
from jig.store.bus import MessageBus
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, TicketStatus, WorkType


@pytest.fixture
async def wired(tmp_path):
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, bus):
        await s.load()
    project_ticket = Ticket(
        id="project",
        work_type=WorkType.BRIEF,
        title="L0 project pitch",
        created_by="cli",
    )
    await tickets.create(project_ticket)
    return {
        "tickets": tickets,
        "threads": threads,
        "bus": bus,
        "project_path": tmp_path,
        "spec_dir": spec_dir,
    }


# ---------- renderer ---------------------------------------------------------


def test_render_project_md_minimum():
    p = Project(
        name="jig-search",
        pitch="hosted search SaaS for ecommerce",
        problem="merchants run blind without good search",
        audience="ecommerce ops staff at SMBs",
    )
    md = render_project_md(p)
    assert md.startswith("# jig-search\n")
    assert "hosted search SaaS for ecommerce" in md
    assert "## Problem" in md
    assert "merchants run blind without good search" in md
    assert "## Audience" in md
    assert "ecommerce ops staff at SMBs" in md
    # Empty non-goals: heading still present so operator can see the
    # section is intentionally empty rather than missing.
    assert "## Non-goals (product-level)" in md
    # Trailing newline so cat / editors don't complain.
    assert md.endswith("\n")


def test_render_project_md_with_non_goals():
    p = Project(
        name="jig-search",
        pitch="x",
        problem="y",
        audience="z",
        non_goals=[
            ProductNonGoal(
                id="no-cms",
                text="We will not build a CMS",
                rationale="out of scope for v2",
            ),
            ProductNonGoal(id="no-recs", text="No recommendation engine"),
        ],
    )
    md = render_project_md(p)
    # Format per design.md §"L0 — Pitch":
    #   - {#no-cms} We will not build a CMS — out of scope for v2
    assert "- {#no-cms} We will not build a CMS — out of scope for v2" in md
    # Without rationale: no em-dash trailer.
    assert "- {#no-recs} No recommendation engine\n" in md
    assert "out of scope" not in md.split("- {#no-recs}")[1].split("\n")[0]


def test_render_project_md_section_order():
    p = Project(
        name="x",
        pitch="p",
        problem="pp",
        audience="aa",
        non_goals=[ProductNonGoal(id="ng-1", text="something")],
    )
    md = render_project_md(p)
    # Pitch comes first (before any H2), then Problem, then Audience,
    # then Non-goals — order matters for the design contract.
    pitch_idx = md.index("\np\n")
    problem_idx = md.index("## Problem")
    audience_idx = md.index("## Audience")
    nongoals_idx = md.index("## Non-goals (product-level)")
    assert pitch_idx < problem_idx < audience_idx < nongoals_idx


# ---------- l0_finalize ------------------------------------------------------


@pytest.mark.asyncio
async def test_l0_finalize_writes_project_md(wired):
    await handle_l0_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        name="jig-search",
        pitch="hosted search SaaS for ecommerce",
        problem="merchants run blind",
        audience="ecommerce ops staff",
        non_goals=[],
        author="po-l0",
    )
    md_path = wired["project_path"] / "docs" / "brief.md"
    assert md_path.is_file()
    body = md_path.read_text()
    assert "# jig-search" in body
    assert "hosted search SaaS for ecommerce" in body
    assert "## Problem" in body


@pytest.mark.asyncio
async def test_l0_finalize_writes_structured_yaml(wired):
    await handle_l0_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        name="jig-search",
        pitch="x",
        problem="y",
        audience="z",
        non_goals=[
            {"id": "no-cms", "text": "No CMS", "rationale": "out of scope"},
        ],
        author="po-l0",
    )
    yaml_path = wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
    assert yaml_path.is_file()
    data = yaml.safe_load(yaml_path.read_text())
    assert data["spec_version"] == 2
    assert data["name"] == "jig-search"
    assert data["pitch"] == "x"
    assert data["problem"] == "y"
    assert data["audience"] == "z"
    assert data["non_goals"][0]["id"] == "no-cms"
    assert data["non_goals"][0]["rationale"] == "out of scope"
    # Round-trip back through the schema to confirm the dump is valid.
    Project.model_validate(data)


@pytest.mark.asyncio
async def test_l0_finalize_resolves_ticket(wired):
    await handle_l0_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        name="x",
        pitch="x",
        problem="x",
        audience="x",
        non_goals=[],
        author="po-l0",
    )
    t = await wired["tickets"].get("project")
    assert t is not None
    assert t.status == TicketStatus.RESOLVED


@pytest.mark.asyncio
async def test_l0_finalize_emits_handoff(wired):
    await handle_l0_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        name="x",
        pitch="x",
        problem="x",
        audience="x",
        non_goals=[],
        author="po-l0",
    )
    entries = await wired["threads"].for_ticket("project")
    handoffs = [e for e in entries if e.kind == "handoff"]
    assert len(handoffs) == 1
    assert handoffs[0].phase == "po-l1"
    assert "docs/brief.md" in handoffs[0].outputs
    assert ".jig/spec/project.structured.yaml" in handoffs[0].outputs


@pytest.mark.asyncio
async def test_l0_finalize_rejects_blank_fields(wired):
    with pytest.raises(ValueError):
        await handle_l0_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            name="x",
            pitch="",
            problem="x",
            audience="x",
            non_goals=[],
            author="po-l0",
        )


@pytest.mark.asyncio
async def test_l0_finalize_rejects_bad_non_goal(wired):
    with pytest.raises(ValueError):
        await handle_l0_finalize(
            tickets=wired["tickets"],
            threads=wired["threads"],
            bus=wired["bus"],
            project_path=wired["project_path"],
            name="x",
            pitch="x",
            problem="x",
            audience="x",
            non_goals=[{"id": "", "text": "missing id"}],
            author="po-l0",
        )


@pytest.mark.asyncio
async def test_l0_finalize_idempotent_overwrite(wired):
    """Calling finalize twice replaces both artifacts cleanly.

    Bones use case: an operator may re-run the L0 PO after deciding the
    pitch needs revision. The artifacts must reflect the latest call,
    not be appended.
    """
    await handle_l0_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        name="first",
        pitch="first pitch",
        problem="first problem",
        audience="first audience",
        non_goals=[],
        author="po-l0",
    )
    # Reactivate the ticket so a second call doesn't no-op the resolve.
    await wired["tickets"].update("project", status=TicketStatus.IN_PROGRESS)
    await handle_l0_finalize(
        tickets=wired["tickets"],
        threads=wired["threads"],
        bus=wired["bus"],
        project_path=wired["project_path"],
        name="second",
        pitch="second pitch",
        problem="second problem",
        audience="second audience",
        non_goals=[],
        author="po-l0",
    )
    md = (wired["project_path"] / "docs" / "brief.md").read_text()
    assert "second" in md
    assert "first pitch" not in md
    data = yaml.safe_load(
        (
            wired["project_path"] / ".jig" / "spec" / "project.structured.yaml"
        ).read_text()
    )
    assert data["name"] == "second"
