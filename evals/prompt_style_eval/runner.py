"""End-to-end runner: one cell, one ``RunRecord``.

``run_cell`` is the integration point: it invokes the SDK, classifies the
outcome, and — when the candidate produced code — runs the hidden tests,
computes static metrics, and asks the judge. Failures along the way are
surfaced as outcomes rather than exceptions; the store gets a record either
way so re-running can top up to the target sample count.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.prompt_style_eval import classify, metrics, sandbox
from evals.prompt_style_eval.classify import (
    extract_assistant_text,
    extract_files,
)
from evals.prompt_style_eval.judge import JudgeError
from evals.prompt_style_eval.judge import score as judge_score
from evals.prompt_style_eval.models import Cell, Rubric, RunRecord, Task
from evals.prompt_style_eval.sdk import invoke

_logger = logging.getLogger(__name__)


async def run_cell(
    cell: Cell,
    prompt_text: str,
    task: Task,
    tests_dir: Path,
    rubric: Rubric,
    judge_model: str,
    *,
    fixtures: list[Path] | None = None,
) -> RunRecord:
    """Produce one ``RunRecord`` for the given cell + prompt.

    The function never raises — every failure mode becomes a recorded
    outcome. SDK / transport exceptions yield ``outcome="error"``; judge
    failures yield ``outcome="code"`` with ``judge=None``.
    """
    run_id = str(uuid.uuid4())
    timestamp = datetime.now(UTC)

    base: dict[str, Any] = {
        "run_id": run_id,
        "timestamp": timestamp,
        "cell": cell,
        "prompt": prompt_text,
    }

    try:
        invocation = await invoke(
            prompt_text,
            model=cell.model,
            temperature=cell.temperature,
        )
    except Exception:
        _logger.exception("SDK invocation failed for run %s", run_id)
        return RunRecord(
            outcome="error",
            transcript=[],
            candidate_tokens={},
            candidate_cost_usd=0.0,
            **base,
        )

    candidate_fields = {
        "transcript": invocation.transcript,
        "candidate_tokens": invocation.tokens,
        "candidate_cost_usd": invocation.cost_usd,
    }
    outcome = classify.classify(invocation.transcript)

    if outcome != "code":
        return RunRecord(outcome=outcome, **base, **candidate_fields)

    text = extract_assistant_text(invocation.transcript) or ""
    files = extract_files(text, default_filename=task.entrypoint)
    if not files:
        # Classifier said "code" but extraction came up empty — treat as
        # malformed rather than crashing.
        return RunRecord(outcome="malformed", **base, **candidate_fields)

    test_result = await sandbox.run_tests(files, task, tests_dir, fixtures=fixtures)

    # Static metrics: judge the entrypoint file (the candidate's main artifact
    # for single-file tasks; for multi-file tasks the entrypoint is the most
    # interesting single file to measure, even if the entire candidate is
    # bigger).
    entrypoint_source = files.get(task.entrypoint) or next(iter(files.values()))
    static_metrics = await metrics.compute(entrypoint_source)

    # Judge: same — use the entrypoint as the representative artifact for
    # the quality rubric. (If the judge should later see all files, we'd
    # build a combined string here.)
    try:
        judge = await judge_score(entrypoint_source, rubric, model=judge_model)
    except JudgeError as exc:
        _logger.warning("Judge failed for run %s: %s", run_id, exc)
        judge = None

    return RunRecord(
        outcome="code",
        extracted_files=files,
        test_result=test_result,
        static_metrics=static_metrics,
        judge=judge,
        **base,
        **candidate_fields,
    )
