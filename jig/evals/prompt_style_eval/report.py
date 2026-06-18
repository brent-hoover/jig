"""Aggregation and reporting over persisted ``RunRecord`` rows.

Groups records by ``(task_id, prompt_id)``, computes per-cell descriptive
stats with bootstrap CIs, and renders either a human-readable text block
or a JSON dump.

Statistics:

- **outcomes**: count per outcome bucket (code / question / refusal /
  malformed / error / timeout).
- **pass_rate**: fraction of *all* runs whose tests passed — not just
  code-outcome runs, since non-code outcomes count against the prompt.
- **pass_ci**: 95 % bootstrap CI on pass_rate.
- **cost**: sum of candidate cost + judge cost (when present), total
  for the cell.
- **static**: mean LoC / ruff_findings / cyclomatic_max over code
  outcomes.
- **judge**: mean of each checklist item over code-outcome runs *with
  a judge score* (judge failures are dropped from the mean rather than
  treated as zeros).
"""

from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from jig.evals.prompt_style_eval.models import Outcome, RunRecord


_OUTCOMES: tuple[Outcome, ...] = (
    "code",
    "question",
    "refusal",
    "malformed",
    "error",
    "timeout",
)


@dataclass
class CellReport:
    task_id: str
    task_version: str
    prompt_id: str
    prompt_version: str  # sha256:<hex>
    model: str
    model_snapshot: str | None
    temperature: float
    rubric_version: str
    n: int
    outcomes: dict[Outcome, int]
    pass_rate: float
    pass_ci: tuple[float, float]
    total_cost_usd: float
    static_mean: dict[str, float]
    judge_mean: dict[str, float]
    judge_coverage: int  # how many runs had a judge score

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_version": self.task_version,
            "prompt_id": self.prompt_id,
            "prompt_version": self.prompt_version,
            "model": self.model,
            "model_snapshot": self.model_snapshot,
            "temperature": self.temperature,
            "rubric_version": self.rubric_version,
            "n": self.n,
            "outcomes": dict(self.outcomes),
            "pass_rate": self.pass_rate,
            "pass_ci_lo": self.pass_ci[0],
            "pass_ci_hi": self.pass_ci[1],
            "total_cost_usd": self.total_cost_usd,
            "static_mean": self.static_mean,
            "judge_mean": self.judge_mean,
            "judge_coverage": self.judge_coverage,
        }


@dataclass
class Report:
    cells: list[CellReport] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"cells": [c.to_dict() for c in self.cells]}


def bootstrap_ci(
    values: Sequence[float],
    *,
    n_resamples: int = 1000,
    alpha: float = 0.05,
    seed: int = 0,
) -> tuple[float, float]:
    """Non-parametric bootstrap CI on the mean of ``values``.

    Returns ``(lo, hi)`` at confidence ``1 - alpha``. With ``len(values)``
    very small (e.g. 1–3), the interval will be near-uninformative; that's
    a property of the data, not a bug — we still surface a CI so the
    caller knows the uncertainty.
    """
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(values)
    means: list[float] = []
    for _ in range(n_resamples):
        sample = [values[rng.randrange(n)] for _ in range(n)]
        means.append(statistics.fmean(sample))
    means.sort()
    lo_idx = int(n_resamples * (alpha / 2))
    hi_idx = int(n_resamples * (1 - alpha / 2)) - 1
    return (means[lo_idx], means[hi_idx])


def aggregate(records: Iterable[RunRecord]) -> Report:
    """Group records by full ``Cell`` identity before aggregation.

    Task version, prompt version, model snapshot, temperature, and rubric
    version all split cells so incompatible experiment variants cannot blend.
    """
    grouped: dict[
        tuple[str, str, str, str, str, str | None, float, str],
        list[RunRecord],
    ] = defaultdict(list)
    for record in records:
        key = (
            record.cell.task_id,
            record.cell.task_version,
            record.cell.prompt_id,
            record.cell.prompt_version,
            record.cell.model,
            record.cell.model_snapshot,
            record.cell.temperature,
            record.cell.rubric_version,
        )
        grouped[key].append(record)

    cells = [
        _aggregate_cell(
            task_id,
            task_version,
            prompt_id,
            prompt_version,
            model,
            model_snapshot,
            temperature,
            rubric_version,
            group,
        )
        for (
            task_id,
            task_version,
            prompt_id,
            prompt_version,
            model,
            model_snapshot,
            temperature,
            rubric_version,
        ), group in sorted(grouped.items(), key=_group_sort_key)
    ]
    return Report(cells=cells)


def _group_sort_key(
    item: tuple[
        tuple[str, str, str, str, str, str | None, float, str],
        list[RunRecord],
    ],
) -> tuple[str, str, str, str, str, str, float, str]:
    key, _records = item
    (
        task_id,
        task_version,
        prompt_id,
        prompt_version,
        model,
        snapshot,
        temperature,
        rubric,
    ) = key
    return (
        task_id,
        task_version,
        prompt_id,
        prompt_version,
        model,
        snapshot or "",
        temperature,
        rubric,
    )


def _aggregate_cell(
    task_id: str,
    task_version: str,
    prompt_id: str,
    prompt_version: str,
    model: str,
    model_snapshot: str | None,
    temperature: float,
    rubric_version: str,
    records: list[RunRecord],
) -> CellReport:
    outcome_counter: Counter[Outcome] = Counter()
    for r in records:
        outcome_counter[r.outcome] += 1
    outcomes: dict[Outcome, int] = {o: outcome_counter.get(o, 0) for o in _OUTCOMES}

    pass_flags: list[float] = [
        1.0 if (r.outcome == "code" and r.test_result and r.test_result.passed) else 0.0
        for r in records
    ]
    pass_rate = statistics.fmean(pass_flags) if pass_flags else 0.0
    pass_ci = bootstrap_ci(pass_flags)

    total_cost = sum(r.candidate_cost_usd for r in records) + sum(
        r.judge.cost_usd for r in records if r.judge is not None
    )

    code_metrics = [
        r.static_metrics
        for r in records
        if r.outcome == "code" and r.static_metrics is not None
    ]
    static_mean: dict[str, float] = {}
    if code_metrics:
        static_mean = {
            "loc": statistics.fmean(m.loc for m in code_metrics),
            "ruff_findings": statistics.fmean(m.ruff_findings for m in code_metrics),
            "cyclomatic_max": statistics.fmean(m.cyclomatic_max for m in code_metrics),
        }

    judged = [r for r in records if r.judge is not None]
    judge_mean: dict[str, float] = {}
    if judged:
        per_item: dict[str, list[float]] = defaultdict(list)
        for r in judged:
            assert r.judge is not None
            for item_id, value in r.judge.checklist.items():
                # bools become 0/1 (False/True); ints come through as-is.
                per_item[item_id].append(float(value))
        judge_mean = {k: statistics.fmean(v) for k, v in per_item.items()}

    return CellReport(
        task_id=task_id,
        task_version=task_version,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        model=model,
        model_snapshot=model_snapshot,
        temperature=temperature,
        rubric_version=rubric_version,
        n=len(records),
        outcomes=outcomes,
        pass_rate=pass_rate,
        pass_ci=pass_ci,
        total_cost_usd=total_cost,
        static_mean=static_mean,
        judge_mean=judge_mean,
        judge_coverage=len(judged),
    )


def render_text(report: Report) -> str:
    from jig.evals.prompt_style_eval.report_render import render_text as _render_text

    return _render_text(report)


def render_json(report: Report) -> str:
    from jig.evals.prompt_style_eval.report_render import render_json as _render_json

    return _render_json(report)
