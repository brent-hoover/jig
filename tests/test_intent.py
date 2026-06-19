"""Tests for the v2 intent layer (problem / simplest / complications)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.intent import ComplicationsConsidered, Intent


def test_intent_minimum_valid():
    i = Intent(
        problem="batch ingest must be atomic",
        simplest_solution="single transaction wrapping all writes",
    )
    assert i.problem == "batch ingest must be atomic"
    assert i.simplest_solution == "single transaction wrapping all writes"
    # complications_considered defaults to all-None
    assert i.complications_considered.scale is None
    assert i.complications_considered.concurrency is None


def test_intent_with_complications():
    i = Intent(
        problem="P",
        simplest_solution="S",
        complications_considered=ComplicationsConsidered(
            scale="batches up to 100k SKUs",
            concurrency="multiple shops ingest simultaneously",
            failure_modes="partial network failures mid-batch",
            cross_cutting="audit log row per batch",
        ),
    )
    c = i.complications_considered
    assert c.scale == "batches up to 100k SKUs"
    assert c.concurrency.startswith("multiple")


def test_intent_rejects_empty_problem():
    with pytest.raises(ValidationError):
        Intent(problem="", simplest_solution="S")


def test_intent_rejects_empty_simplest():
    with pytest.raises(ValidationError):
        Intent(problem="P", simplest_solution="")


def test_complications_extra_keys_allowed():
    """Problem-specific complications beyond the four canonical keys."""
    c = ComplicationsConsidered(
        scale="x",
        cross_cutting="y",
        # design says "plus problem-specific complications as needed"
        idempotency="retries must not duplicate writes",
        observability="every batch emits a metric",
    )
    dump = c.model_dump()
    assert dump["idempotency"] == "retries must not duplicate writes"
    assert dump["observability"] == "every batch emits a metric"


def test_intent_round_trips_through_dict():
    i = Intent(
        problem="P",
        simplest_solution="S",
        complications_considered=ComplicationsConsidered(scale="x"),
    )
    blob = i.model_dump()
    i2 = Intent.model_validate(blob)
    assert i2 == i
