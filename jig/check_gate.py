"""Handoff gating on check results (Phase 5 Task D).

The check runners (Task A scripted / Task B agent) turn a catalog
check into a ``CheckResult`` record. This module turns a batch of
those results into a gate verdict the orchestrator can act on:

* ``required`` severity + non-``pass`` verdict → failing gate
* ``warning`` severity → never gates; advisory only
* Missing results for a declared required check → failing gate
  (treat "never ran" as "didn't pass")
* Failures that carry an accepted Waiver (Task E) → don't gate

Side effect: one ``SystemEvent(event_type="check_failure")`` per
failing required check, posted to the thread store. These are what
agents see via ``read_comments`` when they come back to fix a
bounced handoff. The orchestrator (Task O) takes the verdict and
either spawns the evaluator (pass) or sends the ticket back to the
completing actor (fail) — that wiring isn't here.

The "did we already post an event for this check?" check is scoped
to the open handoff attempt: when a handoff is accepted/rejected we
consider the attempt closed and the next gate run is free to emit
new check_failure entries. Dedup is deliberately *not* cross-attempt
— a re-run that fails again deserves its own audit record.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel

from jig.check_results import CheckResult, CheckVerdict
from jig.checks import CheckCatalog, CheckSeverity
from jig.store.check_results import CheckResultsStore
from jig.store.threads import ThreadStore
from jig.thread import SystemEvent

_logger = logging.getLogger(__name__)


# Excerpt length for the SystemEvent body — full output stays in the
# CheckResult record. Tuned so the evaluator prompt stays readable
# when multiple checks fail; the agent can always pull the full log
# from the check_results store if it wants more.
_EXCERPT_TAIL_BYTES = 2048


class CheckFailureEntry(BaseModel):
    """One failing check paired with the SystemEvent id emitted for it."""

    check_name: str
    verdict: CheckVerdict
    severity: CheckSeverity
    event_id: str


class GateVerdict(BaseModel):
    """Outcome of a handoff-gate evaluation.

    ``passing`` is True when every declared required check has a
    latest result of ``verdict='pass'`` (or a non-pass result with an
    active waiver). ``failing`` lists the results that blocked the
    gate, pre-waiver. ``missing`` lists declared required checks that
    have no result record at all — treated as a fail for gating.
    ``posted_events`` lists the ``SystemEvent`` entries written by
    this run; empty when the gate passes or when dedupe skipped all
    entries.
    """

    passing: bool
    failing: list[CheckFailureEntry] = []
    missing: list[str] = []
    posted_events: list[str] = []
    mode: Literal["pre-spawn", "handoff"] = "handoff"


def _excerpt(output: str) -> str:
    """Trim the check output to a size that fits in a thread entry."""
    if len(output) <= _EXCERPT_TAIL_BYTES:
        return output
    return "...[trimmed]...\n" + output[-_EXCERPT_TAIL_BYTES:]


def _build_failure_content(result: CheckResult) -> str:
    """Human-readable body for the ``check_failure`` SystemEvent."""
    header = (
        f"Required check {result.check_name!r} did not pass "
        f"(verdict={result.verdict!r}, severity={result.severity.value!r})."
    )
    if not result.output.strip():
        return header
    return f"{header}\n\n{_excerpt(result.output)}"


async def _post_failure_event(
    *,
    threads: ThreadStore,
    result: CheckResult,
) -> str:
    """Write the ``check_failure`` SystemEvent for a failing result."""
    ev = SystemEvent(
        ticket_id=result.ticket_id,
        author="harness",
        event_type="check_failure",
        content=_build_failure_content(result),
        check_name=result.check_name,
        check_severity=result.severity.value,  # type: ignore[arg-type]
        check_verdict=result.verdict,
        excerpt=_excerpt(result.output),
        commit_sha=result.commit_sha or None,
    )
    return await threads.post(ev)


async def _latest_failure_waived_for_result(
    *,
    threads: ThreadStore,
    ticket_id: str,
    check_name: str,
    result: CheckResult,
) -> bool:
    """Does the existing waiver authorize *this specific* failing run?

    A waiver authorizes one concrete failure, not a check name for the
    lifetime of the ticket. We honor the waiver iff the most recent
    ``check_failure`` SystemEvent for ``check_name`` is marked
    ``waived=True`` *and* represents the same failing run as
    ``result`` (matched on ``commit_sha``). A re-run that produces a
    new failure on a different commit is treated as unwaived — the
    authorizer must waive it explicitly if they want the gate to clear.

    Conservative default: when either side is missing ``commit_sha``
    (legacy records, scripted checks that didn't capture it), we
    refuse to extend the waiver. Better to re-post and re-authorize
    than silently swallow a fresh failure.
    """
    if not result.commit_sha:
        return False
    entries = await threads.for_ticket(ticket_id)
    for entry in reversed(entries):
        if not isinstance(entry, SystemEvent):
            continue
        if entry.event_type != "check_failure":
            continue
        if entry.check_name != check_name:
            continue
        if not entry.waived:
            return False  # Newest is an unwaived failure — waiver expired.
        return entry.commit_sha == result.commit_sha
    return False


async def evaluate_handoff_gate(
    *,
    catalog: CheckCatalog,
    results: CheckResultsStore,
    threads: ThreadStore,
    ticket_id: str,
    phase: str,
    required_check_names: list[str],
    post_events: bool = True,
) -> GateVerdict:
    """Decide whether a handoff may advance to evaluator spawn.

    Looks at the *latest* result per check in ``required_check_names``.
    For each failing required check, emits one
    ``SystemEvent(event_type="check_failure")`` (unless
    ``post_events=False`` — used by the pre-spawn read-only check in
    ``check_gate_status`` where we just want the verdict).

    Unknown check names in ``required_check_names`` raise ``KeyError``
    with the offending name — catalogs and phase configs are validated
    at load time, so a mismatch here means the caller built the list
    wrong.
    """
    # Validate inputs loud so miswiring doesn't silently look like
    # a passing gate.
    for name in required_check_names:
        if catalog.get(name) is None:
            raise KeyError(f"check {name!r} not in catalog")

    latest_by_name: dict[str, CheckResult] = {}
    for r in await results.latest_batch(ticket_id, phase):
        latest_by_name[r.check_name] = r

    failing: list[CheckFailureEntry] = []
    missing: list[str] = []
    posted: list[str] = []

    for name in required_check_names:
        catalog_entry = catalog.get(name)
        # Catalog-level severity — so a check that ran once, then had
        # its severity flipped in the catalog, is scored by the current
        # declaration, not the stale one baked into the record.
        assert catalog_entry is not None
        if catalog_entry.severity != CheckSeverity.REQUIRED:
            continue

        result = latest_by_name.get(name)
        if result is None:
            missing.append(name)
            continue
        # Re-score using the catalog severity rather than the record's
        # stored severity — see note above.
        is_fail = result.verdict != "pass"
        if not is_fail:
            continue
        # Waiver correlates against the specific failing run, not the
        # check name. A re-run on a new commit needs its own waiver.
        if await _latest_failure_waived_for_result(
            threads=threads,
            ticket_id=ticket_id,
            check_name=name,
            result=result,
        ):
            continue

        event_id = ""
        if post_events:
            event_id = await _post_failure_event(threads=threads, result=result)
            posted.append(event_id)
        failing.append(
            CheckFailureEntry(
                check_name=name,
                verdict=result.verdict,
                severity=CheckSeverity.REQUIRED,
                event_id=event_id,
            )
        )

    passing = not failing and not missing
    return GateVerdict(
        passing=passing,
        failing=failing,
        missing=missing,
        posted_events=posted,
        mode="handoff",
    )


async def check_gate_status(
    *,
    catalog: CheckCatalog,
    results: CheckResultsStore,
    threads: ThreadStore,
    ticket_id: str,
    phase: str,
    required_check_names: list[str],
) -> GateVerdict:
    """Read-only gate check for "may the evaluator spawn right now?"

    Same logic as ``evaluate_handoff_gate`` but posts no events —
    used by the orchestrator's pre-spawn precondition and by the
    evaluator prompt builder when it needs to list "still-failing
    required checks" alongside the handoff.
    """
    verdict = await evaluate_handoff_gate(
        catalog=catalog,
        results=results,
        threads=threads,
        ticket_id=ticket_id,
        phase=phase,
        required_check_names=required_check_names,
        post_events=False,
    )
    return verdict.model_copy(update={"mode": "pre-spawn"})


__all__ = [
    "CheckFailureEntry",
    "GateVerdict",
    "check_gate_status",
    "evaluate_handoff_gate",
]
