"""TracerSpec — first-class tracer schema (Phase 5.12).

A tracer is a runnable smoke test spec stored at
``.jig/spec/tracers/<id>.yaml``, decoupled from the bones ticket that
originally commissioned it.  Having the spec in version control lets the
graph derive layer link tracers to the nodes they cover, and the
tracer-preservation reviewer can detect when a ticket's changes touch a
covered node.

Example ``.jig/spec/tracers/hn-cli-smoke.yaml``::

    spec_version: 1
    id: hn-cli-smoke
    description: "Smoke: hn-cli top --limit 3 prints 3 lines"
    command: ["python", "-m", "pytest", "tests/smoke/test_hn_cli.py"]
    covers:
      modules: [api-client, cli-frontend]
      capabilities: [fetch-top-stories, format-output]
    bones_ticket_id: t-001   # optional backlink
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jig.safe_path import validate_safe_path_segment


class TracerCovers(BaseModel):
    """The graph nodes a tracer exercises."""

    model_config = ConfigDict(extra="forbid")

    modules: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    exposed_apis: list[str] = Field(
        default_factory=list,
        description="Format: '<module_id>:<api_name>'",
    )
    emitted_events: list[str] = Field(
        default_factory=list,
        description="Format: '<module_id>:<event_name>'",
    )


class TracerSpec(BaseModel):
    """One tracer bullet — decoupled from its bones ticket."""

    model_config = ConfigDict(extra="forbid")

    spec_version: int = 1
    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    command: list[str] = Field(
        ...,
        min_length=1,
        description="Shell command to run the tracer smoke.",
    )
    covers: TracerCovers = Field(default_factory=TracerCovers)
    bones_ticket_id: str | None = None
    timeout_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
        description="Hard timeout in seconds before the run is killed.",
    )

    @field_validator("id")
    @classmethod
    def _validate_id(cls, v: str) -> str:
        return validate_safe_path_segment(v, "TracerSpec.id")


__all__ = ["TracerSpec", "TracerCovers"]
