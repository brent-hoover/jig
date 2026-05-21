"""Intent-layer enforcement reviewer (Track I MVP).

Per ``docs/v2.0/agent-leverage/problem.md`` §1: a mechanical reviewer that
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
from tests._test_ticket import TICKET_AC_PLACEHOLDER, EPIC_AC_PLACEHOLDER_BULLET


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
        # Schema-level invariant (Block A.1): risks past ``open`` must
        # declare the dependent_contracts the cascade workflow inspects.
        dependent_contracts=[
            "project://arch/modules/catalog-ingest/contracts#data_contracts/dedup-key",
        ],
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
        acceptance_criteria=[EPIC_AC_PLACEHOLDER_BULLET],
    )


# ---- length check --------------------------------------------------------


def test_flags_too_short_problem():
    intent = _good_intent().model_copy(update={"problem": "too short"})
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_TOO_SHORT.value in types
    # Severity is IMPORTANT so the dev/auth agent has to acknowledge.
    short = next(
        c for c in comments if c.type == IntentCommentType.INTENT_TOO_SHORT.value
    )
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
        c.type != IntentCommentType.INTENT_COMPLICATIONS_SKIPPED.value for c in comments
    )


def test_at_least_one_canonical_filled_passes():
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(
            scale="needs covering index at 100k SKUs"
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert all(
        c.type != IntentCommentType.INTENT_COMPLICATIONS_SKIPPED.value for c in comments
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
    bad_module = _module(intent=_good_intent().model_copy(update={"problem": "x"}))
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
        description=TICKET_AC_PLACEHOLDER,
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
    """If the planner authored a reviewer_set, it's honored; judgment
    defaults are appended on top."""
    from jig.reviewers.dispatch import _JUDGMENT_DEFAULTS

    t = _ticket(layer="mvp", reviewer_set=["spec-compliance"])
    ids = select_reviewers_for_ticket(t)
    assert "spec-compliance" in ids
    for jid in _JUDGMENT_DEFAULTS:
        assert jid in ids


# ---- Final-scope: citation density --------------------------------------


def test_flags_complications_without_citations():
    """All four canonical fields filled but with abstract prose
    containing no concrete references → INTENT_NO_CITATIONS.
    """
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(
            scale="lots more",
            concurrency="some race",
            failure_modes="oops happens",
            cross_cutting="might apply",
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_NO_CITATIONS.value in types


def test_passes_when_complications_carry_concrete_references():
    """Even one citation-shaped token in the complications prose
    satisfies the citation-density check.
    """
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(
            scale="needs covering index on `dedup_key`",
            concurrency="see catalog-ingest module",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_NO_CITATIONS.value not in types


def test_no_citation_check_when_all_fields_explicit_none():
    """``none — N/A`` everywhere → empty-complications/citation checks
    BOTH skip (the operator explicitly said no complications apply).
    """
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="none — N/A",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_NO_CITATIONS.value not in types


def test_citation_check_recognizes_project_uri():
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(
            scale="see project://arch/modules/catalog-ingest/contracts",
            concurrency=None,
            failure_modes=None,
            cross_cutting=None,
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert all(c.type != IntentCommentType.INTENT_NO_CITATIONS.value for c in comments)


def test_citation_check_recognizes_file_path():
    intent = Intent(
        problem=_good_intent().problem,
        simplest_solution=_good_intent().simplest_solution,
        complications_considered=ComplicationsConsidered(
            scale="see jig/quartermaster.py for the pattern",
            concurrency=None,
            failure_modes=None,
            cross_cutting=None,
        ),
    )
    comments = review_intent(intent, kind="Module", artifact_id="m-1")
    assert all(c.type != IntentCommentType.INTENT_NO_CITATIONS.value for c in comments)


# ---- Final-scope: cross-artifact uniqueness -----------------------------


@pytest.mark.asyncio
async def test_flags_duplicate_intent_across_artifacts():
    """Two artifacts with identical (problem, simplest_solution) tuples
    each get a duplicate-intent comment (except the first, which is the
    "original" the duplicates point at).
    """
    same = _good_intent()
    a = _module(intent=same)
    b = _data_contract(intent=same)
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([a, b])
    types = [c.type for c in comments]
    assert IntentCommentType.INTENT_DUPLICATE_ACROSS_ARTIFACTS.value in types


@pytest.mark.asyncio
async def test_uniqueness_normalizes_whitespace_and_case():
    """Differences in whitespace and case do NOT save a duplicate."""
    intent_a = Intent(
        problem="The problem statement is HERE  with  extra spaces.",
        simplest_solution="Do the simplest thing possible.",
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="none — N/A",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )
    intent_b = Intent(
        problem="the problem statement is here with extra spaces.",
        simplest_solution="do the simplest thing possible.",
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="none — N/A",
            failure_modes="none — N/A",
            cross_cutting="none — N/A",
        ),
    )
    a = _module(intent=intent_a)
    b = _data_contract(intent=intent_b)
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([a, b])
    assert any(
        c.type == IntentCommentType.INTENT_DUPLICATE_ACROSS_ARTIFACTS.value
        for c in comments
    )


@pytest.mark.asyncio
async def test_uniqueness_does_not_flag_distinct_artifacts():
    """Distinct (problem, simplest_solution) tuples never trigger the
    uniqueness check even when artifacts are otherwise similar."""
    a = _module()
    distinct = Intent(
        problem=(
            "Frontend authentication needs a token-refresh hook so the "
            "user stays logged in across long-running sessions."
        ),
        simplest_solution=(
            "On 401 from the API, call /auth/refresh once and retry the "
            "original request before bubbling the error."
        ),
        complications_considered=ComplicationsConsidered(
            scale="none — N/A",
            concurrency="multiple in-flight 401s should refresh once",
            failure_modes="refresh failure forces logout",
            cross_cutting="csrf token needs refresh too",
        ),
    )
    b = _data_contract(intent=distinct)
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([a, b])
    assert all(
        c.type != IntentCommentType.INTENT_DUPLICATE_ACROSS_ARTIFACTS.value
        for c in comments
    )


# ---- Final-scope: project-wide review_project pass ----------------------


@pytest.mark.asyncio
async def test_review_project_walks_arch_modules_and_contracts(tmp_path):
    """``review_project`` loads architecture.yaml + per-module
    contracts.yaml + the build plan + frontend spec and runs the full
    intent pass across every Intent-bearing artifact."""
    from pathlib import Path

    import yaml as yaml_mod

    from jig.schemas.arch import (
        Architecture,
        BehavioralContract,
        ContractsFile,
        Module as ArchModule,
        TierHint as ArchTierHint,
    )

    project_root: Path = tmp_path
    (project_root / ".jig" / "spec" / "modules" / "m1").mkdir(parents=True)

    bad_intent = _good_intent().model_copy(update={"problem": "x"})
    arch = Architecture(
        modules=[
            ArchModule(
                id="m1",
                title="m1",
                summary="m1 summary",
                tier_hint=ArchTierHint.STANDARD,
                intent=bad_intent,
            )
        ],
    )
    (project_root / ".jig" / "spec" / "architecture.yaml").write_text(
        yaml_mod.safe_dump(arch.model_dump(mode="json"), sort_keys=False)
    )
    bc = BehavioralContract(
        id="bc-1",
        scope="every-call",
        precondition="incoming",
        postcondition="outgoing",
        intent=_good_intent(),
    )
    cf = ContractsFile(
        spec_version=1,
        module="m1",
        behavioral_contracts=[bc],
    )
    (project_root / ".jig" / "spec" / "modules" / "m1" / "contracts.yaml").write_text(
        yaml_mod.safe_dump(cf.model_dump(mode="json"), sort_keys=False)
    )

    reviewer = IntentComplianceReviewer()
    by_uri = await reviewer.review_project(project_root)
    # The bad module's URI carries comments; the BC's URI may not.
    assert any("m1" in uri for uri in by_uri)
    # All comments are tagged with the intent reviewer.
    for comments in by_uri.values():
        for c in comments:
            assert c.reviewer == INTENT_REVIEWER_ID


@pytest.mark.asyncio
async def test_review_project_handles_no_artifacts(tmp_path):
    """Empty project root → empty mapping; no exceptions raised."""
    reviewer = IntentComplianceReviewer()
    by_uri = await reviewer.review_project(tmp_path)
    assert by_uri == {}


@pytest.mark.asyncio
async def test_review_project_includes_uniqueness_check(tmp_path):
    """The project-wide pass routes through ``review_artifacts`` so the
    cross-artifact uniqueness check fires across files."""
    from pathlib import Path

    import yaml as yaml_mod

    from jig.schemas.arch import (
        Architecture,
        Module as ArchModule,
        TierHint as ArchTierHint,
    )

    project_root: Path = tmp_path
    (project_root / ".jig" / "spec").mkdir(parents=True)

    same = _good_intent()
    arch = Architecture(
        modules=[
            ArchModule(
                id="m1",
                title="m1",
                summary="m1 summary",
                tier_hint=ArchTierHint.STANDARD,
                intent=same,
            ),
            ArchModule(
                id="m2",
                title="m2",
                summary="m2 summary",
                tier_hint=ArchTierHint.STANDARD,
                intent=same,
            ),
        ]
    )
    (project_root / ".jig" / "spec" / "architecture.yaml").write_text(
        yaml_mod.safe_dump(arch.model_dump(mode="json"), sort_keys=False)
    )

    reviewer = IntentComplianceReviewer()
    by_uri = await reviewer.review_project(project_root)
    flat = [c for cs in by_uri.values() for c in cs]
    assert any(
        c.type == IntentCommentType.INTENT_DUPLICATE_ACROSS_ARTIFACTS.value
        for c in flat
    )


# ---- FrontendSpec is intent-bearing -------------------------------------


@pytest.mark.asyncio
async def test_reviewer_handles_frontend_spec():
    """FrontendSpec is intent-bearing in Final scope; the reviewer must
    walk it without raising and without flagging a good intent."""
    from jig.schemas.frontend import FrontendSpec

    fs = FrontendSpec(intent=_good_intent())
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([fs])
    assert comments == []


@pytest.mark.asyncio
async def test_reviewer_flags_bad_frontend_spec_intent():
    from jig.schemas.frontend import FrontendSpec

    fs = FrontendSpec(intent=_good_intent().model_copy(update={"problem": "x"}))
    reviewer = IntentComplianceReviewer()
    comments = await reviewer.review_artifacts([fs])
    types = {c.type for c in comments}
    assert IntentCommentType.INTENT_TOO_SHORT.value in types
