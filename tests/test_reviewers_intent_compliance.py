"""Intent-layer enforcement reviewer (Track I MVP).

Per ``docs/agent-leverage/problem.md`` §1: a mechanical reviewer that
flags thin/boilerplate ``Intent`` blocks on authored artifacts. Three
deterministic checks (length, boilerplate-restatement, empty
complications) — no LLM. Cousin of ``ContractComplianceReviewer``: same
shape, same dispatch wiring.
"""
from __future__ import annotations

import pytest

from jig.intent import ComplicationsConsidered, Intent
from jig.reviewers.dispatch import BONES_REVIEWER_ID, select_reviewers_for_ticket
from jig.reviewers.comment import Severity
from jig.reviewers.intent_compliance import (
    INTENT_REVIEWER_ID,
    IntentComplianceReviewer,
    IntentCommentType,
    review_intent,
)
from jig.schemas.arch import (
    BehavioralContract,
    DataContract,
    Module,
    Risk,
    RiskImpact,
    RiskLikelihood,
    RiskStatus,
    TierHint,
)
from jig.schemas.plan import Epic, EpicLayers
from jig.ticket import Ticket, WorkType


# ---- helpers -------------------------------------------------------------


def _good_intent() -> Intent:
    return Intent(
        problem=(
            "The Shopify catalog ingestion needs deterministic "
            "deduplication when the same product arrives across "
            "multiple webhook events."
        ),
        simplest_solution=(
            "Hash the canonical SKU plus the source store id and "
            "skip inserts that collide with an existing row."
        ),
        complications_considered=ComplicationsConsidered(
            scale="Catalogs of 100k+ SKUs need a covering index on the dedup key.",
            concurrency="Two webhooks may race; use INSERT ON CONFLICT, not read-modify-write.",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )


def _module(intent: Intent | None = None) -> Module:
    return Module(
        id="catalog-ingest",
        title="Catalog Ingest",
        summary="Ingests product catalogs from external sources.",
        implements_capabilities=["shopify-connect"],
        tier_hint=TierHint.STANDARD,
        intent=intent or _good_intent(),
    )


def _data_contract(intent: Intent | None = None) -> DataContract:
    return DataContract(
        id="product-row",
        description="Product rows in the catalog table.",
        schema_ref="project://arch/contracts/shared/product-row",
        intent=intent or _good_intent(),
    )


def _behavioral_contract(intent: Intent | None = None) -> BehavioralContract:
    return BehavioralContract(
        id="dedup-on-insert",
        scope="every-insert",
        precondition="incoming product has canonical SKU",
        postcondition="catalog table contains exactly one row per (sku, source)",
        side_effect_required="catalog-ingested event published",
        intent=intent or _good_intent(),
    )


def _risk(intent: Intent | None = None) -> Risk:
    return Risk(
        id="dedup-key-collision",
        text="Canonical SKU may collide across different vendors.",
        impact=RiskImpact.MEDIUM,
        likelihood=RiskLikelihood.LOW,
        status=RiskStatus.SPIKE_PROPOSED,
        intent=intent or _good_intent(),
    )


def _epic(intent: Intent | None = None) -> Epic:
    return Epic(
        id="catalog-ingest",
        title="Catalog Ingest",
        suite="catalog",
        modules=["catalog-ingest"],
        layers=EpicLayers(),
        intent=intent or _good_intent(),
    )


# ---- length check --------------------------------------------------------


def test_flags_too_short_problem():
    intent = _good_intent().model_copy(update={"problem": "too short"})
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_TOO_SHORT.value in types
    # Severity is IMPORTANT so the dev/auth agent has to acknowledge.
    short = next(c for c in comments if c.type == IntentCommentType.INTENT_TOO_SHORT.value)
    assert short.severity == Severity.IMPORTANT.value
    assert short.reviewer == INTENT_REVIEWER_ID
    assert "problem" in short.prose.lower()
    # contract_uri pinpoints the offending artifact for the operator.
    assert short.contract_uri is not None
    assert "m-1" in short.contract_uri


def test_flags_too_short_simplest_solution():
    intent = _good_intent().model_copy(update={"simplest_solution": "do it"})
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert any(
        c.type == IntentCommentType.INTENT_TOO_SHORT.value
        and "simplest_solution" in c.prose
        for c in comments
    )


def test_passes_at_exactly_threshold():
    """A 20-char problem/simplest_solution is the inclusive boundary."""
    twenty = "x" * 20
    # Both fields exactly 20 chars; complications populated as N/A so
    # the empty-complications check doesn't fire alongside.
    intent = Intent(
        problem=twenty,
        simplest_solution=twenty,
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="none — N/A",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert all(c.type != IntentCommentType.INTENT_TOO_SHORT.value for c in comments)


# ---- boilerplate-restatement check ---------------------------------------


def test_flags_boilerplate_restatement():
    """``simplest_solution`` that just rephrases ``problem`` is the highest-signal
    failure mode per docs §1 ("agents jump to the elegant answer and skip
    simplification"). Token overlap > 50% catches the obvious cases.
    """
    intent = Intent(
        problem=(
            "We need to deduplicate incoming Shopify products by canonical SKU "
            "across multiple webhook events."
        ),
        simplest_solution=(
            "Deduplicate incoming Shopify products by canonical SKU across "
            "webhook events."
        ),
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="none — N/A",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_BOILERPLATE_RESTATEMENT.value in types


def test_does_not_flag_genuinely_distinct_solution():
    """Real simplest-solution prose has its own technical vocabulary."""
    comments = review_intent(_good_intent(), kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_BOILERPLATE_RESTATEMENT.value not in types


# ---- empty-complications check -------------------------------------------


def test_flags_all_none_complications():
    """All four canonical fields ``None`` and no extras → agent skipped the step.

    Legitimate "no complications apply" must be filled with explicit prose
    (e.g. ``"none — N/A"``) so the absence is intentional rather than
    unconsidered.
    """
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_COMPLICATIONS_SKIPPED.value in types


def test_extra_complication_keys_count_as_filled():
    """``extra='allow'`` on ComplicationsConsidered means the dev agent can
    add problem-specific complications. The presence of any extra key counts
    as "considered" even if all four canonicals are None.
    """
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered.model_validate(
            {"data_freshness": "Stale rows above 24h drop the consumer SLA."}
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert all(
        c.type != IntentCommentType.INTENT_COMPLICATIONS_SKIPPED.value
        for c in comments
    )


def test_at_least_one_canonical_filled_passes():
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(scale="needs covering index at 100k SKUs"),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert all(
        c.type != IntentCommentType.INTENT_COMPLICATIONS_SKIPPED.value
        for c in comments
    )


# ---- happy path ----------------------------------------------------------


def test_happy_path_returns_no_comments():
    comments = review_intent(_good_intent(), kind="Module", artifact_id="m-1")
    assert comments == []


# ---- reviewer class + per-artifact dispatch ------------------------------


@pytest.mark.asyncio
async def test_reviewer_handles_module():
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([_module()])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_handles_data_contract():
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([_data_contract()])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_handles_behavioral_contract():
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([_behavioral_contract()])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_handles_risk():
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([_risk()])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_handles_epic():
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([_epic()])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_skips_risk_without_intent():
    """Risk.intent is optional (only required for status >= spike_proposed).

    The reviewer must skip a Risk that legitimately has no intent rather than
    crashing or flagging — risks at status=open / mitigated don't need it.
    """
    open_risk = Risk(
        id="r-1",
        text="placeholder",
        impact=RiskImpact.LOW,
        likelihood=RiskLikelihood.LOW,
        status=RiskStatus.OPEN,
    )
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([open_risk])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_aggregates_comments_across_artifacts():
    """One reviewer call against multiple artifacts returns all comments,
    each labeled with the right contract_uri so the operator can locate
    the offending artifact.
    """
    bad_module = _module(
        intent=_good_intent().model_copy(update={"problem": "x"})
    )
    bad_dc = _data_contract(
        intent=_good_intent().model_copy(update={"simplest_solution": "y"})
    )
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([bad_module, bad_dc])
    # One INTENT_TOO_SHORT per bad artifact.
    short = [c for c in comments if c.type == IntentCommentType.INTENT_TOO_SHORT.value]
    assert len(short) == 2
    uris = {c.contract_uri for c in short}
    assert any("catalog-ingest" in (u or "") for u in uris)
    assert any("product-row" in (u or "") for u in uris)


# ---- bones dispatch wiring ----------------------------------------------


def _ticket(layer: str | None = "mvp", reviewer_set: list[str] | None = None) -> Ticket:
    return Ticket(
        id="t-1",
        work_type=WorkType.FEATURE,
        title="t",
        created_by="planner-pm",
        module_id="catalog-ingest",
        capability_ids=["shopify-connect"],
        layer=layer,
        reviewer_set=reviewer_set if reviewer_set is not None else [],
    )


def test_mvp_layer_default_includes_intent_reviewer():
    """``should_run_for_bones`` for MVP-layer tickets with no explicit
    reviewer_set returns both the contract and intent reviewers.

    Bones layer keeps just contract-compliance per the bones budget.
    MVP layer is where intent enforcement starts firing.
    """
    t = _ticket(layer="mvp", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert INTENT_REVIEWER_ID in ids
    assert BONES_REVIEWER_ID in ids


def test_final_layer_default_includes_intent_reviewer():
    t = _ticket(layer="final", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert INTENT_REVIEWER_ID in ids


def test_bones_layer_does_not_default_intent_reviewer():
    """Bones budget excludes intent-compliance.

    Bones default-on set = contract-compliance + cross-cutting-policy.
    Intent-layer enforcement starts at MVP per the bones budget; this
    test pins the negative side of that policy.
    """
    t = _ticket(layer="bones", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert INTENT_REVIEWER_ID not in ids
    assert BONES_REVIEWER_ID in ids


def test_explicit_reviewer_set_still_honored():
    """If the planner authored a reviewer_set, it wins (even on MVP)."""
    t = _ticket(layer="mvp", reviewer_set=["spec-compliance"])
    ids = select_reviewers_for_ticket(t)
    assert ids == ["spec-compliance"]
