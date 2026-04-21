"""Check execution runners per doc 10 §Execution.

Phase 5 Task A: scripted checks. The catalog loader (Phase 2E)
already parses and validates ``.jig/checks.yaml``; this module runs
a ``ScriptedCheck`` in the ticket's worktree and persists a
``CheckResult`` record for the gating layer (Task D) to consume.

Phase 5 Task B: agent checks. ``AgentCheckRunner`` spawns a short-
lived Claude agent per ``implementation_aware_agent`` /
``black_box_agent`` check. Context URIs resolve through the usual
context-bundle pipeline; the agent sees the rubric as its prompt
and records a verdict via a scoped ``check_verdict`` MCP tool.

The runner is deliberately narrow: it turns a (check, worktree) pair
into a result. It does not decide what to do with the verdict —
severity gating, check_failure SystemEvents, and evaluator bounces
all live downstream. Re-run policy is also the caller's call:
``run_for_phase`` re-executes every declared check unconditionally;
selective re-runs are out of scope per doc 10 §Re-run policy.

Sandbox parity: scripted checks run in the ticket's worktree via a
shell subprocess. Agent checks run through the SDK with an empty
MCP surface (just ``check_verdict``). Both slot into the capability-
policy hook boundary (Task F/G) without API changes.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
from datetime import datetime, timezone
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, query

from jig.check_mcp import create_check_mcp_server
from jig.check_results import CheckResult
from jig.checks import (
    BlackBoxAgentCheck,
    CheckCatalog,
    CheckSeverity,
    ImplementationAwareAgentCheck,
    ScriptedCheck,
)
from jig.context_resolver import resolve_context_uris
from jig.store.check_results import CheckResultsStore
from jig.store.threads import ThreadStore
from jig.ticket import Ticket

_logger = logging.getLogger(__name__)


# Stdout+stderr are combined and capped so a runaway check can't
# produce JSONL records that choke the store reader. The tail is what
# downstream cares about; the leading bytes stay too so an evaluator
# sees context.
_MAX_OUTPUT_BYTES = 64 * 1024


async def _git_head(cwd: Path) -> str:
    """Return the HEAD sha for ``cwd`` or empty string if not a git dir."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "HEAD",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
    except (FileNotFoundError, OSError):
        pass
    return ""


def _trim_output(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= _MAX_OUTPUT_BYTES:
        return text
    head = text[: _MAX_OUTPUT_BYTES // 2]
    tail = text[-_MAX_OUTPUT_BYTES // 2 :]
    return f"{head}\n...[trimmed]...\n{tail}"


class ScriptedRunner:
    """Execute ``ScriptedCheck`` entries from a ``CheckCatalog``.

    Parameters
    ----------
    catalog:
        Loaded ``CheckCatalog`` — the check names referenced in
        ``automated_checks`` must resolve here.
    results:
        ``CheckResultsStore`` that each run appends to.
    worktree_path:
        Ticket's worktree root. Each check's ``working_dir`` is
        resolved relative to this path.
    author:
        Recorded on each ``CheckResult``. Defaults to ``"harness"``
        because scripted runs aren't attributed to an agent.
    """

    def __init__(
        self,
        *,
        catalog: CheckCatalog,
        results: CheckResultsStore,
        worktree_path: Path,
        author: str = "harness",
    ) -> None:
        self._catalog = catalog
        self._results = results
        self._worktree = worktree_path
        self._author = author

    async def run_check(
        self,
        *,
        ticket_id: str,
        phase: str,
        check_name: str,
    ) -> CheckResult:
        """Run a single scripted check and persist its result.

        Raises ``KeyError`` if ``check_name`` isn't in the catalog
        and ``TypeError`` if it resolves to a non-scripted check —
        agent checks run through ``AgentCheckRunner`` (Task B).
        """
        check = self._catalog.get(check_name)
        if check is None:
            raise KeyError(f"check {check_name!r} not in catalog")
        if not isinstance(check, ScriptedCheck):
            raise TypeError(
                f"check {check_name!r} is {check.type!r}; "
                "ScriptedRunner only handles 'scripted'"
            )

        cwd = (self._worktree / check.working_dir).resolve()
        commit_sha = await _git_head(self._worktree)
        started_at = datetime.now(timezone.utc)

        verdict: str
        output: str
        # Commands come from project-owned .jig/checks.yaml — shell
        # interpretation is intentional so entries like ``pytest -q``
        # or ``cd sub && make`` work. Invoked via ``/bin/sh -c`` (exec
        # path, not string-to-shell) so the contract is explicit.
        try:
            proc = await asyncio.create_subprocess_exec(
                "/bin/sh",
                "-c",
                check.command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as exc:
            finished_at = datetime.now(timezone.utc)
            result = CheckResult(
                ticket_id=ticket_id,
                phase=phase,
                author=self._author,
                check_name=check_name,
                check_type="scripted",
                verdict="error",
                severity=check.severity,
                output=f"spawn failed: {exc}",
                started_at=started_at,
                finished_at=finished_at,
                commit_sha=commit_sha,
            )
            await self._results.post(result)
            return result

        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=check.timeout_s
            )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                stdout, _ = await proc.communicate()
            except Exception:
                stdout = b""
            verdict = "timeout"
            output = _trim_output(stdout) if stdout else ""
        else:
            output = _trim_output(stdout or b"")
            verdict = "pass" if proc.returncode == 0 else "fail"

        finished_at = datetime.now(timezone.utc)
        result = CheckResult(
            ticket_id=ticket_id,
            phase=phase,
            author=self._author,
            check_name=check_name,
            check_type="scripted",
            verdict=verdict,  # type: ignore[arg-type]
            severity=check.severity,
            output=output,
            started_at=started_at,
            finished_at=finished_at,
            commit_sha=commit_sha,
        )
        await self._results.post(result)
        return result

    async def run_for_phase(
        self,
        *,
        ticket_id: str,
        phase: str,
        check_names: list[str],
    ) -> list[CheckResult]:
        """Run every scripted check named for a phase.

        Non-scripted names are skipped (agent checks dispatch
        elsewhere); unknown names raise. Re-run-all semantics:
        callers drive this on rejected handoff per doc 10.
        """
        results: list[CheckResult] = []
        for name in check_names:
            check = self._catalog.get(name)
            if check is None:
                raise KeyError(f"check {name!r} not in catalog")
            if not isinstance(check, ScriptedCheck):
                continue
            results.append(
                await self.run_check(
                    ticket_id=ticket_id, phase=phase, check_name=name
                )
            )
        return results


def severity_for(
    catalog: CheckCatalog, check_name: str
) -> CheckSeverity | None:
    """Look up a check's severity without running it.

    The gating layer (Task D) needs this to decide whether a fail
    blocks phase advance; exposing it here keeps the "checks live in
    the catalog" invariant.
    """
    check = catalog.get(check_name)
    return None if check is None else check.severity


# ---- Agent checks (Task B) ---------------------------------------------


_AgentCheck = ImplementationAwareAgentCheck | BlackBoxAgentCheck


def _matches_excluded(uri: str, excluded: list[str]) -> bool:
    """True when ``uri`` resolves to a path blocked by ``excluded``.

    Only ``repo://`` URIs can hit excluded paths today — other
    schemes (``ticket://``, ``role://``, ``project://``) resolve
    through curated artifacts that never overlap with source tree
    globs. Patterns use shell-style globs matched against the URI
    body (the ``repo://`` prefix is stripped before comparison).
    """
    if not excluded:
        return False
    if not uri.startswith("repo://"):
        return False
    body = uri[len("repo://") :]
    return any(fnmatch.fnmatch(body, pat) for pat in excluded)


async def _resolve_check_context(
    check: _AgentCheck,
    *,
    ticket: Ticket,
    parent: Ticket | None,
    threads: ThreadStore,
    worktree_path: Path,
    project_path: Path,
) -> str:
    """Resolve a check's context URIs, dropping any that hit ``excluded``.

    Black-box checks list paths the agent must not see; we filter
    those at resolution time so no text from those paths ever lands
    in the prompt. Hook-level path denies (Task F/G) are the belt-
    and-suspenders — this filter is the suspender.
    """
    excluded = getattr(check, "excluded", [])
    filtered: list[str] = []
    for uri in check.context:
        if _matches_excluded(uri, excluded):
            _logger.warning(
                "context URI %s excluded by check policy; skipping", uri
            )
            continue
        filtered.append(uri)
    return await resolve_context_uris(
        filtered,
        ticket=ticket,
        parent=parent,
        threads=threads,
        worktree_path=worktree_path,
        project_path=project_path,
    )


def _build_check_prompt(
    check: _AgentCheck,
    *,
    ticket: Ticket,
    resolved_context: str,
) -> str:
    """Compose the initial prompt from template + resolved context.

    The template is the rubric the catalog author wrote; we surface
    ticket identity and the verdict-recording instruction alongside
    it so the agent can't miss the tool call.
    """
    parts = [
        "# Verification check",
        "",
        check.template.strip(),
        "",
        f"**Ticket:** {ticket.id} — {ticket.title}",
        "",
    ]
    if resolved_context:
        parts.extend([resolved_context.strip(), ""])
    parts.extend([
        "When finished, call the `check_verdict` tool exactly once "
        "with a verdict of `pass` or `fail` and 1–3 sentences of "
        "reasoning. Do not emit a verdict any other way.",
    ])
    return "\n".join(parts)


class AgentCheckRunner:
    """Execute ``ImplementationAwareAgentCheck`` / ``BlackBoxAgentCheck``.

    Spawns a short-lived Claude agent per check. The agent sees the
    rubric (``template``), resolved context URIs, and a single MCP
    tool (``check_verdict``). It runs until it calls the tool or the
    configured ``timeout_s`` elapses — timeouts are recorded as
    ``verdict=timeout`` and the agent is killed.

    Parameters mirror ``ScriptedRunner`` plus the plumbing needed to
    resolve ticket-scoped context (threads, parent ticket, project
    path).
    """

    def __init__(
        self,
        *,
        catalog: CheckCatalog,
        results: CheckResultsStore,
        worktree_path: Path,
        project_path: Path,
        threads: ThreadStore,
        author: str = "check-agent",
    ) -> None:
        self._catalog = catalog
        self._results = results
        self._worktree = worktree_path
        self._project_path = project_path
        self._threads = threads
        self._author = author

    async def run_check(
        self,
        *,
        ticket: Ticket,
        phase: str,
        check_name: str,
        parent: Ticket | None = None,
    ) -> CheckResult:
        check = self._catalog.get(check_name)
        if check is None:
            raise KeyError(f"check {check_name!r} not in catalog")
        if not isinstance(
            check, (ImplementationAwareAgentCheck, BlackBoxAgentCheck)
        ):
            raise TypeError(
                f"check {check_name!r} is {check.type!r}; "
                "AgentCheckRunner handles agent checks only"
            )

        resolved_context = await _resolve_check_context(
            check,
            ticket=ticket,
            parent=parent,
            threads=self._threads,
            worktree_path=self._worktree,
            project_path=self._project_path,
        )
        initial_prompt = _build_check_prompt(
            check, ticket=ticket, resolved_context=resolved_context
        )

        captured = {"verdict": None, "reasoning": "", "call_count": 0}
        mcp_server = create_check_mcp_server(captured)

        # Black-box: only the scoped MCP tool — no filesystem reads.
        # Implementation-aware: read/search over the worktree so the
        # rubric can cite specific lines. Neither can write, bash, or
        # invoke other agents. Hook-level excluded-path denies (Task
        # F/G) layer on top without touching this list.
        if isinstance(check, BlackBoxAgentCheck):
            allowed_tools: list[str] = ["mcp__jig_check__check_verdict"]
        else:
            allowed_tools = [
                "mcp__jig_check__check_verdict",
                "Read",
                "Grep",
                "Glob",
            ]

        options = ClaudeAgentOptions(
            cwd=str(self._worktree),
            allowed_tools=allowed_tools,
            system_prompt=check.template,
            mcp_servers={"jig_check": mcp_server},
            permission_mode="bypassPermissions",
        )

        started_at = datetime.now(timezone.utc)
        commit_sha = await _git_head(self._worktree)
        timed_out = False
        spawn_error: str | None = None

        async def _drive() -> None:
            async for _msg in query(
                prompt=initial_prompt, options=options
            ):
                # Stop as soon as the verdict is recorded — the agent
                # may keep talking after the tool call, but the
                # runner only cares about the first verdict and has
                # no need to consume more turns once captured.
                if captured["call_count"] > 0:
                    break

        try:
            await asyncio.wait_for(_drive(), timeout=check.timeout_s)
        except asyncio.TimeoutError:
            timed_out = True
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "agent check %s crashed", check_name
            )
            spawn_error = f"{type(exc).__name__}: {exc}"

        finished_at = datetime.now(timezone.utc)

        if timed_out:
            verdict = "timeout"
            output = "agent did not record a verdict before timeout"
        elif spawn_error is not None:
            verdict = "error"
            output = spawn_error
        elif captured["call_count"] == 0:
            verdict = "error"
            output = "agent exited without calling check_verdict"
        else:
            verdict = captured["verdict"]  # type: ignore[assignment]
            output = captured.get("reasoning") or ""

        result = CheckResult(
            ticket_id=ticket.id,
            phase=phase,
            author=self._author,
            check_name=check_name,
            check_type=check.type,  # type: ignore[arg-type]
            verdict=verdict,  # type: ignore[arg-type]
            severity=check.severity,
            output=_trim_output(output.encode("utf-8", errors="replace")),
            started_at=started_at,
            finished_at=finished_at,
            commit_sha=commit_sha,
        )
        await self._results.post(result)
        return result

    async def run_for_phase(
        self,
        *,
        ticket: Ticket,
        phase: str,
        check_names: list[str],
        parent: Ticket | None = None,
    ) -> list[CheckResult]:
        """Run every agent check named for a phase.

        Scripted names are skipped (handled by ``ScriptedRunner``);
        unknown names raise. Same re-run-all contract as the scripted
        runner — callers drive it per doc 10.
        """
        results: list[CheckResult] = []
        for name in check_names:
            check = self._catalog.get(name)
            if check is None:
                raise KeyError(f"check {name!r} not in catalog")
            if not isinstance(
                check, (ImplementationAwareAgentCheck, BlackBoxAgentCheck)
            ):
                continue
            results.append(
                await self.run_check(
                    ticket=ticket,
                    phase=phase,
                    check_name=name,
                    parent=parent,
                )
            )
        return results


__all__ = [
    "AgentCheckRunner",
    "ScriptedRunner",
    "severity_for",
]
