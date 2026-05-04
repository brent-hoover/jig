"""Constructors for ``project://arch/...`` URIs (SA output)."""
from __future__ import annotations

from jig.uri.constructors._segments import (
    fragment_suffix,
    revision_suffix,
    seg,
)


def arch_architecture_uri(revision: int | None = None) -> str:
    """``project://arch/architecture[@revision:N]`` — top-level architecture doc."""
    return f"project://arch/architecture{revision_suffix(revision)}"


def arch_module_uri(module_id: str) -> str:
    """``project://arch/modules/<id>`` — one module within architecture."""
    return f"project://arch/modules/{seg(module_id, 'module_id')}"


def arch_contracts_uri(
    module_id: str,
    fragment: str | None = None,
    revision: int | None = None,
) -> str:
    """``project://arch/modules/<id>/contracts[@revision:N][#fragment]``."""
    return (
        f"project://arch/modules/{seg(module_id, 'module_id')}/contracts"
        f"{revision_suffix(revision)}{fragment_suffix(fragment)}"
    )


def arch_shared_contract_uri(contract_id: str) -> str:
    """``project://arch/contracts/shared/<id>`` — a project-wide shared contract."""
    return (
        "project://arch/contracts/shared/"
        f"{seg(contract_id, 'contract_id')}"
    )


def arch_integration_ac_uri(
    module_id: str, capability_id: str, must_index: int | None = None
) -> str:
    """``project://arch/modules/<m>/contracts#integration_ac/<cap>[/must/<n>]``."""
    base = arch_contracts_uri(module_id)
    fragment = f"integration_ac/{seg(capability_id, 'capability_id')}"
    if must_index is not None:
        if must_index < 0:
            raise ValueError(
                f"must_index must be >= 0, got {must_index!r}"
            )
        fragment = f"{fragment}/must/{must_index}"
    return f"{base}#{fragment}"


def arch_behavioral_contract_uri(
    module_id: str, contract_id: str
) -> str:
    """``project://arch/modules/<m>/contracts#behavioral_contracts/<id>``."""
    return (
        f"{arch_contracts_uri(module_id)}"
        f"#behavioral_contracts/{seg(contract_id, 'contract_id')}"
    )


def arch_data_contract_uri(module_id: str, contract_id: str) -> str:
    """``project://arch/modules/<m>/contracts#data_contracts/<id>``."""
    return (
        f"{arch_contracts_uri(module_id)}"
        f"#data_contracts/{seg(contract_id, 'contract_id')}"
    )


def arch_risk_uri(risk_id: str) -> str:
    """``project://arch/risks/<id>`` — one risk in the architecture register."""
    return f"project://arch/risks/{seg(risk_id, 'risk_id')}"
