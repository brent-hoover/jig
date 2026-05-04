"""Synthetic operator persona schema (Track H3 bones + H7 MVP).

Bones shipped only ``methodical``. MVP (Track H follow-on) added
``fast-and-shippy`` and ``ambivalent``. ``scope-creeper`` and
``hostile`` land in Final. The schema's defaults assume the
methodical baseline so authoring a new persona only requires the
fields that diverge.

A persona is a structured behavior profile, not a vibe — the driver
loads it as system-prompt context for the synthetic-operator LLM
(eventually; bones + MVP scope is fully scripted; policy-driven
turns are Final).

Persona files live at ``jig/sim/personas/<id>.yaml`` and ship with the
package. Operators authoring custom personas point ``load_persona``
at any path.
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
# ``docs/v2.0/synthetic-operator/design.md`` §"Persona library". Locked in a
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

    # MVP (Track H follow-on) — explicit probability fields for the two
    # behaviors that distinguish fast-and-shippy + ambivalent from
    # methodical. Defaults preserve methodical's profile so existing
    # YAMLs validate unchanged.
    #
    # ``gate_acceptance_probability``: how often the persona accepts a
    # gate without scrutiny. fast-and-shippy is high (0.9+); methodical
    # mid (~0.5); ambivalent high but for the wrong reason (~0.85).
    #
    # ``clarification_request_probability``: how often the persona
    # asks the agent to clarify before answering. methodical is low
    # (~0.05); ambivalent is high (~0.6) because vague answers
    # frequently force the agent to ask.
    #
    # ``prefers_short_rationale``: when true, persona's rationale text
    # in scripted scenarios is terse. fast-and-shippy true; methodical
    # false; ambivalent true.
    gate_acceptance_probability: float = Field(default=0.5, ge=0.0, le=1.0)
    clarification_request_probability: float = Field(
        default=0.05, ge=0.0, le=1.0
    )
    prefers_short_rationale: bool = False

    # Final (Track H Final) — behavior fields that distinguish
    # scope-creeper + hostile from the other personas. Defaults stay
    # methodical-friendly (zero) so existing YAMLs continue to validate
    # unchanged.
    #
    # ``feature_addition_probability``: how often the persona attempts
    # to introduce additional scope mid-stream ("oh and it should also
    # send an email"). scope-creeper is high (~0.7); others ~0.0.
    #
    # ``contradictory_input_probability``: how often the persona
    # contradicts a previous answer or an agent playback. hostile is
    # high (~0.6); others ~0.0.
    #
    # ``tangent_question_probability``: how often the persona derails
    # with an unrelated question ("what time is it?", "go away"). hostile
    # is high (~0.4); others ~0.0.
    feature_addition_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    contradictory_input_probability: float = Field(default=0.0, ge=0.0, le=1.0)
    tangent_question_probability: float = Field(default=0.0, ge=0.0, le=1.0)

    # Final (Track H Final) — response template bank for policy-driven
    # turns. Maps a "gate kind" or named situation (e.g. "confirm_gate",
    # "give_pitch", "clarify_request") to a list of candidate responses;
    # the policy module samples deterministically from the list given a
    # scenario seed. Empty by default; policy-driven scenarios fall
    # back to a single generic response if a key is missing.
    response_templates: dict[str, list[str]] = Field(default_factory=dict)


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
