"""Estimation calibration loop (Track F Final).

Per ``docs/v2.0/pm-workflow/design.md`` §"Estimation calibration":

> Once the project has run a handful of tickets, observed cycles feed
> back into Planner PM's estimation prompts. The metrics that matter:
>
> - **Turns** (primary)
> - **Tool calls** (secondary)
> - **Tokens** (tertiary, cost forecasting only)
>
> Wall-clock is deliberately skipped — too noisy, too model-dependent.
>
> Calibration loop: every N completed tickets, the analytics layer
> computes the actual turn / tool-call distribution per tier per S/M/L
> bucket and emits a ``EstimationCalibrationUpdated`` event.

This module ships:

- ``CalibrationSample`` — one observation row.
- ``CalibrationStore`` — JSONL append-only at
  ``.jig/plan/calibration.jsonl``.
- ``record_completion_sample`` — extracts a sample from the live
  ticket + analytics + agent-completion outcome and persists it.
- ``Envelope`` + ``current_envelopes`` — computes per-size median +
  p90 envelopes for turns / cost / duration with the shipped defaults
  as the fallback when the per-size sample count is below
  ``MIN_SAMPLES_FOR_CALIBRATION``.

Mechanical only — no LLM. Aggregation is on-demand at the call site
(no background recompute thread).
"""
from __future__ import annotations

import json
import logging
import statistics
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import EstimationCalibrationUpdated, ToolCalled
from jig.analytics.store import AnalyticsStore
from jig.atomic import atomic_write_text
from jig.ticket import Size, Ticket

__all__ = [
    "CALIBRATION_RELPATH",
    "CalibrationSample",
    "CalibrationStore",
    "DEFAULT_ENVELOPES",
    "Envelope",
    "MIN_SAMPLES_FOR_CALIBRATION",
    "current_envelopes",
    "record_completion",
    "record_completion_sample",
]


_logger = logging.getLogger(__name__)


CALIBRATION_RELPATH = Path(".jig") / "plan" / "calibration.jsonl"

# Below this per-size sample count, we fall back to the shipped
# defaults rather than computing envelopes from too-few samples.
# Aligns with the design's "every N completed tickets" cadence — the
# first few completions don't get to drive calibration on their own.
MIN_SAMPLES_FOR_CALIBRATION: int = 5

# Threshold: emit ``EstimationCalibrationUpdated`` when an envelope's
# median or p90 shifts more than this fraction from the prior values.
# The default of 0.20 (20%) is conservative — tiny noise won't fire,
# meaningful drift will.
ENVELOPE_SHIFT_THRESHOLD: float = 0.20


_CompletionStatus = Literal[
    "success", "failed", "blocked", "timeout", "killed"
]


class CalibrationSample(BaseModel):
    """One observation row in ``.jig/plan/calibration.jsonl``."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: str
    size: str  # ``Size`` value: "xs" | "s" | "m" | "l" | "xl"
    dev_tier: str = "standard"
    layer: str | None = None  # bones | mvp | final | None
    observed_turns: int = 0
    observed_tool_calls: int = 0
    observed_duration_ms: int = 0
    observed_cost_usd: float = 0.0
    completion_status: _CompletionStatus = "success"
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class Envelope(BaseModel):
    """Per-size envelope summarizing observed effort."""

    model_config = ConfigDict(extra="forbid")

    size: str
    sample_count: int
    median_turns: float
    p90_turns: float
    median_cost_usd: float
    p90_cost_usd: float
    median_duration_ms: float
    p90_duration_ms: float


# Shipped defaults — derived from the design table's "starter values"
# for the standard tier. Used when per-size samples are scarce so the
# Coordinator + Planner have a sensible envelope to fall back on.
DEFAULT_ENVELOPES: dict[str, Envelope] = {
    Size.XS.value: Envelope(
        size=Size.XS.value,
        sample_count=0,
        median_turns=10.0,
        p90_turns=15.0,
        median_cost_usd=0.10,
        p90_cost_usd=0.30,
        median_duration_ms=60_000.0,
        p90_duration_ms=180_000.0,
    ),
    Size.S.value: Envelope(
        size=Size.S.value,
        sample_count=0,
        median_turns=10.0,
        p90_turns=15.0,
        median_cost_usd=0.20,
        p90_cost_usd=0.60,
        median_duration_ms=120_000.0,
        p90_duration_ms=300_000.0,
    ),
    Size.M.value: Envelope(
        size=Size.M.value,
        sample_count=0,
        median_turns=25.0,
        p90_turns=40.0,
        median_cost_usd=1.0,
        p90_cost_usd=3.0,
        median_duration_ms=420_000.0,
        p90_duration_ms=900_000.0,
    ),
    Size.L.value: Envelope(
        size=Size.L.value,
        sample_count=0,
        median_turns=60.0,
        p90_turns=100.0,
        median_cost_usd=4.0,
        p90_cost_usd=10.0,
        median_duration_ms=1_200_000.0,
        p90_duration_ms=2_400_000.0,
    ),
    Size.XL.value: Envelope(
        size=Size.XL.value,
        sample_count=0,
        median_turns=120.0,
        p90_turns=200.0,
        median_cost_usd=8.0,
        p90_cost_usd=20.0,
        median_duration_ms=2_400_000.0,
        p90_duration_ms=4_800_000.0,
    ),
}


# ---- store ---------------------------------------------------------------


class CalibrationStore:
    """JSONL append-only calibration sample store.

    Lives at ``<project_root>/.jig/plan/calibration.jsonl``. Mirrors
    the deferred-queue pattern in ``jig.coordinator``: small file,
    rewritten atomically on append. Volume is bounded by completed-
    ticket count.
    """

    def __init__(self, project_root: Path) -> None:
        self._project_root = project_root
        self._samples: list[CalibrationSample] = []
        self._loaded = False

    @property
    def path(self) -> Path:
        return self._project_root / CALIBRATION_RELPATH

    async def load(self) -> None:
        """Load samples from disk into memory.

        Idempotent — multiple ``load`` calls re-read the file. Missing
        file → empty list (the JSONL hasn't been seeded yet).
        """
        path = self.path
        if not path.is_file():
            self._samples = []
            self._loaded = True
            return
        rows: list[CalibrationSample] = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(CalibrationSample.model_validate_json(line))
            except Exception:
                _logger.warning(
                    "skipping malformed calibration row: %r", line
                )
        self._samples = rows
        self._loaded = True

    async def append(self, sample: CalibrationSample) -> None:
        """Append one sample atomically."""
        if not self._loaded:
            await self.load()
        self._samples.append(sample)
        payload = "\n".join(
            json.dumps(s.model_dump(mode="json"), sort_keys=True)
            for s in self._samples
        )
        if payload:
            payload += "\n"
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, payload)

    def all(self) -> list[CalibrationSample]:
        """Return the in-memory sample list (call ``load`` first)."""
        return list(self._samples)


# ---- sample extraction --------------------------------------------------


async def record_completion_sample(
    *,
    ticket: Ticket,
    status: str,
    duration_ms: int,
    cost_usd: float | None,
    tokens_in: int | None,
    tokens_out: int | None,
    store: CalibrationStore,
    analytics: AnalyticsStore | None = None,
    emitter: EventEmitter | None = None,
) -> CalibrationSample:
    """Extract + persist one calibration sample from a finished agent run.

    Approximates ``observed_turns`` from the agent's tool-call count
    (one tool call ≈ one turn) when the analytics store is available;
    falls back to 0 otherwise. ``observed_tool_calls`` is the same
    underlying count.

    When the new sample materially shifts a per-size envelope (median
    or p90 moves more than ``ENVELOPE_SHIFT_THRESHOLD``), an
    ``EstimationCalibrationUpdated`` event fires.
    """
    del tokens_in, tokens_out  # tokens captured in AgentCompleted; unused here
    size = (ticket.size.value if isinstance(ticket.size, Size)
            else (ticket.size or Size.M.value))
    dev_tier = ticket.dev_tier or "standard"

    # Pre-compute prior envelopes so we can detect a meaningful shift
    # after appending the new sample.
    prior_envelopes = current_envelopes(store)

    tool_calls = 0
    if analytics is not None:
        # The agent_id convention in orchestrator is ``"<role>:<ticket-prefix>"``
        # — match by ticket_id field on tool events isn't guaranteed,
        # but ToolCalled doesn't carry ticket_id. We approximate by
        # filtering on agent_id matching this ticket's recent spawn.
        spawned = await analytics.by_kind("agent_spawned")
        agent_ids = {
            getattr(e, "agent_id", None)
            for e in spawned
            if getattr(e, "ticket_id", None) == ticket.id
        }
        if agent_ids:
            tool_events = await analytics.by_kind("tool_called")
            tool_calls = sum(
                1 for e in tool_events
                if isinstance(e, ToolCalled) and e.agent_id in agent_ids
            )

    sample = CalibrationSample(
        ticket_id=ticket.id,
        size=size,
        dev_tier=dev_tier,
        layer=ticket.layer,
        observed_turns=tool_calls,
        observed_tool_calls=tool_calls,
        observed_duration_ms=int(max(0, duration_ms)),
        observed_cost_usd=float(cost_usd or 0.0),
        completion_status=status if status in {
            "success", "failed", "blocked", "timeout", "killed",
        } else "failed",  # type: ignore[arg-type]
    )
    await store.append(sample)

    # Recompute envelopes; emit if an envelope materially shifted.
    if emitter is not None:
        new_envelopes = current_envelopes(store)
        if _envelope_shifted(
            prior_envelopes.get(size), new_envelopes.get(size)
        ):
            emitter.emit_nowait(
                EstimationCalibrationUpdated(
                    sample_size=len(store.all()),
                    bands=_envelopes_to_bands(new_envelopes),
                )
            )

    return sample


# Backwards-compatible alias matching the deliverable's spec name.
record_completion = record_completion_sample


# ---- envelope computation ------------------------------------------------


def current_envelopes(
    store: CalibrationStore,
    *,
    project_root: Path | None = None,
) -> dict[str, Envelope]:
    """Compute per-size envelopes from observed samples.

    For each size the store has at least ``MIN_SAMPLES_FOR_CALIBRATION``
    samples for, computes median + p90 over turns / cost / duration.
    Sizes with fewer samples fall back to the shipped defaults so
    callers always get a complete map.

    ``project_root`` is accepted for API symmetry (a future variant
    might cross-reference architecture tier hints) but isn't used for
    the mechanical median/p90 path.
    """
    del project_root
    by_size: dict[str, list[CalibrationSample]] = {}
    for sample in store.all():
        # Successful samples only — failed/blocked are skewed toward
        # the long tail and would bias the envelope upward.
        if sample.completion_status != "success":
            continue
        by_size.setdefault(sample.size, []).append(sample)

    out: dict[str, Envelope] = {}
    for size, default in DEFAULT_ENVELOPES.items():
        observed = by_size.get(size, [])
        if len(observed) < MIN_SAMPLES_FOR_CALIBRATION:
            out[size] = default
            continue
        out[size] = _envelope_from_samples(size, observed)

    # Surface any size we don't ship a default for (e.g. a custom value)
    # so consumers see all observed sizes, not just the defaults.
    for size, observed in by_size.items():
        if size in out:
            continue
        if len(observed) < MIN_SAMPLES_FOR_CALIBRATION:
            continue
        out[size] = _envelope_from_samples(size, observed)
    return out


def _envelope_from_samples(
    size: str, samples: list[CalibrationSample]
) -> Envelope:
    turns = sorted(s.observed_turns for s in samples)
    costs = sorted(s.observed_cost_usd for s in samples)
    durations = sorted(s.observed_duration_ms for s in samples)
    return Envelope(
        size=size,
        sample_count=len(samples),
        median_turns=float(statistics.median(turns)),
        p90_turns=float(_percentile(turns, 90)),
        median_cost_usd=float(statistics.median(costs)),
        p90_cost_usd=float(_percentile(costs, 90)),
        median_duration_ms=float(statistics.median(durations)),
        p90_duration_ms=float(_percentile(durations, 90)),
    )


def _percentile(values: list[float] | list[int], pct: int) -> float:
    """Compute the ``pct``th percentile.

    Uses linear interpolation between the two surrounding indices so
    small samples don't ratchet around a single observation.
    """
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    rank = (pct / 100.0) * (len(values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(values) - 1)
    frac = rank - lo
    return float(values[lo] + (values[hi] - values[lo]) * frac)


def _envelope_shifted(prior: Envelope | None, current: Envelope | None) -> bool:
    """True iff median or p90 turns / cost / duration moved >threshold.

    Treats absence of prior (first calibration computation) as a shift
    so the first emit fires. Same for absence of current (defensive;
    shouldn't happen under normal flow).
    """
    if prior is None or current is None:
        return True
    fields = (
        "median_turns", "p90_turns",
        "median_cost_usd", "p90_cost_usd",
        "median_duration_ms", "p90_duration_ms",
    )
    for field in fields:
        prior_val = getattr(prior, field)
        cur_val = getattr(current, field)
        if prior_val == 0 and cur_val == 0:
            continue
        if prior_val == 0:
            return True
        delta = abs(cur_val - prior_val) / max(abs(prior_val), 1e-9)
        if delta >= ENVELOPE_SHIFT_THRESHOLD:
            return True
    return False


def _envelopes_to_bands(
    envelopes: dict[str, Envelope]
) -> dict[str, dict[str, dict[str, list[float]]]]:
    """Flatten the envelope dict into the analytics event's nested shape.

    The event schema's ``bands`` is
    ``{tier: {S/M/L: {turns: [low, high], tool_calls: [low, high]}}}``.
    For Final scope we report a single tier slot ("standard") because
    the calibration store keys by size + tier but the per-tier split
    isn't yet meaningful with the volumes Final operators see — the
    aggregate-by-size view is what Planner reads.
    """
    bands: dict[str, dict[str, list[float]]] = {}
    for size, env in envelopes.items():
        bands[size.upper()] = {
            "turns": [env.median_turns, env.p90_turns],
            "tool_calls": [env.median_turns, env.p90_turns],
            "cost_usd": [env.median_cost_usd, env.p90_cost_usd],
            "duration_ms": [env.median_duration_ms, env.p90_duration_ms],
        }
    return {"standard": bands}



def serialize_envelopes_for_cli(
    envelopes: dict[str, Envelope]
) -> dict[str, dict[str, Any]]:
    """Flat dict suitable for json.dumps in CLI output."""
    return {
        size: env.model_dump(mode="json")
        for size, env in envelopes.items()
    }
