"""Round-trip + defaults coverage for the SA MVP write helpers (Track C MVP).

The bones SA path used a single one-shot ``handle_sa_finalize`` that
serialized two pre-validated artifacts in one go. The MVP discovery
loop authors them incrementally, so we need ``save_architecture`` and
``save_module_contracts`` for the upsert handlers to round-trip cleanly
through disk between tool calls.

Also pins ``Module.n_a_categories``: a per-module list of checklist
categories the module legitimately doesn't address. ``arch_finalize``
in Deliverable 3 reads this to skip checklist enforcement on the named
categories — silence is treated as "forgot", not "doesn't apply".
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from jig.intent import ComplicationsConsidered, Intent
from jig.schemas.arch import (
    Architecture,
    ContractsFile,
    DataStore,
    Module,
    OwnedCollection,
    TierHint,
)
from jig.spec_loader import (
    architecture_path,
    load_architecture,
    load_module_contracts,
    module_contracts_path,
    save_architecture,
    save_module_contracts,
)


def _intent() -> Intent:
    return Intent(
        problem="Persist normalized catalog records for downstream queries.",
        simplest_solution="Single SQLite table keyed by (customer_id, sku).",
        complications_considered=ComplicationsConsidered(),
    )


def _module(module_id: str = "catalog-ingest") -> Module:
    return Module(
        id=module_id,
        title="Catalog Ingest",
        summary="Pulls from external sources and normalizes.",
        implements_capabilities=["shopify-connect"],
        owns=["products"],
        tier_hint=TierHint.STANDARD,
        intent=_intent(),
    )


# ---- Module.n_a_categories defaults ---------------------------------------


def test_module_n_a_categories_defaults_to_empty_list() -> None:
    m = _module()
    # Default must be an empty list (not None / not unset) so the
    # checklist enforcer can iterate without a None-check at every site.
    assert m.n_a_categories == []


def test_module_accepts_n_a_categories_list() -> None:
    m = Module(
        id="thin-module",
        title="Thin module",
        summary="Owns nothing; implements one trivial capability.",
        implements_capabilities=["passthrough"],
        owns=[],
        intent=_intent(),
        n_a_categories=["behavioral_contracts", "external_dependencies"],
    )
    assert "behavioral_contracts" in m.n_a_categories
    assert "external_dependencies" in m.n_a_categories


# ---- save_architecture round-trip -----------------------------------------


@pytest.fixture
def arch(tmp_path: Path) -> Architecture:
    return Architecture(
        spec_version=1,
        data_stores=[
            DataStore(
                id="main-db",
                kind="sqlite",
                rationale="bones — single-process file-backed",
                accessed_by=["catalog-ingest"],
            )
        ],
        modules=[_module()],
    )


def test_save_architecture_writes_to_canonical_path(
    tmp_path: Path, arch: Architecture
) -> None:
    save_architecture(tmp_path, arch)
    assert architecture_path(tmp_path).is_file()


def test_save_architecture_round_trip(tmp_path: Path, arch: Architecture) -> None:
    save_architecture(tmp_path, arch)
    loaded = load_architecture(tmp_path)
    assert loaded.modules[0].id == "catalog-ingest"
    assert loaded.data_stores[0].kind == "sqlite"
    assert loaded.modules[0].intent.problem.startswith("Persist normalized")


def test_save_architecture_preserves_n_a_categories(tmp_path: Path) -> None:
    """The new field must round-trip — checklist enforcement reads it."""
    m = _module()
    m.n_a_categories = ["behavioral_contracts"]
    arch = Architecture(modules=[m])
    save_architecture(tmp_path, arch)
    loaded = load_architecture(tmp_path)
    assert loaded.modules[0].n_a_categories == ["behavioral_contracts"]


def test_save_architecture_yaml_is_human_readable(
    tmp_path: Path, arch: Architecture
) -> None:
    """sort_keys=False keeps the operator-visible field order stable."""
    save_architecture(tmp_path, arch)
    raw = architecture_path(tmp_path).read_text()
    # spec_version should appear before modules in the dump, not
    # alphabetically before data_stores — the operator reads top-down.
    spec_version_idx = raw.find("spec_version:")
    modules_idx = raw.find("modules:")
    assert spec_version_idx >= 0 and modules_idx >= 0
    assert spec_version_idx < modules_idx


def test_save_architecture_overwrites_existing(
    tmp_path: Path, arch: Architecture
) -> None:
    """Idempotent re-save lets the upsert handlers replay safely."""
    save_architecture(tmp_path, arch)
    arch.modules[0].title = "Catalog Ingest (revised)"
    save_architecture(tmp_path, arch)
    loaded = load_architecture(tmp_path)
    assert loaded.modules[0].title == "Catalog Ingest (revised)"


# ---- save_module_contracts round-trip -------------------------------------


@pytest.fixture
def contracts() -> ContractsFile:
    return ContractsFile(
        spec_version=1,
        module="catalog-ingest",
        owns=[
            OwnedCollection(
                collection="products",
                db="main-db",
                write_access=["self"],
                read_access=[],
            )
        ],
    )


def test_save_module_contracts_writes_to_canonical_path(
    tmp_path: Path, contracts: ContractsFile
) -> None:
    save_module_contracts(tmp_path, "catalog-ingest", contracts)
    assert module_contracts_path(tmp_path, "catalog-ingest").is_file()


def test_save_module_contracts_creates_module_dir(
    tmp_path: Path, contracts: ContractsFile
) -> None:
    """First upsert against a new module must create the parent dir."""
    target = module_contracts_path(tmp_path, "catalog-ingest")
    assert not target.parent.exists()
    save_module_contracts(tmp_path, "catalog-ingest", contracts)
    assert target.parent.is_dir()


def test_save_module_contracts_round_trip(
    tmp_path: Path, contracts: ContractsFile
) -> None:
    save_module_contracts(tmp_path, "catalog-ingest", contracts)
    loaded = load_module_contracts(tmp_path, "catalog-ingest")
    assert loaded.module == "catalog-ingest"
    assert loaded.owns[0].collection == "products"


def test_save_module_contracts_yaml_field_order(
    tmp_path: Path, contracts: ContractsFile
) -> None:
    save_module_contracts(tmp_path, "catalog-ingest", contracts)
    raw = module_contracts_path(tmp_path, "catalog-ingest").read_text()
    # Operator-readable: module name appears before owns list.
    module_idx = raw.find("module:")
    owns_idx = raw.find("owns:")
    assert module_idx >= 0 and owns_idx >= 0
    assert module_idx < owns_idx


def test_save_module_contracts_yaml_is_valid_yaml(
    tmp_path: Path, contracts: ContractsFile
) -> None:
    save_module_contracts(tmp_path, "catalog-ingest", contracts)
    data = yaml.safe_load(
        module_contracts_path(tmp_path, "catalog-ingest").read_text()
    )
    assert data["module"] == "catalog-ingest"
