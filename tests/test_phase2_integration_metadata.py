"""Phase 2 integration-metadata tests.

Covers:
  - Integration checkpoint fields on Handoff (2.4)
  - ApiConsumption + EventConsumption on Module (2.5)
  - ExposedAPI.kind extension (route/migration/env_var)
  - TicketTouches on Ticket
  - arch_finalize cross-ref validation rejects broken consumptions
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.schemas.arch import (
    ApiConsumption,
    EventConsumption,
    ExposedAPI,
    Module,
)
from jig.thread import Handoff
from jig.ticket import Ticket, TicketTouches, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- Integration checkpoint on Handoff (2.4) -------------------------------


def test_handoff_minimal_still_works():
    h = Handoff(
        ticket_id="arch-01",
        author="sa",
        phase="pm",
    )
    assert h.new_public_surfaces == []
    assert h.changed_assumptions == []
    assert h.required_followups == []
    assert h.unknowns == []


def test_handoff_with_integration_checkpoint():
    h = Handoff(
        ticket_id="arch-01",
        author="sa",
        phase="pm",
        new_public_surfaces=["GET /api/stories", "event:story.fetched"],
        changed_assumptions=["api-client now owns rate-limit state"],
        required_followups=["add consumer test for story.fetched"],
        unknowns=["which story types to include in default filter?"],
    )
    assert len(h.new_public_surfaces) == 2
    assert "GET /api/stories" in h.new_public_surfaces
    assert len(h.unknowns) == 1


def test_handoff_round_trips_through_dict():
    h = Handoff(
        ticket_id="x",
        author="y",
        phase="z",
        new_public_surfaces=["route-a"],
        required_followups=["followup-1"],
    )
    d = h.model_dump()
    h2 = Handoff.model_validate(d)
    assert h2.new_public_surfaces == ["route-a"]
    assert h2.required_followups == ["followup-1"]


# ---- ApiConsumption + EventConsumption on Module (2.5) ----------------------


def _make_module(module_id: str = "api-client", **kwargs) -> Module:
    from jig.intent import Intent, ComplicationsConsidered

    defaults = dict(
        id=module_id,
        title="API Client",
        summary="Wraps the HN Firebase API.",
        intent=Intent(
            problem="Need to fetch HN stories.",
            simplest_solution="One function that calls the API.",
            complications_considered=ComplicationsConsidered(),
        ),
    )
    defaults.update(kwargs)
    return Module(**defaults)


def test_module_consumes_apis_defaults_empty():
    m = _make_module()
    assert m.consumes_apis == []
    assert m.consumes_events == []


def test_module_consumes_apis():
    m = _make_module(
        consumes_apis=[
            ApiConsumption(module="story-store", name="get_story"),
            ApiConsumption(module="story-store", name="list_stories"),
        ],
        consumes_events=[
            EventConsumption(module="story-store", name="story.created"),
        ],
    )
    assert len(m.consumes_apis) == 2
    assert m.consumes_apis[0].name == "get_story"
    assert len(m.consumes_events) == 1
    assert m.consumes_events[0].name == "story.created"


def test_api_consumption_rejects_extra_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ApiConsumption(module="m", name="f", extra_field="bad")  # type: ignore[call-arg]


# ---- ExposedAPI.kind extension (route/migration/env_var) -------------------


def test_exposed_api_accepts_route_kind():
    api = ExposedAPI(name="GET /api/stories", kind="route", summary="Returns stories.")
    assert api.kind == "route"


def test_exposed_api_accepts_migration_kind():
    api = ExposedAPI(name="add_score_index", kind="migration", summary="Adds index.")
    assert api.kind == "migration"


def test_exposed_api_accepts_env_var_kind():
    api = ExposedAPI(name="HN_API_BASE_URL", kind="env_var", summary="Base URL.")
    assert api.kind == "env_var"


def test_exposed_api_rejects_unknown_kind():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        ExposedAPI(name="x", kind="webhook", summary="y")  # type: ignore[arg-type]


# ---- TicketTouches on Ticket -----------------------------------------------


def test_ticket_touches_defaults_empty():
    t = Ticket(
        id="t-01",
        title="Fetch stories",
        work_type=WorkType.FEATURE,
        created_by="pm",
        description=TICKET_AC_PLACEHOLDER,
    )
    assert t.touches.modules == []
    assert t.touches.exposed_apis == []
    assert t.touches.routes == []


def test_ticket_touches_with_declarations():
    t = Ticket(
        id="t-02",
        title="Add route",
        work_type=WorkType.FEATURE,
        created_by="pm",
        touches=TicketTouches(
            modules=["web-frontend"],
            routes=["GET /api/stories"],
            env_vars=["HN_API_BASE_URL"],
        ),
        description=TICKET_AC_PLACEHOLDER,
    )
    assert "web-frontend" in t.touches.modules
    assert "GET /api/stories" in t.touches.routes
    assert "HN_API_BASE_URL" in t.touches.env_vars


def test_ticket_touches_rejects_extra_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TicketTouches(unknown_field="bad")  # type: ignore[call-arg]


# ---- arch_finalize cross-ref validation ------------------------------------


def _write_arch_yaml(project_root: Path, arch_dict: dict) -> None:
    p = project_root / ".jig" / "spec" / "architecture.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(arch_dict, allow_unicode=True))


def _write_contracts_yaml(
    project_root: Path, module_id: str, contracts_dict: dict
) -> None:
    p = project_root / ".jig" / "spec" / "modules" / module_id / "contracts.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.dump(contracts_dict, allow_unicode=True))


def _minimal_intent() -> dict:
    return {
        "problem": "Need to do X.",
        "simplest_solution": "One function that does X.",
        "complications_considered": {},
    }


@pytest.mark.asyncio
async def test_arch_finalize_passes_valid_consumption_refs(tmp_path):
    from unittest.mock import AsyncMock
    from jig.sa_incremental_mcp import handle_arch_finalize

    # consumer module calls an API from provider module
    _write_arch_yaml(
        tmp_path,
        {
            "spec_version": 1,
            "data_stores": [{"id": "main-db", "kind": "postgres"}],
            "modules": [
                {
                    "id": "provider",
                    "title": "Provider",
                    "summary": "Provides an API.",
                    "intent": _minimal_intent(),
                    "n_a_categories": ["behavioral_contracts", "external_dependencies"],
                    "consumes_apis": [],
                    "consumes_events": [],
                },
                {
                    "id": "consumer",
                    "title": "Consumer",
                    "summary": "Consumes provider API.",
                    "intent": _minimal_intent(),
                    "n_a_categories": [
                        "behavioral_contracts",
                        "external_dependencies",
                        "ownership",
                    ],
                    "consumes_apis": [{"module": "provider", "name": "get_data"}],
                    "consumes_events": [],
                },
            ],
        },
    )
    _write_contracts_yaml(
        tmp_path,
        "provider",
        {
            "spec_version": 1,
            "module": "provider",
            "owns": [
                {"collection": "data", "db": "main-db", "write_access": ["provider"]}
            ],
            "integration_ac": [
                {"capability": "provide-data", "must": ["exposes get_data"]}
            ],
            "exposes": [
                {"name": "get_data", "kind": "function", "summary": "Get data."}
            ],
            "emits": [],
        },
    )
    _write_contracts_yaml(
        tmp_path,
        "consumer",
        {
            "spec_version": 1,
            "module": "consumer",
            "owns": [],
            "integration_ac": [
                {"capability": "consume-data", "must": ["calls provider.get_data"]}
            ],
            "exposes": [],
            "emits": [],
        },
    )

    resolved_ticket = Ticket(
        id="architecture",
        title="Architecture",
        work_type=WorkType.FEATURE,
        created_by="sa",
        description=TICKET_AC_PLACEHOLDER,
    )
    mock_tickets = AsyncMock()
    mock_tickets.update = AsyncMock(return_value=resolved_ticket)
    mock_threads = AsyncMock()
    mock_threads.post = AsyncMock(return_value="entry-1")
    mock_bus = AsyncMock()

    await handle_arch_finalize(
        tickets=mock_tickets,
        threads=mock_threads,
        bus=mock_bus,
        project_path=tmp_path,
        summary="All good.",
        author="sa",
    )
    # If we get here without exception, validation passed.


@pytest.mark.asyncio
async def test_arch_finalize_rejects_unresolved_api_consumption(tmp_path):
    from unittest.mock import AsyncMock
    from jig.sa_incremental_mcp import handle_arch_finalize

    _write_arch_yaml(
        tmp_path,
        {
            "spec_version": 1,
            "data_stores": [{"id": "main-db", "kind": "postgres"}],
            "modules": [
                {
                    "id": "provider",
                    "title": "Provider",
                    "summary": "Provides an API.",
                    "intent": _minimal_intent(),
                    "n_a_categories": ["behavioral_contracts", "external_dependencies"],
                },
                {
                    "id": "consumer",
                    "title": "Consumer",
                    "summary": "Consumes provider.",
                    "intent": _minimal_intent(),
                    "n_a_categories": [
                        "behavioral_contracts",
                        "external_dependencies",
                        "ownership",
                    ],
                    "consumes_apis": [{"module": "provider", "name": "nonexistent_fn"}],
                },
            ],
        },
    )
    _write_contracts_yaml(
        tmp_path,
        "provider",
        {
            "spec_version": 1,
            "module": "provider",
            "owns": [
                {"collection": "c", "db": "main-db", "write_access": ["provider"]}
            ],
            "integration_ac": [
                {"capability": "provide-data", "must": ["exposes get_data"]}
            ],
            "exposes": [
                {"name": "get_data", "kind": "function", "summary": "Get data."}
            ],
            "emits": [],
        },
    )
    _write_contracts_yaml(
        tmp_path,
        "consumer",
        {
            "spec_version": 1,
            "module": "consumer",
            "owns": [],
            "integration_ac": [
                {"capability": "consume-data", "must": ["calls provider"]}
            ],
            "exposes": [],
            "emits": [],
        },
    )

    mock_tickets = AsyncMock()
    mock_threads = AsyncMock()
    mock_bus = AsyncMock()
    mock_threads.post = AsyncMock(return_value="entry-1")

    with pytest.raises(ValueError, match="nonexistent_fn"):
        await handle_arch_finalize(
            tickets=mock_tickets,
            threads=mock_threads,
            bus=mock_bus,
            project_path=tmp_path,
            summary="Should fail.",
            author="sa",
        )
