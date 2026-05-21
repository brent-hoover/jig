"""Static metrics on candidate-generated code: LoC, ruff findings, cyclomatic.

These are the objective half of the quality signal — the half that doesn't
go through an LLM judge. Reported alongside (not collapsed into) the judge
score so neither can launder the other.
"""

from __future__ import annotations

import asyncio
import json

from radon.complexity import cc_visit

from jig.evals.prompt_style_eval.models import StaticMetrics


def count_loc(code: str) -> int:
    """Non-blank, non-comment lines.

    Bare comments don't count. A line that has code followed by a trailing
    comment counts (the code is still there)."""
    n = 0
    for raw in code.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            continue
        n += 1
    return n


def max_cyclomatic(code: str) -> int:
    """Largest cyclomatic complexity across all functions/methods.

    Returns 0 if the snippet has no functions/methods or if it doesn't parse;
    a syntax error elsewhere in the eval pipeline will already have flagged
    the run as ``code`` with a failing test suite.
    """
    try:
        blocks = cc_visit(code)
    except SyntaxError:
        return 0
    if not blocks:
        return 0
    return max(block.complexity for block in blocks)


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
