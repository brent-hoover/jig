#!/usr/bin/env -S uv run python
"""Multi-reviewer code review orchestrator.

Spawns N Claude agents in parallel, each reviewing a codebase from a different
angle, then runs a synthesis agent to merge findings into per-module digests
and a top-level SUMMARY.md.

Usage:
    uv run python scripts/multi_review.py \\
        --repo-root . \\
        --scope "the jig/ Python package" \\
        --exclude "tests/, .claude/, .jig/, templates/, .review/, __pycache__/, .venv/"

Outputs land in <output-dir> (default ./.review/):
    01-overcomplexity.md ... 08-performance.md   per-reviewer reports
    by-module/<module>.md                         per-module digests
    SUMMARY.md                                    top-level summary
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import click
from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, ResultMessage, TextBlock


# ---------------------------------------------------------------------------
# Reviewer definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reviewer:
    name: str
    title: str
    output_filename: str
    focus: str  # what to look for + how to investigate
    severity_guide: str  # 3-4 lines: severity definitions


REVIEWERS: list[Reviewer] = [
    Reviewer(
        name="overcomplexity",
        title="Overcomplexity Review",
        output_filename="01-overcomplexity.md",
        focus=textwrap.dedent("""
            Code that could be simpler, smaller, or more localized:
            - Functions/classes/files that exist solely to delegate to one other thing
            - Premature abstraction — interfaces/protocols/base classes with one implementation
            - Code spread across 2+ files only used together
            - Wrapper classes with no behavior beyond passthrough
            - Excessive parameterization or unused configuration knobs
            - Multi-step pipelines where steps could be merged
            - Helper modules imported in only one place
            - Builder/factory patterns where direct construction is simpler
            - Layered try/except chains that could collapse
            - Optional parameters never set to a non-default
            - Code re-implementing stdlib or already-imported library functions

            How to investigate:
            - Read entry points first (CLI, package __init__) to map call graph
            - For each suspicious abstraction, count call sites with rg
            - Skip personal-style preferences; flag only objective overcomplexity
        """).strip(),
        severity_guide=(
            "HIGH: clear, multi-file overcomplexity actively harming maintainability\n"
            "MEDIUM: localized overcomplexity worth fixing in normal course of work\n"
            "LOW: minor; would-be-nice cleanups"
        ),
    ),
    Reviewer(
        name="dead-code",
        title="Dead Code Review",
        output_filename="02-dead-code.md",
        focus=textwrap.dedent("""
            What to look for:
            - Functions, methods, classes never referenced anywhere (after excluding tests)
            - Modules never imported by any other module
            - __all__ exports that aren't consumed
            - Unused imports, unreachable code, dead branches
            - Constants/enums never read
            - CLI commands not wired into any group
            - Event handlers never triggered
            - Commented-out code blocks left in source

            DO NOT flag as dead:
            - Functions invoked dynamically (getattr, importlib, dispatch tables, decorators)
            - Anything in package __init__ that's a public API surface
            - Pydantic model fields, dataclass fields (used by serialization)
            - Functions used in tests (count test usage even if tests are out of scope)

            How to investigate:
            - For each candidate run `rg "<symbol>"` across the WHOLE repo (incl. tests)
            - Check pyproject.toml / setup.cfg for entry_points and dynamic registration
            - Check YAML config files for symbol references

            Be conservative: false positives waste user time. Every finding needs a
            "call-site count" field. If you can't find a call site but it might be
            dynamic, set confidence to "low".
        """).strip(),
        severity_guide=(
            "HIGH: confident dead code, safe to delete (call-sites = 0, no dynamic dispatch concern)\n"
            "MEDIUM: likely dead but needs caller verification\n"
            "LOW: minor unused imports / single-use constants that could be inlined"
        ),
    ),
    Reviewer(
        name="partial-implementations",
        title="Partial Implementations Review",
        output_filename="03-partial-implementations.md",
        focus=textwrap.dedent("""
            Code that implements part of a feature but not all of it:
            - Functions raising NotImplementedError
            - TODO / FIXME / XXX / HACK comments in production code
            - Functions returning placeholder values (None, empty list, hard-coded "ok")
            - Branches that look intentional but do nothing (`if x: pass`)
            - Half-wired features: CLI flag exists but handler ignores it; config key
              defined but never read; model field set but never persisted
            - Commented-out code suggesting abandoned work
            - Public functions whose docstring claims behavior not actually implemented
            - Event publishers without subscribers, or vice versa
            - except: pass error handlers — partial because the error path is unimplemented
            - "Phase N" / "v1" / "for now" comments suggesting planned-but-not-done work
            - Unused parameters in handlers
            - Workflow steps in YAML with no Python handler (or vice versa)

            How to investigate:
            - rg -n "TODO|FIXME|XXX|HACK|NotImplementedError"
            - rg -n "for now|phase \\d|placeholder|stub" -i
            - rg -n "except.*:\\s*pass"
            - Check function docstrings against bodies — does body deliver what doc says?
            - Look at recent commits with "phase" or "wip" or "partial" in subject

            Distinguish "intentional minimal implementation" (fine) from "abandoned/
            incomplete" (a finding). Function-name + docstring + body together usually
            reveal intent.
        """).strip(),
        severity_guide=(
            "HIGH: partial implementation in a code path users will hit; could cause confusing failures\n"
            "MEDIUM: partial implementation in a non-critical path or behind a flag\n"
            "LOW: cosmetic TODOs, abandoned scaffolding in unused modules"
        ),
    ),
    Reviewer(
        name="comments",
        title="Comment Accuracy Review",
        output_filename="04-comments.md",
        focus=textwrap.dedent("""
            Do code comments and docstrings clearly and correctly explain how the code works?

            Find:
            - Stale comments — claim something the code no longer does
            - Wrong comments — factually incorrect about what the code does
            - Misleading comments — technically true but creating wrong mental model
            - Outdated references — referencing issues, tickets, paths that don't exist
            - Mismatched docstrings — params/returns don't match signature
            - Aspirational docstrings — describe intended behavior not implemented yet
            - Pervasive obvious comments (`i += 1  # increment i` style)
            - Tombstone comments — superseded TODOs, "removed X" notes, commented-out code

            DO NOT flag:
            - Style preferences (formatting, docstring style)
            - Missing comments where code is self-explanatory
            - Type hints (those are not comments)

            How to investigate:
            - Read each file's comments and docstrings against the surrounding code
            - For docstrings, check param names match signature, return type matches
              actual returns, raised exceptions match `raise` statements
            - For inline comments, verify the next 5-15 lines actually do what the
              comment claims
            - For module-level docstrings, check exports match documented purpose

            Every finding needs file:line and a quote of the offending comment.
        """).strip(),
        severity_guide=(
            "HIGH: comment that would actively mislead a reader trying to understand or modify the code\n"
            "MEDIUM: stale or imprecise but not actively dangerous\n"
            "LOW: minor cleanup, redundant comments"
        ),
    ),
    Reviewer(
        name="architecture",
        title="Architecture Review",
        output_filename="05-architecture.md",
        focus=textwrap.dedent("""
            Architectural concerns at the module/package level:
            - Module boundaries and coupling — cohesive responsibilities? doing too much?
            - Layering — clear separation between transport/IO, domain logic, orchestration?
            - Async correctness at architectural level — sync blocking inside async,
              missing cancellation handling, lifecycle leaks
            - State management — shared mutable state, singletons leaking
            - Error propagation across module boundaries
            - Circular dependencies, leaky abstractions
            - Public API surface — what's exposed vs internal?
            - Configuration coupling — does config leak into business logic?
            - Testability — modules hard to test in isolation
            - Concurrency hazards — shared mutable state across coroutines, races

            Focus on architectural concerns, NOT micro-style. The code-quality
            reviewer handles micro-style. Every finding needs file:line.
        """).strip(),
        severity_guide=(
            "HIGH: structural issue impacting maintainability, correctness, or scalability\n"
            "MEDIUM: notable design concern worth addressing in normal work\n"
            "LOW: minor structural cleanup"
        ),
    ),
    Reviewer(
        name="security",
        title="Security Review",
        output_filename="06-security.md",
        focus=textwrap.dedent("""
            Security audit. Distinguish defense-in-depth concerns from exploitable issues.

            What to evaluate (adapt to the project):
            - Command injection: subprocess with shell=True, unquoted args, f-string commands
            - Path traversal: file operations using untrusted paths without normalization
            - Credential handling: how are secrets passed, logged, stored?
            - Network bind addresses: 0.0.0.0 vs 127.0.0.1; auth on network endpoints
            - Input validation: pydantic / sanitization at trust boundaries
            - YAML loading: prefer safe_load over load
            - Unsafe deserialization (eval, exec, or unsafe object loaders on external data)
            - Logging: sensitive args or tokens leaking to logs
            - File permissions, sandbox configuration if applicable
            - Race conditions on shared state (TOCTOU)

            Every finding needs file:line and an attacker model — "an attacker who
            controls X can do Y".
        """).strip(),
        severity_guide=(
            "CRITICAL: directly exploitable, leads to RCE, sandbox escape, or credential compromise\n"
            "HIGH: exploitable with realistic preconditions\n"
            "MEDIUM: defense-in-depth weaknesses, hardening opportunities\n"
            "LOW: best-practice deviations with low real-world impact"
        ),
    ),
    Reviewer(
        name="code-quality",
        title="Code Quality Review",
        output_filename="07-code-quality.md",
        focus=textwrap.dedent("""
            Bugs, error handling, type safety, idiomatic style.
            (NOT architecture — separate reviewer; NOT comments — separate reviewer.)

            What to evaluate:
            - Latent bugs: off-by-one, None handling, race conditions, resource leaks,
              missed `await`s
            - Error handling: bare `except`, `except: pass`, swallowed exceptions,
              exceptions caught too broadly, error messages without context
            - Type safety: missing type hints on new code, Any overuse, mismatched hints
            - Async correctness: sync I/O in async (open(), requests.get, time.sleep,
              subprocess.run), missing `await`, asyncio.create_task without retaining
              reference (GC hazard), missing timeouts
            - Pydantic v2: BaseModel.dict() (deprecated), parse_obj (deprecated),
              `class Config` (should be `model_config`)
            - Logging: print() vs logger, f-string in logger.info(f"...") (should use lazy %s)
            - Resource management: missing context managers
            - Concurrency hazards: shared dicts/lists across coroutines without locks

            Skip pure style preferences a linter would catch. Every finding needs file:line.
        """).strip(),
        severity_guide=(
            "HIGH: latent bug or async/error-handling issue that could cause incorrect runtime behavior\n"
            "MEDIUM: code that works today but is fragile or violates conventions in load-bearing ways\n"
            "LOW: minor idioms, cosmetic improvements"
        ),
    ),
    Reviewer(
        name="performance",
        title="Performance Review",
        output_filename="08-performance.md",
        focus=textwrap.dedent("""
            Performance review. Focus on real concerns under realistic load — skip
            micro-optimizations.

            What to evaluate:
            - Sync I/O in async paths: open(), os.path.exists, subprocess.run,
              time.sleep, requests — these block the event loop
            - File I/O patterns: full reads where streaming would do; repeated reads
              of the same file with no caching; writes without batching
            - Collection access: O(N) scans on every query; missing in-memory caches
            - WebSocket / pub-sub fan-out: per-event JSON cost, message size
            - Subprocess spawning: per-message process spawn instead of per-session
            - Hot loops: comprehensions over large data; repeated work that could memoize
            - Pydantic model construction in hot paths
            - Async patterns: serial await chains where asyncio.gather would parallelize
            - Memory: unbounded queues, growing lists never pruned, leaky caches
            - Startup time: heavy imports at module level
            - Polling loops without sleep

            Every finding needs file:line and an estimated impact ("blocks event loop
            ~Nms per call; called per event").
        """).strip(),
        severity_guide=(
            "HIGH: degrades responsiveness under realistic load\n"
            "MEDIUM: scaling concern past current targets, or wasted work that compounds\n"
            "LOW: micro-inefficiency in cold paths"
        ),
    ),
]


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewContext:
    repo_root: Path
    output_dir: Path
    project_name: str
    project_description: str
    scope: str
    exclude: str
    model: str
    today: str = field(default_factory=lambda: date.today().isoformat())


_SCHEMA = """
```markdown
# {title}

**Reviewer**: {name}
**Date**: {today}
**Scope**: {scope}
**Files scanned**: <number>

## Summary
<2-4 sentences>

## Findings

### [HIGH] <Short title>
- **File**: `path/to/file.py:42-58`
- **Finding**: <what's wrong>
- **Suggested action**: <concrete fix>
- **Confidence**: high | medium | low

### [MEDIUM] ...
### [LOW] ...

## Statistics
- Total findings: N
- HIGH: N | MEDIUM: N | LOW: N
```
""".strip()


_REVIEWER_PROMPT_TEMPLATE = """
You are reviewing the **{project_name}** codebase for **{title}**.

{project_description}

**Working directory**: {repo_root}
**Scope**: {scope}
**Exclude**: {exclude}

## What to look for

{focus}

## Constraints

- **DO NOT edit any code.** Report only.
- Be specific: every finding must include `file:line` references.
- Prefer high-confidence findings over many weak ones.
- Don't flag personal-style preferences. Flag objective issues.

## Output

Write your report to `{output_path}` using EXACTLY this schema:

{schema}

## Severity guide

{severity_guide}

Aim for thorough coverage but don't pad with weak findings. 5-25 strong
findings is better than 50 weak ones.

When done, briefly summarize key findings in 5 lines or less. Do not paste
the full report into your reply — it's already on disk.
""".strip()


def build_reviewer_prompt(reviewer: Reviewer, ctx: ReviewContext) -> str:
    output_path = ctx.output_dir / reviewer.output_filename
    schema = _SCHEMA.format(
        title=reviewer.title,
        name=reviewer.name,
        today=ctx.today,
        scope=ctx.scope,
    )
    return _REVIEWER_PROMPT_TEMPLATE.format(
        project_name=ctx.project_name,
        project_description=ctx.project_description or "",
        title=reviewer.title,
        repo_root=ctx.repo_root,
        scope=ctx.scope,
        exclude=ctx.exclude,
        focus=reviewer.focus,
        output_path=output_path,
        schema=schema,
        severity_guide=reviewer.severity_guide,
    )


_SYNTHESIS_PROMPT = """
You are the **synthesis pass** for a multi-reviewer code review of **{project_name}**.

{n_reports} reviewer reports have been written to `{output_dir}/`:

{report_list}

## Your job

Read all reports, then produce two outputs.

### Output 1: per-module digests

Group findings by source-file or directory. Write one digest file per major
module to `{output_dir}/by-module/<module>.md`. Pick groupings based on the
codebase — combine related files when reasonable, give big files (e.g. an
orchestrator with 20+ findings) their own digest.

Each per-module digest:

```markdown
# <Module name>

## Severity counts
- CRITICAL: N | HIGH: N | MEDIUM: N | LOW: N
- Total: N findings across <list of reviewers>

## Findings (grouped by reviewer)

### <Reviewer name>
- [HIGH] [foo.py:42] short summary — see `05-architecture.md` finding #N
- ...

(only include sections that have findings)

## Hot spots
File:line ranges that appear in >=2 different reviewer reports.
```

### Output 2: top-level summary

Write `{output_dir}/SUMMARY.md`:

```markdown
# {project_name} Code Review — Summary

**Date**: {today}
**Scope**: {scope}
**Reviewers**: {n_reports}

## Headline numbers
| Reviewer | CRITICAL | HIGH | MEDIUM | LOW | Total |
| ... |

## Top 10 issues to fix first
Ranked. Each: 1-line description, file:line, which reviewer(s), rough effort (S/M/L).
Bias toward (a) HIGH severity, (b) flagged by multiple reviewers, (c) low-effort high-impact.

## Cross-cutting themes
3-6 themes across reviewers. Each: 1-2 sentences + 3-5 example findings with file:line.

## Hot-spot files
Files appearing in >=3 reviewer reports, ranked by total finding count.

| File | Findings | Reviewers | Top issue |
| ... |

## What's notable in a good way
Things multiple reviewers flagged as well-handled.

## Recommended next steps
3-5 actionable steps in priority order.
```

## Constraints

- **DO NOT edit any code outside `{output_dir}/`.**
- Do not invent findings or severities. Only report what's in the source files.
- Preserve original severity (HIGH/MEDIUM/LOW) from each reviewer.
- File:line refs must be exact — copy from the source reports.
- Keep summaries terse. Reader prefers diffs over prose.
- Create the `{output_dir}/by-module/` directory before writing files.

When done, reply with: (a) how many per-module digests you wrote, (b) total
finding count, (c) confirm SUMMARY.md is written, (d) the top 3 hot-spot
files. Keep your reply under 250 words.
""".strip()


def build_synthesis_prompt(ctx: ReviewContext, reports: list[Path]) -> str:
    report_list = "\n".join(f"- `{r.name}`" for r in reports)
    return _SYNTHESIS_PROMPT.format(
        project_name=ctx.project_name,
        n_reports=len(reports),
        output_dir=ctx.output_dir,
        report_list=report_list,
        today=ctx.today,
        scope=ctx.scope,
    )


# ---------------------------------------------------------------------------
# Agent driver
# ---------------------------------------------------------------------------


async def _run_agent(prompt: str, cwd: Path, model: str) -> str:
    """Run a single Claude agent to completion. Returns final assistant text."""
    options = ClaudeAgentOptions(
        cwd=str(cwd),
        allowed_tools=["Read", "Grep", "Glob", "Bash", "Write"],
        permission_mode="bypassPermissions",
        model=model,
    )
    final_text = ""
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content or []:
                if isinstance(block, TextBlock) and block.text:
                    final_text = block.text
        elif isinstance(message, ResultMessage):
            final_text = message.result or final_text
    return final_text


async def run_reviewer(
    reviewer: Reviewer, ctx: ReviewContext
) -> tuple[str, bool, str]:
    """Returns (name, success, message). On failure, message is the error."""
    prompt = build_reviewer_prompt(reviewer, ctx)
    started = time.monotonic()
    click.echo(f"  ▶ {reviewer.name}")
    try:
        summary = await _run_agent(prompt, ctx.repo_root, ctx.model)
    except Exception as exc:  # noqa: BLE001 — top-level orchestrator; record and continue
        elapsed = time.monotonic() - started
        click.echo(f"  ✗ {reviewer.name} ({elapsed:.0f}s): {exc}")
        return reviewer.name, False, str(exc)
    elapsed = time.monotonic() - started
    output_path = ctx.output_dir / reviewer.output_filename
    if not output_path.exists():
        click.echo(
            f"  ⚠ {reviewer.name} ({elapsed:.0f}s): finished without writing report"
        )
        return reviewer.name, False, "no report written"
    click.echo(f"  ✓ {reviewer.name} ({elapsed:.0f}s)")
    return reviewer.name, True, summary


async def run_synthesis(ctx: ReviewContext, reports: list[Path]) -> tuple[bool, str]:
    prompt = build_synthesis_prompt(ctx, reports)
    started = time.monotonic()
    click.echo("  ▶ synthesis")
    try:
        summary = await _run_agent(prompt, ctx.repo_root, ctx.model)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        click.echo(f"  ✗ synthesis ({elapsed:.0f}s): {exc}")
        return False, str(exc)
    elapsed = time.monotonic() - started
    summary_path = ctx.output_dir / "SUMMARY.md"
    if not summary_path.exists():
        click.echo(f"  ⚠ synthesis ({elapsed:.0f}s): SUMMARY.md not written")
        return False, "SUMMARY.md not written"
    click.echo(f"  ✓ synthesis ({elapsed:.0f}s)")
    return True, summary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def amain(
    ctx: ReviewContext,
    selected: list[Reviewer],
    parallelism: int,
    skip_synthesis: bool,
) -> int:
    ctx.output_dir.mkdir(parents=True, exist_ok=True)
    click.echo(
        f"Running {len(selected)} reviewer(s) on {ctx.repo_root} "
        f"(model={ctx.model}, parallelism={parallelism})"
    )

    sem = asyncio.Semaphore(parallelism)

    async def bounded(r: Reviewer) -> tuple[str, bool, str]:
        async with sem:
            return await run_reviewer(r, ctx)

    results = await asyncio.gather(*(bounded(r) for r in selected))
    failed = [(name, err) for name, ok, err in results if not ok]
    if failed:
        click.echo(f"\n{len(failed)} reviewer(s) failed:")
        for name, err in failed:
            click.echo(f"  - {name}: {err}")
        if len(failed) == len(selected):
            return 1

    if skip_synthesis:
        click.echo(f"\nReports in {ctx.output_dir}/. Synthesis skipped.")
        return 0 if not failed else 2

    reports = sorted(p for p in ctx.output_dir.glob("*.md") if p.name != "SUMMARY.md")
    if not reports:
        click.echo("\nNo reports to synthesize.")
        return 1

    click.echo("\nSynthesizing...")
    ok, _ = await run_synthesis(ctx, reports)
    if not ok:
        return 1

    click.echo(f"\nDone. See {ctx.output_dir}/SUMMARY.md")
    return 0 if not failed else 2


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--repo-root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path.cwd(),
    show_default="cwd",
    help="Codebase root.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Where reports land. Default: <repo-root>/.review",
)
@click.option(
    "--project-name",
    default=None,
    help="Project name shown in reports. Default: repo basename.",
)
@click.option(
    "--project-description",
    default="",
    help="Free-text project context (1-3 sentences). Helps reviewers calibrate.",
)
@click.option(
    "--scope",
    default="the whole project",
    help='What to scan, e.g. "the foo/ Python package".',
)
@click.option(
    "--exclude",
    default="tests/, .git/, node_modules/, __pycache__/, .venv/, dist/, build/",
    help="Comma-separated paths/patterns to skip.",
)
@click.option(
    "--model",
    default="claude-opus-4-7",
    show_default=True,
    help="Claude model id for reviewer + synthesis agents.",
)
@click.option(
    "--parallelism",
    type=int,
    default=8,
    show_default=True,
    help="Max concurrent reviewer agents.",
)
@click.option(
    "--reviewers",
    default=None,
    help=(
        "Comma-separated subset of reviewer names to run. "
        f"Default: all. Available: {', '.join(r.name for r in REVIEWERS)}"
    ),
)
@click.option(
    "--skip-synthesis",
    is_flag=True,
    help="Run reviewers only; skip the synthesis pass.",
)
def main(
    repo_root: Path,
    output_dir: Path | None,
    project_name: str | None,
    project_description: str,
    scope: str,
    exclude: str,
    model: str,
    parallelism: int,
    reviewers: str | None,
    skip_synthesis: bool,
) -> None:
    repo_root = repo_root.resolve()
    output_dir = (output_dir or repo_root / ".review").resolve()
    project_name = project_name or repo_root.name

    if reviewers:
        wanted = {n.strip() for n in reviewers.split(",") if n.strip()}
        unknown = wanted - {r.name for r in REVIEWERS}
        if unknown:
            click.echo(f"Unknown reviewer(s): {', '.join(sorted(unknown))}", err=True)
            sys.exit(2)
        selected = [r for r in REVIEWERS if r.name in wanted]
    else:
        selected = list(REVIEWERS)

    ctx = ReviewContext(
        repo_root=repo_root,
        output_dir=output_dir,
        project_name=project_name,
        project_description=project_description,
        scope=scope,
        exclude=exclude,
        model=model,
    )
    sys.exit(asyncio.run(amain(ctx, selected, parallelism, skip_synthesis)))


if __name__ == "__main__":
    main()
