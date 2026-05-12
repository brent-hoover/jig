"""Pydantic v2 models for the prompt-style eval harness.

These types are the contract every other module is built against. Validators
encode the few invariants the rest of the harness relies on:

- ``outcome == "code"`` iff ``extracted_code``, ``test_result``,
  ``static_metrics``, and ``judge`` are all populated.
- Rubric item ids are unique within a rubric version.

JSON round-trip is required: the JSONL store reads and writes ``RunRecord``
exclusively via ``model_dump_json`` / ``model_validate_json``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


Outcome = Literal["code", "question", "refusal", "malformed", "error", "timeout"]
RubricScale = Literal["bool", "likert_5"]


class Task(BaseModel):
    """Task metadata loaded from ``tasks/<id>/task.yaml``."""

    id: str
    version: str
    language: str = "python"
    entrypoint: str
    test_command: list[str]
    timeout_s: int = Field(gt=0)


class Prompt(BaseModel):
    """A prompt variant loaded from ``tasks/<task_id>/prompts/<id>.md``.

    The prompt text is read verbatim and passed to the candidate as the user
    message. ``content_hash`` is the sha256 of ``text`` and becomes the
    ``prompt_version`` recorded against each run — editing the prompt produces
    a new version automatically.
    """

    task_id: str
    prompt_id: str
    text: str
    content_hash: str  # sha256 hex of text


class RubricItem(BaseModel):
    id: str
    prompt: str  # natural-language question presented to the judge
    scale: RubricScale


class Rubric(BaseModel):
    version: str
    items: list[RubricItem]

    @model_validator(mode="after")
    def _no_duplicate_item_ids(self) -> "Rubric":
        ids = [item.id for item in self.items]
        if len(set(ids)) != len(ids):
            raise ValueError(f"Rubric {self.version}: duplicate item ids")
        return self


class TestResult(BaseModel):
    # Not a pytest test class — opt out of collection.
    __test__ = False

    passed: bool
    n_passed: int = Field(ge=0)
    n_failed: int = Field(ge=0)
    duration_s: float = Field(ge=0)
    stdout: str = ""
    stderr: str = ""


class StaticMetrics(BaseModel):
    loc: int = Field(ge=0)
    ruff_findings: int = Field(ge=0)
    ruff_breakdown: dict[str, int] = Field(default_factory=dict)
    cyclomatic_max: int = Field(ge=0)


class JudgeScore(BaseModel):
    model: str
    snapshot: str | None = None
    rubric_version: str
    checklist: dict[str, bool | int]
    tokens: dict[str, int] = Field(default_factory=dict)
    cost_usd: float = Field(ge=0)


class Cell(BaseModel):
    """Identity of a (task, prompt, config) combination.

    Two records belong to the same cell iff every field of their ``Cell``
    matches. Sample-count semantics in the runner use this for "ensure the
    store has N records for this cell".
    """

    task_id: str
    task_version: str
    prompt_id: str
    prompt_version: str  # sha256:<hex>
    model: str
    model_snapshot: str | None = None
    temperature: float
    rubric_version: str


class RunRecord(BaseModel):
    """One row in the JSONL store.

    Represents either a fresh candidate invocation (``derived_from is None``)
    or a rescore derived from an existing record (``derived_from`` set, only
    the judge fields are new).
    """

    run_id: str  # uuid4
    timestamp: datetime
    cell: Cell
    prompt: str  # full prompt text sent to the candidate
    transcript: list[dict]  # SDK message list; opaque to the harness
    outcome: Outcome
    extracted_code: str | None = None
    test_result: TestResult | None = None
    static_metrics: StaticMetrics | None = None
    judge: JudgeScore | None = None
    candidate_tokens: dict[str, int] = Field(default_factory=dict)
    candidate_cost_usd: float = Field(ge=0)
    derived_from: str | None = None  # original run_id for rescores

    @model_validator(mode="after")
    def _outcome_fields_consistent(self) -> "RunRecord":
        code_specific = {
            "extracted_code": self.extracted_code,
            "test_result": self.test_result,
            "static_metrics": self.static_metrics,
            "judge": self.judge,
        }
        if self.outcome == "code":
            missing = [name for name, value in code_specific.items() if value is None]
            if missing:
                raise ValueError(
                    f"outcome='code' requires {', '.join(sorted(missing))} to be populated"
                )
        else:
            populated = [name for name, value in code_specific.items() if value is not None]
            if populated:
                raise ValueError(
                    f"outcome='{self.outcome}' must not have code-specific fields populated "
                    f"(found: {', '.join(sorted(populated))})"
                )
        return self
