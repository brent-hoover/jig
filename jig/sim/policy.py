"""Policy-driven turn generation (Track H Final).

Per ``docs/v2.0/synthetic-operator/design.md`` §"Scripted vs policy-driven
turns":

> Pure scripts are deterministic but tedious; policy-driven ('this
> persona accepts gates 80% of the time') is more flexible but less
> predictable. Probably both — scripts for regression suites, policies
> for exploratory runs.

For Final scope, this module ships the **template-based** policy
machinery:

- ``apply_policy(persona, prompt, scenario_seed)`` samples a response
  from the persona's ``response_templates`` bank using a deterministic
  RNG seeded by ``(scenario_seed, persona.id, prompt_kind)``.
- The same (persona, prompt_kind, seed) triple always returns the same
  response — reproducibility is load-bearing for the simulator's CI
  contract.
- LLM-driven response generation is v2.x.

A scenario opts in via ``policy_driven: true`` (see
``jig.sim.scenario.Scenario``); the driver then routes step responses
through ``apply_policy`` rather than reading literal scripted text.
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.sim.persona import Persona

__all__ = [
    "PolicyTurn",
    "PromptKind",
    "apply_policy",
    "sample_response",
]


# Common prompt kinds the simulator emits. Keeping this tight (Literal,
# not free-form) means a typo in a scenario YAML or driver call lands
# as a TypeError rather than silently sampling the fallback.
PromptKind = Literal[
    "confirm_gate",
    "give_pitch",
    "clarify_request",
    "answer_question",
    "propose_capability",
]


class PolicyTurn(BaseModel):
    """One sampled policy-driven turn.

    Captures the inputs (persona id, prompt, seed) + the deterministic
    output so a scenario report can reproduce + audit policy decisions
    after the fact. Reproducibility is the load-bearing contract: the
    same triple always yields the same ``response``.
    """

    model_config = ConfigDict(extra="forbid")

    persona_id: str = Field(..., min_length=1)
    prompt_kind: PromptKind
    prompt: str = Field(default="")
    scenario_seed: int = Field(...)
    response: str = Field(...)


# Generic fallback responses keyed by gate_confirmation_policy. When a
# persona has no template entry for the requested ``prompt_kind`` we
# fall through to this map so a partially-templated persona still yields
# a sensible response. Keeping the fallback short + persona-flavored
# means policy turns degrade gracefully when a scenario hits an
# unexpected prompt.
_GATE_POLICY_FALLBACK: dict[str, str] = {
    "confirm_when_clear": "Looks good — proceed.",
    "confirm_eagerly": "Yes, ship it.",
    "confirm_then_re_open": "Approved. Wait, can we also add one more thing?",
    "confirm_passively": "Sure, I guess.",
    "refuse_initially": "No.",
}


def _seeded_index(scenario_seed: int, persona_id: str, prompt_kind: str, n: int) -> int:
    """Deterministic non-negative index in ``range(n)``.

    Hash the (seed, persona, prompt_kind) tuple to a stable bucket.
    SHA-256 over the canonical bytes form is overkill for this sample
    size but means we never collide on a Python-version-dependent hash
    (CPython's ``hash()`` is randomized at process start).
    """
    if n <= 0:
        raise ValueError("_seeded_index requires n > 0")
    payload = f"{scenario_seed}|{persona_id}|{prompt_kind}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big") % n


def sample_response(
    persona: Persona,
    prompt_kind: PromptKind,
    *,
    scenario_seed: int,
) -> str:
    """Sample one response from the persona's template bank.

    Deterministic given (persona.id, prompt_kind, scenario_seed). When
    the persona has no entries for ``prompt_kind`` we fall through to
    ``_GATE_POLICY_FALLBACK[persona.gate_confirmation_policy]``.
    """
    bank = persona.response_templates.get(prompt_kind, [])
    if bank:
        idx = _seeded_index(scenario_seed, persona.id, prompt_kind, len(bank))
        return bank[idx]
    fallback = _GATE_POLICY_FALLBACK.get(persona.gate_confirmation_policy)
    if fallback is not None:
        return fallback
    return "(no template)"


def apply_policy(
    persona: Persona,
    prompt: str,
    *,
    prompt_kind: PromptKind = "confirm_gate",
    scenario_seed: int,
) -> PolicyTurn:
    """Generate one policy-driven turn for ``persona`` given ``prompt``.

    The ``prompt`` text is recorded on the resulting ``PolicyTurn`` for
    audit purposes but doesn't influence sampling — Final scope is
    template-based; LLM-driven generation (which would condition on the
    full prompt + history) is v2.x.

    Reproducibility contract: identical (persona.id, prompt_kind,
    scenario_seed) inputs always return a ``PolicyTurn`` with the same
    ``response``.
    """
    response = sample_response(persona, prompt_kind, scenario_seed=scenario_seed)
    return PolicyTurn(
        persona_id=persona.id,
        prompt_kind=prompt_kind,
        prompt=prompt,
        scenario_seed=scenario_seed,
        response=response,
    )
