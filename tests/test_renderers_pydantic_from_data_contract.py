"""Pydantic-from-data-contract renderer (Track I MVP).

Per ``docs/v2.0/agent-leverage/problem.md`` §6: one source of truth (the
DataContract) renders into N derived views; this is the first
renderer (Pydantic class). MVP scope: one renderer, deterministic
template substitution. The other formats (OpenAPI, SQL DDL, etc.)
are deferred per the v2 sequencing table.
"""
from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import yaml
from click.testing import CliRunner

from jig.cli import cli
from jig.intent import ComplicationsConsidered, Intent
from jig.renderers.pydantic_from_data_contract import (
    render_pydantic_from_data_contract,
)
from jig.schemas.arch import ContractsFile, DataContract
from jig.spec_loader import module_contracts_path


# ---- helpers -------------------------------------------------------------


def _intent() -> Intent:
    return Intent(
        problem="Need a typed product row for catalog ingestion.",
        simplest_solution="Hand-write a Pydantic model with str sku and int qty.",
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="none — N/A",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )


def _contract(
    *,
    contract_id: str = "product-row",
    fields: dict[str, str] | None = None,
    schema_ref: str | None = "project://arch/contracts/shared/product-row",
) -> DataContract:
    return DataContract(
        id=contract_id,
        description="One product row in the catalog.",
        schema_ref=schema_ref,
        fields=fields,
        intent=_intent(),
    )


# ---- renderer ------------------------------------------------------------


def test_renders_basic_pydantic_class():
    contract = _contract(fields={"sku": "str", "qty": "int"})
    src = render_pydantic_from_data_contract(contract)
    # Header carries the source URI (operator can compare)
    assert "project://arch/contracts/shared/product-row" in src
    # Class name is the contract id PascalCased
    assert "class ProductRow(BaseModel):" in src
    # extra='forbid' on the model_config — strict by default
    assert 'model_config = ConfigDict(extra="forbid")' in src
    # Each field is annotated
    assert "sku: str" in src
    assert "qty: int" in src
    # Self-contained: imports come along
    assert "from pydantic import BaseModel, ConfigDict" in src


def test_renders_optional_and_collection_types():
    contract = _contract(
        fields={
            "tags": "list[str]",
            "price_cents": "int | None",
            "metadata": "dict[str, str]",
        }
    )
    src = render_pydantic_from_data_contract(contract)
    assert "tags: list[str]" in src
    assert "price_cents: int | None" in src
    assert "metadata: dict[str, str]" in src


def test_uses_contract_id_for_class_name_pascal_case():
    """Single-segment and multi-segment kebab ids both pascal-case correctly."""
    a = render_pydantic_from_data_contract(
        _contract(contract_id="user-profile", fields={"id": "str"})
    )
    assert "class UserProfile(BaseModel):" in a
    b = render_pydantic_from_data_contract(
        _contract(contract_id="user-profile-row", fields={"id": "str"})
    )
    assert "class UserProfileRow(BaseModel):" in b


def test_includes_description_as_class_docstring_when_present():
    contract = _contract(fields={"sku": "str"})
    src = render_pydantic_from_data_contract(contract)
    # Description renders as the class docstring so the operator
    # reading the generated file sees the rationale at a glance.
    assert '"""One product row in the catalog."""' in src


def test_handles_contract_without_description():
    contract = DataContract(
        id="x",
        description=None,
        schema_ref=None,
        fields={"a": "str"},
        intent=_intent(),
    )
    src = render_pydantic_from_data_contract(contract)
    # No docstring, but the class still renders.
    assert "class X(BaseModel):" in src


def test_raises_when_fields_missing():
    """No ``fields`` payload → renderer can't generate; fail loudly.

    Per CLAUDE.md "fail loudly" — no synthetic field placeholder, no
    silent empty class. Operator sees a clear error and either fills
    ``fields`` on the contract or waits for URI-resolution to land.
    """
    contract = _contract(fields=None)
    with pytest.raises(ValueError, match="fields"):
        render_pydantic_from_data_contract(contract)


def test_raises_when_fields_empty():
    contract = _contract(fields={})
    with pytest.raises(ValueError, match="fields"):
        render_pydantic_from_data_contract(contract)


def test_rejects_invalid_field_name():
    """Bad field names are caught early — they'd produce an unimportable
    class otherwise."""
    contract = _contract(fields={"123bad": "str"})
    with pytest.raises(ValueError, match="not a valid Python identifier"):
        render_pydantic_from_data_contract(contract)


def test_rejects_dunder_field_name():
    """Dunders collide with Pydantic internals — reject."""
    contract = _contract(fields={"__init__": "str"})
    with pytest.raises(ValueError, match="dunder"):
        render_pydantic_from_data_contract(contract)


# ---- round-trip ----------------------------------------------------------


def test_generated_source_imports_and_validates_an_instance(tmp_path: Path):
    """The generated source is real Pydantic code — write it, import
    it, instantiate it, and validate against extra='forbid'.
    """
    import importlib.util
    import sys

    contract = _contract(
        fields={"sku": "str", "qty": "int", "tags": "list[str]"}
    )
    src = render_pydantic_from_data_contract(contract)
    module_file = tmp_path / "generated_product_row.py"
    module_file.write_text(src)

    spec = importlib.util.spec_from_file_location(
        "generated_product_row", module_file
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["generated_product_row"] = mod
    try:
        spec.loader.exec_module(mod)
        ProductRow = mod.ProductRow
        # Happy path
        instance = ProductRow(sku="abc", qty=2, tags=["new"])
        assert instance.sku == "abc"
        # extra='forbid' rejects unknown field
        with pytest.raises(Exception):
            ProductRow(sku="abc", qty=2, tags=[], extra_thing=1)
    finally:
        sys.modules.pop("generated_product_row", None)


# ---- cli -----------------------------------------------------------------


def _write_contracts_with_data_contract(
    project_root: Path,
    *,
    module_id: str,
    contract: DataContract,
) -> None:
    contracts = ContractsFile(
        spec_version=1,
        module=module_id,
        data_contracts=[contract],
    )
    path = module_contracts_path(project_root, module_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))


def test_cli_render_pydantic_prints_to_stdout(tmp_path: Path):
    runner = CliRunner()
    contract = _contract(fields={"sku": "str", "qty": "int"})
    _write_contracts_with_data_contract(
        tmp_path, module_id="catalog-ingest", contract=contract
    )
    result = runner.invoke(
        cli,
        [
            "render",
            "pydantic",
            "catalog-ingest",
            "product-row",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "class ProductRow(BaseModel):" in result.output
    assert "sku: str" in result.output


def test_cli_render_pydantic_unknown_module(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "render",
            "pydantic",
            "missing-module",
            "x",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "missing-module" in result.output


def test_cli_render_pydantic_unknown_contract(tmp_path: Path):
    runner = CliRunner()
    contract = _contract(fields={"x": "int"})
    _write_contracts_with_data_contract(
        tmp_path, module_id="m", contract=contract
    )
    result = runner.invoke(
        cli,
        [
            "render",
            "pydantic",
            "m",
            "nonexistent-contract",
            "--path",
            str(tmp_path),
        ],
    )
    assert result.exit_code != 0
    assert "nonexistent-contract" in result.output


def test_cli_render_pydantic_handles_missing_fields_payload(tmp_path: Path):
    runner = CliRunner()
    contract = _contract(fields=None)
    _write_contracts_with_data_contract(
        tmp_path, module_id="m", contract=contract
    )
    result = runner.invoke(
        cli,
        ["render", "pydantic", "m", "product-row", "--path", str(tmp_path)],
    )
    assert result.exit_code != 0
    # User-friendly error explains the missing fields payload.
    assert "fields" in result.output


# ---- shape sanity --------------------------------------------------------


def test_rendered_output_matches_expected_shape():
    """Sanity check that the rendered output is laid out the way an
    operator copying it into their codebase would expect — no excess
    blank lines, no oddly-formatted blocks.
    """
    contract = _contract(fields={"sku": "str", "qty": "int"})
    src = render_pydantic_from_data_contract(contract)
    expected_substring = dedent(
        """\
        class ProductRow(BaseModel):
            \"\"\"One product row in the catalog.\"\"\"

            model_config = ConfigDict(extra=\"forbid\")

            sku: str
            qty: int
        """
    )
    assert expected_substring in src


def test_rendered_header_includes_regenerate_hint():
    """Track I Final: header carries an operator-readable regenerate hint."""
    contract = _contract(fields={"sku": "str"})
    src = render_pydantic_from_data_contract(contract)
    assert "Regenerate by re-running" in src
    assert "module_set_data_contract" in src


# ---- SA auto-generation integration (Track I Final) ---------------------


@pytest.mark.asyncio
async def test_module_set_data_contract_auto_renders_when_fields_present(tmp_path: Path):
    """When module_set_data_contract is invoked with a contract that
    carries inline fields, the handler atomically writes a Pydantic
    file under .jig/generated/contracts/<module>/<contract>.py."""
    from jig.sa_incremental_mcp import handle_module_set_data_contract
    from jig.spec_loader import generated_contract_path

    contract = _contract(fields={"sku": "str", "qty": "int"})
    cid = await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="catalog-ingest",
        data_contract=contract.model_dump(mode="json"),
    )
    assert cid == "product-row"
    expected = generated_contract_path(
        tmp_path, "catalog-ingest", "product-row"
    )
    assert expected.is_file()
    src = expected.read_text()
    assert "class ProductRow(BaseModel):" in src
    assert "sku: str" in src


@pytest.mark.asyncio
async def test_module_set_data_contract_skips_render_when_fields_absent(tmp_path: Path):
    """No fields → no render. The contract still saves to YAML; the
    Pydantic file just doesn't appear."""
    from jig.sa_incremental_mcp import handle_module_set_data_contract
    from jig.spec_loader import generated_contract_path

    contract = _contract(fields=None)
    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="m",
        data_contract=contract.model_dump(mode="json"),
    )
    assert not generated_contract_path(tmp_path, "m", "product-row").is_file()


@pytest.mark.asyncio
async def test_arch_regenerate_pydantic_models_walks_all_modules(tmp_path: Path):
    """Bulk regeneration tool walks every module's contracts.yaml and
    re-renders each contract that has inline fields."""
    from jig.sa_incremental_mcp import (
        handle_arch_regenerate_pydantic_models,
        handle_module_set_data_contract,
    )

    a = _contract(contract_id="row-a", fields={"sku": "str"})
    b = _contract(contract_id="row-b", fields={"qty": "int"})
    no_fields = _contract(contract_id="row-c", fields=None)

    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="m1",
        data_contract=a.model_dump(mode="json"),
    )
    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="m2",
        data_contract=b.model_dump(mode="json"),
    )
    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="m2",
        data_contract=no_fields.model_dump(mode="json"),
    )

    paths = await handle_arch_regenerate_pydantic_models(
        project_path=tmp_path, module_id=None
    )
    names = sorted(p.name for p in paths)
    assert names == ["row-a.py", "row-b.py"]


@pytest.mark.asyncio
async def test_arch_regenerate_pydantic_models_scopes_to_one_module(tmp_path: Path):
    """``module_id`` arg restricts the regeneration to that module."""
    from jig.sa_incremental_mcp import (
        handle_arch_regenerate_pydantic_models,
        handle_module_set_data_contract,
    )

    a = _contract(contract_id="row-a", fields={"sku": "str"})
    b = _contract(contract_id="row-b", fields={"qty": "int"})
    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="m1",
        data_contract=a.model_dump(mode="json"),
    )
    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="m2",
        data_contract=b.model_dump(mode="json"),
    )

    paths = await handle_arch_regenerate_pydantic_models(
        project_path=tmp_path, module_id="m1"
    )
    assert len(paths) == 1
    assert paths[0].name == "row-a.py"


@pytest.mark.asyncio
async def test_arch_regenerate_handles_unknown_module(tmp_path: Path):
    """Asking for a module that doesn't exist returns [] without raising."""
    from jig.sa_incremental_mcp import handle_arch_regenerate_pydantic_models

    paths = await handle_arch_regenerate_pydantic_models(
        project_path=tmp_path, module_id="missing"
    )
    assert paths == []


def test_sa_mvp_role_config_lists_arch_regenerate_pydantic_models(tmp_path: Path):
    """The shipped sa-mvp role exposes arch_regenerate_pydantic_models."""
    from jig.persistence import load_role

    cfg = load_role(tmp_path, "sa_mvp")
    assert "arch_regenerate_pydantic_models" in cfg.allowed_tools


@pytest.mark.asyncio
async def test_arch_regenerate_pydantic_models_mcp_tool_registered(
    tmp_path: Path, monkeypatch
):
    """The MCP server exposes the bulk regen tool when allowed_tools grants it."""
    import jig.mcp_server as mcp_server_mod
    from jig.mcp_server import create_agent_mcp_server
    from jig.models import RoleConfig
    from jig.store.bus import MessageBus
    from jig.store.memory import MemoryStore
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    tickets = TicketStore(tmp_path / "tickets.jsonl")
    threads = ThreadStore(tmp_path / "comments.jsonl")
    memory = MemoryStore(tmp_path)
    bus = MessageBus(tmp_path / "messages.jsonl")
    for s in (tickets, threads, memory, bus):
        await s.load()

    cfg = RoleConfig(
        role="sa-mvp",
        allowed_tools=["Read", "arch_regenerate_pydantic_models"],
        strict_tools=True,
    )
    captured: dict = {}
    real = mcp_server_mod.create_sdk_mcp_server

    def spy(*, name, tools):
        captured["tools"] = tools
        return real(name=name, tools=tools)

    monkeypatch.setattr(mcp_server_mod, "create_sdk_mcp_server", spy)

    create_agent_mcp_server(
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
        agent_role="sa-mvp",
        agent_cfg=cfg,
        worktree_path=tmp_path,
        project_path=tmp_path,
    )
    names = {t.name for t in captured["tools"]}
    assert "arch_regenerate_pydantic_models" in names


@pytest.mark.asyncio
async def test_generated_file_is_importable_round_trip(tmp_path: Path):
    """End-to-end: invoke module_set_data_contract → import the generated
    file → instantiate the model → reject extras."""
    import importlib.util
    import sys

    from jig.sa_incremental_mcp import handle_module_set_data_contract
    from jig.spec_loader import generated_contract_path

    contract = _contract(
        contract_id="round-trip-row",
        fields={"sku": "str", "qty": "int"},
    )
    await handle_module_set_data_contract(
        project_path=tmp_path,
        module_id="catalog-ingest",
        data_contract=contract.model_dump(mode="json"),
    )

    target = generated_contract_path(
        tmp_path, "catalog-ingest", "round-trip-row"
    )
    spec = importlib.util.spec_from_file_location(
        "round_trip_row", target
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["round_trip_row"] = mod
    try:
        spec.loader.exec_module(mod)
        instance = mod.RoundTripRow(sku="abc", qty=2)
        assert instance.sku == "abc"
        with pytest.raises(Exception):
            mod.RoundTripRow(sku="abc", qty=2, mystery=1)
    finally:
        sys.modules.pop("round_trip_row", None)
