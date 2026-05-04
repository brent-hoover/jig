"""Assertion union for the bones synthetic operator (Track H4).

Bones ships the five assertion kinds the bones scenario actually checks:

- ``artifact_written`` — a file path exists, optionally containing a
  substring or matching a regex
- ``analytics_event_emitted`` — at least one analytics event of the
  given kind was emitted, optionally with field-value constraints
- ``ticket_status`` — a ticket in the store reached a given status
- ``reviewer_returned_no_critical`` — a reviewer's return list contains
  no comments with severity ``critical``
- ``cost_under_budget`` — total cost stayed under N USD (skipped in
  mock mode where there's no LLM cost)

The full assertion taxonomy from ``docs/synthetic-operator/design.md``
§"Assertion framework" is a 13-kind superset; bones implements only
what the bones scenario actually needs. Adding new kinds is one entry
in the union + a check function in ``driver.evaluate_assertion``.

We use a Pydantic ``TypeAdapter`` rather than wrapping the union in a
``BaseModel`` because the YAML loader passes raw dicts to
``ScenarioAssertion.validate_python``; the TypeAdapter shape mirrors
the entry-point in ``jig.analytics.events.parse_event``.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

__all__ = [
    "AnalyticsEventEmittedAssertion",
    "ArtifactWrittenAssertion",
    "AssertionKind",
    "CostUnderBudgetAssertion",
    "EnvVarSetAssertion",
    "ReviewerReturnedNoCriticalAssertion",
    "ScenarioAssertion",
    "ScenarioAssertionUnion",
    "TicketStatusAssertion",
]


class AssertionKind(str, Enum):
    """Discriminator values for the bones assertion union."""

    ARTIFACT_WRITTEN = "artifact_written"
    ANALYTICS_EVENT_EMITTED = "analytics_event_emitted"
    TICKET_STATUS = "ticket_status"
    REVIEWER_RETURNED_NO_CRITICAL = "reviewer_returned_no_critical"
    COST_UNDER_BUDGET = "cost_under_budget"
    # Block 2 — verify a sim step stamped an env-var entry on the
    # driver context (used to gate JIG_FIXTURE_MODE-style spawn-env
    # checks without coupling assertions to live process env).
    ENV_VAR_SET = "env_var_set"


class _AssertionBase(BaseModel):
    """Shared envelope. ``extra='forbid'`` catches typos in YAML."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class ArtifactWrittenAssertion(_AssertionBase):
    """A file at ``path`` (project-root-relative) exists.

    Optional ``contains`` substring matches the file's text content;
    useful for "the L0 pitch landed and mentions <keyword>" without
    requiring an exact-match fixture.
    """

    kind: Literal[AssertionKind.ARTIFACT_WRITTEN.value] = AssertionKind.ARTIFACT_WRITTEN.value
    path: str = Field(..., min_length=1)
    contains: str | None = None


class AnalyticsEventEmittedAssertion(_AssertionBase):
    """At least one analytics event of ``event_kind`` was emitted.

    ``field_constraints`` (optional) narrows to events whose top-level
    fields match all listed key/value pairs. For bones this is a
    surface check — values must be JSON-serializable scalars; deep
    structural matches land with MVP if needed.
    """

    kind: Literal[AssertionKind.ANALYTICS_EVENT_EMITTED.value] = (
        AssertionKind.ANALYTICS_EVENT_EMITTED.value
    )
    event_kind: str = Field(..., min_length=1)
    field_constraints: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
    )


class TicketStatusAssertion(_AssertionBase):
    """A ticket in the store reached ``status``.

    Status values are the string forms of ``jig.ticket.TicketStatus``
    (``open``, ``in_progress``, ``resolved``, etc.). The driver
    resolves them by string compare to avoid coupling the simulator
    schema to the ticket-status enum (which evolves on its own
    cadence).
    """

    kind: Literal[AssertionKind.TICKET_STATUS.value] = AssertionKind.TICKET_STATUS.value
    ticket_id: str = Field(..., min_length=1)
    status: str = Field(..., min_length=1)


class ReviewerReturnedNoCriticalAssertion(_AssertionBase):
    """The given reviewer's last invocation returned no critical comments.

    Bones uses this only against ``contract-compliance``. Other reviewer
    ids land alongside the rest of the federation (Track G6+).
    """

    kind: Literal[AssertionKind.REVIEWER_RETURNED_NO_CRITICAL.value] = (
        AssertionKind.REVIEWER_RETURNED_NO_CRITICAL.value
    )
    reviewer_id: str = Field(..., min_length=1)


class EnvVarSetAssertion(_AssertionBase):
    """One env-var entry from the most recent sim step matches.

    Block 2 covers ``JIG_FIXTURE_MODE`` spawn-env checks and the
    operator-supplied connection string surfacing — both stamp their
    output on the driver context so the assertion can introspect
    without depending on live process environment.

    ``source`` selects which ctx field to read:

    - ``fixture_env`` (default) → ``ctx.last_fixture_env``
    - ``operator_supplied`` → returns the single
      ``{"_url": ctx.last_operator_supplied_url}`` view, so the
      assertion can match the URL with ``name="_url"``.
    """

    kind: Literal[AssertionKind.ENV_VAR_SET.value] = (
        AssertionKind.ENV_VAR_SET.value
    )
    name: str = Field(..., min_length=1)
    value: str | None = None
    source: Literal["fixture_env", "operator_supplied"] = "fixture_env"


class CostUnderBudgetAssertion(_AssertionBase):
    """Total scenario cost stayed under ``usd``.

    Mock-mode runs have no LLM cost so the driver evaluates this as
    ``observed_cost == 0.0 < usd`` — always true. Real-mode runs
    aggregate ``AgentCompleted.cost_estimate_usd`` across the run; the
    bones success criterion is < $1.
    """

    kind: Literal[AssertionKind.COST_UNDER_BUDGET.value] = (
        AssertionKind.COST_UNDER_BUDGET.value
    )
    usd: float = Field(..., gt=0.0)


# Discriminated union; the YAML loader passes raw dicts to
# ``validate_python``. ``TypeAdapter`` mirrors the parse_event pattern in
# ``jig.analytics.events`` without paying for a wrapper BaseModel.
ScenarioAssertionUnion = Annotated[
    Union[
        ArtifactWrittenAssertion,
        AnalyticsEventEmittedAssertion,
        TicketStatusAssertion,
        ReviewerReturnedNoCriticalAssertion,
        CostUnderBudgetAssertion,
        EnvVarSetAssertion,
    ],
    Field(discriminator="kind"),
]

ScenarioAssertion: TypeAdapter[ScenarioAssertionUnion] = TypeAdapter(
    ScenarioAssertionUnion,
)
