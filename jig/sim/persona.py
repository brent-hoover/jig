"""Synthetic operator persona schema (Track H3, bones).

Bones ships only ``methodical``. The full persona library
(fast-and-shippy, scope-creeper, ambivalent, hostile) lands in MVP
(Track H7). The schema's defaults assume the methodical baseline so
authoring a new persona only requires the fields that diverge.

A persona is a structured behavior profile, not a vibe — the driver
loads it as system-prompt context for the synthetic-operator LLM
(eventually; bones doesn't actually invoke the LLM for the operator
side because every step is fully scripted).

Persona files live at ``jig/sim/personas/<id>.yaml`` and ship with the
package. Operators authoring custom personas point ``load_persona``
at any path; only the bones ``methodical`` is shipped.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "GateConfirmationPolicy",
    "Persona",
    "load_persona",
    "persona_path",
]


# Conservative bones list — the canonical set from
# ``docs/synthetic-operator/design.md`` §"Persona library". Locked in a
# Literal so a typo in YAML loads to a ValidationError rather than a
# silent default.
GateConfirmationPolicy = Literal[
    "confirm_when_clear",
    "confirm_eagerly",
    "confirm_then_re_open",
    "confirm_passively",
    "refuse_initially",
]


class Persona(BaseModel):
    """One synthetic operator persona profile.

    Fields beyond ``id`` / ``description`` / ``gate_confirmation_policy``
    are advisory for bones — the bones scenario is fully scripted, so
    response_patterns + ambiguity tuning don't drive runtime behavior
    yet. They land in H8 (policy-driven turns) when the synthetic-
    operator LLM starts generating responses.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1, description="kebab-case persona id")
    description: str = Field(..., min_length=1)
    gate_confirmation_policy: GateConfirmationPolicy

    # Advisory for bones; the scripted scenario doesn't read these. They
    # exist on the schema so persona files written for bones already
    # have the slots MVP/Final populate.
    response_patterns: list[str] = Field(default_factory=list)
    avoid_behaviors: list[str] = Field(default_factory=list)
    override_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    ambiguity_in_answers: Literal["low", "medium", "high"] = "low"
    patience_for_clarification: Literal[
        "very_low", "low", "medium", "high"
    ] = "high"


def persona_path(persona_id: str) -> Path:
    """Return the on-disk path to a packaged persona's YAML.

    Raises ``FileNotFoundError`` if the persona id isn't a packaged
    file. Operators using a custom persona path call ``load_persona``
    directly with the full path.
    """
    src = Path(__file__).parent / "personas" / f"{persona_id}.yaml"
    if not src.is_file():
        raise FileNotFoundError(
            f"persona {persona_id!r} not packaged at {src}; "
            "bones ships only 'methodical'"
        )
    return src


def load_persona(path: Path) -> Persona:
    """Load and validate a persona YAML.

    Raises ``pydantic.ValidationError`` on schema mismatch — the
    discriminated literals on ``gate_confirmation_policy`` /
    ``ambiguity_in_answers`` catch typos at load time.
    """
    data = yaml.safe_load(path.read_text()) or {}
    return Persona.model_validate(data)
