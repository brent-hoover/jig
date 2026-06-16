from __future__ import annotations

import json
from collections import defaultdict

from jig.evals.prompt_style_eval.report import CellReport, Report


def render_text(report: Report) -> str:
    if not report.cells:
        return "no records to report\n"

    out: list[str] = []
    by_task: dict[tuple[str, str], list[CellReport]] = defaultdict(list)
    for cell in report.cells:
        by_task[(cell.task_id, cell.task_version)].append(cell)

    for (task_id, task_version), cells in sorted(by_task.items()):
        out.append(f"Task: {task_id} ({task_version})")
        out.append("-" * 72)
        for cell in cells:
            out.extend(_render_cell(cell))
            out.append("")
        out.append("")
    return "\n".join(out)


def _render_cell(cell: CellReport) -> list[str]:
    short_ver = (
        cell.prompt_version[7:15]
        if cell.prompt_version.startswith("sha256:")
        else cell.prompt_version[:8]
    )
    out: list[str] = []
    snapshot = f" snapshot={cell.model_snapshot}" if cell.model_snapshot else ""
    out.append(
        f"  {cell.prompt_id} @ {short_ver}  "
        f"[{cell.model}{snapshot} temp={cell.temperature:g}]  "
        f"(n={cell.n}, cost=${cell.total_cost_usd:.4f}, "
        f"judge cov={cell.judge_coverage}/{cell.n})"
    )
    outcome_str = (
        "  ".join(f"{k}={v}" for k, v in cell.outcomes.items() if v > 0) or "-"
    )
    out.append(f"    outcomes:  {outcome_str}")
    out.append(
        f"    pass rate: {_pct(cell.pass_rate)}  "
        f"[95% CI: {_pct(cell.pass_ci[0])} - {_pct(cell.pass_ci[1])}]"
    )
    if cell.static_mean:
        out.append(
            "    static:    "
            f"LoC={cell.static_mean['loc']:.1f}  "
            f"ruff={cell.static_mean['ruff_findings']:.1f}  "
            f"cc_max={cell.static_mean['cyclomatic_max']:.1f}"
        )
    if cell.judge_mean:
        bits = "  ".join(f"{k}={v:.2f}" for k, v in cell.judge_mean.items())
        out.append(f"    judge:     {bits}")
    return out


def _pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


def render_json(report: Report) -> str:
    return json.dumps(report.to_dict(), indent=2)
