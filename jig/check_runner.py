"""Check execution runners per doc 10 §Execution.

Phase 5 Task A: scripted checks. The catalog loader (Phase 2E)
already parses and validates ``.jig/checks.yaml``; this module runs
a ``ScriptedCheck`` in the ticket's worktree and persists a
``CheckResult`` record for the gating layer (Task D) to consume.

The runner is deliberately narrow: it turns a (check, worktree) pair
into a result. It does not decide what to do with the verdict —
severity gating, check_failure SystemEvents, and evaluator bounces
all live downstream. Re-run policy is also the caller's call:
``run_for_phase`` re-executes every declared check unconditionally;
selective re-runs are out of scope per doc 10 §Re-run policy.

Sandbox parity: scripted checks run in the ticket's worktree via a
shell subprocess. They inherit the orchestrator's environment today;
lifting them into the agent's sandbox image is a later task that
slots in at the same API boundary.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from jig.check_results import CheckResult
from jig.checks import CheckCatalog, CheckSeverity, ScriptedCheck
from jig.store.check_results import CheckResultsStore


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


__all__ = [
    "ScriptedRunner",
    "severity_for",
]
