"""Static metrics on candidate-generated code: LoC, ruff findings, cyclomatic.

These are the objective half of the quality signal — the half that doesn't
go through an LLM judge. Reported alongside (not collapsed into) the judge
score so neither can launder the other.
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Mapping

# Canonical definitions live in jig.code_metrics (one radon call site for the
# whole codebase); re-exported here for the eval harness's existing callers.
from jig.code_metrics import count_loc, max_cyclomatic
from jig.evals.prompt_style_eval.models import StaticMetrics

__all__ = ["compute", "compute_files", "count_loc", "max_cyclomatic", "run_ruff"]


async def run_ruff(code: str) -> tuple[int, dict[str, int]]:
    """Run ``ruff check --output-format=json`` against the snippet via stdin.

    Returns ``(total_findings, breakdown_by_rule_code)``. Ruff exits non-zero
    when findings are present — that's data, not failure — so we don't check
    ``returncode``. If ruff fails to even produce JSON (e.g. internal error),
    we let ``json.loads`` raise rather than papering over it.
    """
    proc = await asyncio.create_subprocess_exec(
        "ruff",
        "check",
        "--output-format=json",
        "--stdin-filename",
        "snippet.py",
        "-",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, _stderr_b = await proc.communicate(input=code.encode("utf-8"))
    raw = stdout_b.decode("utf-8", errors="replace").strip()
    if not raw:
        return 0, {}
    findings = json.loads(raw)
    breakdown: dict[str, int] = {}
    for finding in findings:
        rule = finding.get("code") or "unknown"
        breakdown[rule] = breakdown.get(rule, 0) + 1
    return len(findings), breakdown


async def compute(code: str) -> StaticMetrics:
    """Compute all three signals for one candidate snippet."""
    findings, breakdown = await run_ruff(code)
    return StaticMetrics(
        loc=count_loc(code),
        ruff_findings=findings,
        ruff_breakdown=breakdown,
        cyclomatic_max=max_cyclomatic(code),
    )


async def compute_files(files: Mapping[str, str]) -> StaticMetrics:
    per_file = await asyncio.gather(*(compute(source) for source in files.values()))
    breakdown: Counter[str] = Counter()
    for item in per_file:
        breakdown.update(item.ruff_breakdown)
    return StaticMetrics(
        loc=sum(item.loc for item in per_file),
        ruff_findings=sum(item.ruff_findings for item in per_file),
        ruff_breakdown=dict(breakdown),
        cyclomatic_max=max((item.cyclomatic_max for item in per_file), default=0),
    )
