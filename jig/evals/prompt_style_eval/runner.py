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

from jig.evals.prompt_style_eval import classify, metrics, sandbox
from jig.evals.prompt_style_eval.classify import (
    extract_assistant_text,
    extract_files,
)
from jig.evals.prompt_style_eval.judge import JudgeError
from jig.evals.prompt_style_eval.judge import score as judge_score
from jig.evals.prompt_style_eval.models import Cell, Rubric, RunRecord, Task
from jig.evals.prompt_style_eval.sdk import invoke

_logger = logging.getLogger(__name__)


def _score_files(files: dict[str, str], task: Task) -> dict[str, str]:
    names = task.score_files or [task.entrypoint]
    if task.score_files:
        return {
            name: files.get(name, _missing_score_file_source(name))
            for name in task.score_files
        }
    selected = {name: files[name] for name in names if name in files}
    if selected:
        return selected
    return {next(iter(files)): next(iter(files.values()))}


def _missing_score_file_source(filename: str) -> str:
    return f'raise NotImplementedError("Missing required score file: {filename}")\n'


def _judge_source(files: dict[str, str]) -> str:
    blocks: list[str] = []
    for filename, source in files.items():
        blocks.append(f"# File: {filename}\n{source.rstrip()}")
    return "\n\n".join(blocks)


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

    scored_files = _score_files(files, task)
    static_metrics = await metrics.compute_files(scored_files)
    judge_source = _judge_source(scored_files)

    try:
        judge = await judge_score(judge_source, rubric, model=judge_model)
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
