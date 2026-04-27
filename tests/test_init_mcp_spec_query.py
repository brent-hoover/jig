from datetime import datetime, timezone

import pytest
import yaml

from jig.init_mcp import (
    handle_spec_list_capabilities,
    handle_spec_get_capability,
    handle_spec_get_behavior,
    handle_spec_list_non_goals,
    handle_spec_get_non_goal,
    handle_spec_resolve_uri,
    handle_spec_load_existing,
)
from jig.spec_schema import (
    Behavior, Capability, CapabilityState, NonGoal, StructuredSpec,
)


def _ts():
    return datetime(2026, 4, 27, tzinfo=timezone.utc)


@pytest.fixture
def spec_path(tmp_path):
    spec = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="due-dates", title="Due dates",
                state=CapabilityState.PLANNED,
                behaviors=[Behavior(id="b1", description="x",
                                    acceptance_criteria=["a"])],
                created_at=_ts(), last_updated=_ts(), state_changed_at=_ts(),
            ),
            Capability(
                id="priorities", title="Priorities",
                state=CapabilityState.BACKLOG,
                created_at=_ts(), last_updated=_ts(), state_changed_at=_ts(),
            ),
        ],
        non_goals=[NonGoal(id="no-multi-user", text="Multi-user")],
        generated_at=_ts(),
    )
    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.structured.yaml").write_text(
        yaml.safe_dump(spec.model_dump(mode="json", by_alias=True))
    )
    return tmp_path


@pytest.mark.asyncio
async def test_spec_list_capabilities_returns_summary(spec_path):
    out = await handle_spec_list_capabilities(
        project_path=spec_path, state=None,
    )
    assert {c["id"] for c in out} == {"due-dates", "priorities"}
    assert all({"id", "title", "state"} <= c.keys() for c in out)


@pytest.mark.asyncio
async def test_spec_list_capabilities_filters_by_state(spec_path):
    out = await handle_spec_list_capabilities(
        project_path=spec_path, state="planned",
    )
    assert [c["id"] for c in out] == ["due-dates"]


@pytest.mark.asyncio
async def test_spec_get_capability_returns_full_object(spec_path):
    out = await handle_spec_get_capability(
        project_path=spec_path, id="due-dates",
    )
    assert out["id"] == "due-dates"
    assert len(out["behaviors"]) == 1


@pytest.mark.asyncio
async def test_spec_get_capability_raises_on_unknown(spec_path):
    with pytest.raises(KeyError):
        await handle_spec_get_capability(project_path=spec_path, id="nope")


@pytest.mark.asyncio
async def test_spec_get_behavior(spec_path):
    out = await handle_spec_get_behavior(
        project_path=spec_path, capability_id="due-dates", behavior_id="b1",
    )
    assert out["id"] == "b1"


@pytest.mark.asyncio
async def test_spec_list_non_goals(spec_path):
    out = await handle_spec_list_non_goals(project_path=spec_path)
    assert [n["id"] for n in out] == ["no-multi-user"]


@pytest.mark.asyncio
async def test_spec_get_non_goal(spec_path):
    out = await handle_spec_get_non_goal(
        project_path=spec_path, id="no-multi-user",
    )
    assert out["text"] == "Multi-user"


@pytest.mark.asyncio
async def test_spec_resolve_uri_capability(spec_path):
    out = await handle_spec_resolve_uri(
        project_path=spec_path,
        uri="project://spec/capabilities/due-dates",
    )
    assert out["kind"] == "capability"


@pytest.mark.asyncio
async def test_spec_load_existing_returns_dict(spec_path):
    out = await handle_spec_load_existing(project_path=spec_path)
    assert out["name"] == "x"
    assert len(out["capabilities"]) == 2


@pytest.mark.asyncio
async def test_spec_load_existing_empty_when_no_spec(tmp_path):
    out = await handle_spec_load_existing(project_path=tmp_path)
    assert out == {}


@pytest.mark.asyncio
async def test_spec_generate_from_brief_first_time(tmp_path):
    """First-time generation: no existing spec, brief produces a fresh
    StructuredSpec dict."""
    from jig.init_mcp import handle_spec_generate_from_brief
    from jig.store.tickets import TicketStore

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text(
        "# todoapp\n\nA simple todo manager.\n\n"
        "## Backlog\n\n- {#mobile-app} Mobile app\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()

    result = await handle_spec_generate_from_brief(
        project_path=tmp_path, tickets=tickets,
    )
    assert result["gaps"] == []
    spec = result["spec"]
    assert spec is not None
    assert spec["name"] == "todoapp"
    assert len(spec["capabilities"]) == 1
    assert spec["capabilities"][0]["id"] == "mobile-app"


@pytest.mark.asyncio
async def test_spec_generate_from_brief_returns_format_gaps(tmp_path):
    """Brief with a format violation surfaces as blocking gaps."""
    from jig.init_mcp import handle_spec_generate_from_brief
    from jig.store.tickets import TicketStore

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    (spec_dir / "project.md").write_text(
        "# x\n\nintro\n\n## Backlog\n\n- Mobile app\n"  # missing {#id}
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()

    result = await handle_spec_generate_from_brief(
        project_path=tmp_path, tickets=tickets,
    )
    assert result["spec"] is None
    assert len(result["gaps"]) >= 1
    assert any(g["kind"] == "format_error" for g in result["gaps"])


@pytest.mark.asyncio
async def test_spec_generate_from_brief_preserves_metadata_on_regen(tmp_path):
    """Existing spec's created_at survives regen."""
    from datetime import datetime, timezone
    from jig.init_mcp import handle_spec_generate_from_brief
    from jig.store.tickets import TicketStore
    from jig.spec_schema import (
        Capability, CapabilityState, StructuredSpec,
    )
    import yaml

    spec_dir = tmp_path / ".jig" / "spec"
    spec_dir.mkdir(parents=True)
    earlier = datetime(2026, 1, 1, tzinfo=timezone.utc)
    existing = StructuredSpec(
        name="x", summary="y",
        capabilities=[
            Capability(
                id="mobile-app", title="Mobile app",
                state=CapabilityState.BACKLOG,
                created_at=earlier, last_updated=earlier,
                state_changed_at=earlier,
            ),
        ],
        generated_at=earlier,
    )
    (spec_dir / "project.structured.yaml").write_text(
        yaml.safe_dump(existing.model_dump(mode="json", by_alias=True))
    )
    (spec_dir / "project.md").write_text(
        "# x\n\nintro\n\n## Backlog\n\n- {#mobile-app} Mobile app\n"
    )
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()

    result = await handle_spec_generate_from_brief(
        project_path=tmp_path, tickets=tickets,
    )
    spec = result["spec"]
    assert spec is not None
    # Accept both +00:00 and Z suffix — YAML serialization normalises to Z.
    created_at = spec["capabilities"][0]["created_at"]
    assert created_at.replace("+00:00", "Z") == earlier.isoformat().replace("+00:00", "Z")
