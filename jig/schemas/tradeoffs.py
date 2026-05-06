"""Tradeoff ledger schema — `.jig/spec/tradeoffs.yaml`.

PO authors this during L0/L3 to record deliberate "we decided not to do X
at the bones layer" decisions. The tradeoff-compliance reviewer consults it
to flag when a ticket appears to re-add a deferred item.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jig.spec_schema import _kebab_slug


class Tradeoff(BaseModel):
    """One deliberate scope decision recorded by the PO.

    ``capability_ids`` links the decision to the capabilities it affects;
    the tradeoff-compliance reviewer uses this to fire on the right tickets.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    decision_summary: str = Field(
        ..., min_length=1, description="One-line: what was chosen."
    )
    deferred: list[str] = Field(
        default_factory=list,
        description="Short descriptions of what was explicitly NOT done.",
    )
    deferred_to: Literal["mvp", "final", "never"] = Field(
        ...,
        description=(
            "Which layer the deferred work might land in, or 'never' "
            "if it's permanently out of scope."
        ),
    )
    rationale: str = Field(..., min_length=1)
    capability_ids: list[str] = Field(
        default_factory=list,
        description="Capability ids this tradeoff applies to.",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return _kebab_slug(v)


class TradeoffLedger(BaseModel):
    """`.jig/spec/tradeoffs.yaml` — the full set of recorded tradeoffs."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    tradeoffs: list[Tradeoff] = Field(default_factory=list)

    def for_capability(self, capability_id: str) -> list[Tradeoff]:
        """Return tradeoffs that explicitly reference ``capability_id``."""
        return [t for t in self.tradeoffs if capability_id in t.capability_ids]

    def deferred_to_layer(self, layer: str) -> list[Tradeoff]:
        """Return tradeoffs whose deferred items are expected at ``layer``."""
        return [t for t in self.tradeoffs if t.deferred_to == layer]
