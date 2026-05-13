"""Prompt-style eval CLI.

Three subcommands:

- ``run``: invoke the harness across cells until each has the requested
  sample count.
- ``report``: aggregate the persisted store into a per-cell comparison.
- ``rescore``: re-judge persisted code records with a different rubric
  version (deferred — plan step 13).
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from evals.prompt_style_eval import loaders, report
from evals.prompt_style_eval.models import Cell, Prompt, RunRecord, Task
from evals.prompt_style_eval.runner import run_cell
from evals.prompt_style_eval.store import Store

_DEFAULT_STORE = (
    Path(__file__).resolve().parent / "results" / "runs.jsonl"
)


@click.group()
def cli() -> None:
    """Compare hand-authored prompt variants on hidden-test tasks."""


@cli.command("run")
@click.option(
    "--task",
    "tasks",
    multiple=True,
    required=True,
    help="Task id (repeatable). E.g. --task todo_cli",
)
@click.option(
    "--prompt",
    "prompts",
    multiple=True,
    default=(),
    help="Prompt id (repeatable). If omitted, every prompt under the task is used.",
)
@click.option("--seeds", type=int, default=3, show_default=True,
              help="Target sample count per cell.")
@click.option("--model", default="claude-sonnet-4-6", show_default=True)
@click.option("--temperature", type=float, default=0.0, show_default=True)
@click.option("--judge-model", default="claude-opus-4-7", show_default=True)
@click.option("--rubric", default="v1", show_default=True)
@click.option("--concurrency", type=int, default=3, show_default=True)
@click.option("--force", is_flag=True,
              help="Ignore existing records; run --seeds fresh per cell.")
@click.option("--store-path", type=click.Path(path_type=Path), default=_DEFAULT_STORE,
              show_default=True)
def run_cmd(
    tasks: tuple[str, ...],
    prompts: tuple[str, ...],
    seeds: int,
    model: str,
    temperature: float,
    judge_model: str,
    rubric: str,
    concurrency: int,
    force: bool,
    store_path: Path,
) -> None:
    """Run cells until each has the requested sample count."""
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        raise click.ClickException(
            "CLAUDE_CODE_OAUTH_TOKEN is not set — run `claude setup-token` first "
            "or export the token before running an eval."
        )

    asyncio.run(
        _run_async(
            tasks=tasks,
            prompts=prompts,
            seeds=seeds,
            model=model,
            temperature=temperature,
            judge_model=judge_model,
            rubric_version=rubric,
            concurrency=concurrency,
            force=force,
            store_path=store_path,
        )
    )


async def _run_async(
    *,
    tasks: tuple[str, ...],
    prompts: tuple[str, ...],
    seeds: int,
    model: str,
    temperature: float,
    judge_model: str,
    rubric_version: str,
    concurrency: int,
    force: bool,
    store_path: Path,
) -> None:
    store = Store(store_path)
    rubric = loaders.load_rubric(rubric_version)

    # Resolve work: list[(task, prompt, cell, count_needed)]
    plan: list[tuple[Task, Prompt, Cell, int]] = []
    for task_id in tasks:
        task = loaders.load_task(task_id)
        prompt_ids = prompts if prompts else loaders.list_prompt_ids(task_id)
        if not prompt_ids:
            raise click.ClickException(f"task {task_id} has no prompts")
        for prompt_id in prompt_ids:
            prompt = loaders.load_prompt(task_id, prompt_id)
            cell = Cell(
                task_id=task_id,
                task_version=task.version,
                prompt_id=prompt_id,
                prompt_version=prompt.content_hash,
                model=model,
                model_snapshot=None,
                temperature=temperature,
                rubric_version=rubric_version,
            )
            existing = 0 if force else store.count_matching(cell)
            needed = max(0, seeds - existing)
            plan.append((task, prompt, cell, needed))

    total_runs = sum(n for *_, n in plan)
    click.echo(
        f"Plan: {len(plan)} cell(s), {total_runs} new run(s) "
        f"(seeds={seeds}, concurrency={concurrency})"
    )
    for task, prompt, cell, needed in plan:
        existing_msg = "" if force else f" (existing: {seeds - needed})"
        click.echo(
            f"  {cell.task_id} / {cell.prompt_id}: {needed} runs{existing_msg}"
        )
    if total_runs == 0:
        click.echo("nothing to do.")
        return

    semaphore = asyncio.Semaphore(concurrency)
    completed = {"n": 0, "cost": 0.0}

    async def _one_run(task, prompt, cell):
        async with semaphore:
            tests_dir = loaders.task_tests_dir(task.id)
            fixtures = loaders.task_fixture_paths(task)
            record = await run_cell(
                cell=cell,
                prompt_text=prompt.text,
                task=task,
                tests_dir=tests_dir,
                rubric=rubric,
                judge_model=judge_model,
                fixtures=fixtures,
            )
            await store.append(record)
            completed["n"] += 1
            cost = record.candidate_cost_usd + (
                record.judge.cost_usd if record.judge else 0.0
            )
            completed["cost"] += cost
            tag = _outcome_tag(record)
            click.echo(
                f"  [{completed['n']}/{total_runs}] "
                f"{cell.task_id}/{cell.prompt_id} → {tag} "
                f"(${cost:.4f}, running ${completed['cost']:.4f})"
            )

    coros = [
        _one_run(task, prompt, cell)
        for task, prompt, cell, needed in plan
        for _ in range(needed)
    ]
    await asyncio.gather(*coros)
    click.echo(f"done. {completed['n']} runs, ${completed['cost']:.4f} total.")


def _outcome_tag(record: RunRecord) -> str:
    outcome = record.outcome
    if outcome == "code":
        passed = record.test_result and record.test_result.passed
        return "code PASS" if passed else "code FAIL"
    return outcome


@cli.command("report")
@click.option("--task", "tasks", multiple=True, default=(),
              help="Filter to these task ids.")
@click.option("--prompt", "prompts", multiple=True, default=(),
              help="Filter to these prompt ids.")
@click.option("--rubric", default=None,
              help="Filter to this rubric version (omit for all).")
@click.option("--format", "fmt", type=click.Choice(["text", "json"]), default="text")
@click.option("--store-path", type=click.Path(path_type=Path), default=_DEFAULT_STORE,
              show_default=True)
def report_cmd(
    tasks: tuple[str, ...],
    prompts: tuple[str, ...],
    rubric: str | None,
    fmt: str,
    store_path: Path,
) -> None:
    """Aggregate persisted runs and print a comparison."""
    store = Store(store_path)
    records: list[RunRecord] = []
    for record in store.read_all():
        if tasks and record.cell.task_id not in tasks:
            continue
        if prompts and record.cell.prompt_id not in prompts:
            continue
        if rubric is not None and record.cell.rubric_version != rubric:
            continue
        records.append(record)

    report_obj = report.aggregate(records)
    rendered = (
        report.render_json(report_obj) if fmt == "json" else report.render_text(report_obj)
    )
    sys.stdout.write(rendered)
    if not rendered.endswith("\n"):
        sys.stdout.write("\n")


@cli.command("rescore")
def rescore_cmd() -> None:
    """Re-judge persisted code against a different rubric version. (deferred — plan step 13)"""
    raise click.ClickException("not yet implemented (plan step 13)")
