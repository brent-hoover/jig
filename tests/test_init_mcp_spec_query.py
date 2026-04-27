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
