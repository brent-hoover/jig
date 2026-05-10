"""Bones contract-compliance reviewer (Track G2).

Bones scope per ``docs/v2.0/implementation/v2-plan.md`` Track G row +
``docs/v2.0/pm-workflow/design.md`` §"Reviewer federation — selection
logic": mechanical, end-of-ticket, deterministic, no LLM. Three
checks (existence, module-path, integration-AC reference). The
reviewer must not depend on a non-empty ``ticket.reviewer_set`` and
must default-on for ``layer == "bones"`` tickets per the Track F
handoff note.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.intent import Intent
from jig.reviewers import (
    BONES_REVIEWER_ID,
    ContractComplianceReviewer,
    ReviewerComment,
    ReviewerCommentType,
    Severity,
    select_reviewers_for_ticket,
)
from jig.reviewers.contract_compliance import _significant_tokens
from jig.schemas.arch import (
    ContractsFile,
    IntegrationAcceptance,
    OwnedCollection,
)
from jig.spec_loader import module_contracts_path
from jig.ticket import Ticket, WorkType


# ---- helpers -------------------------------------------------------------


def _write_contracts(
    project_root: Path,
    *,
    module_id: str = "catalog-ingest",
    capability: str = "shopify-connect",
    musts: list[str] | None = None,
) -> ContractsFile:
    """Author a one-module contracts.yaml fixture."""
    if musts is None:
        musts = [
            "Shopify catalog data flows into the products collection",
            "catalog-ingested event published with normalized payload",
        ]
    contracts = ContractsFile(
        spec_version=1,
        module=module_id,
        owns=[
            OwnedCollection(
                collection="products",
                db="catalog",
                write_access=["self"],
                read_access=["categorization"],
            )
        ],
        integration_ac=[
            IntegrationAcceptance(capability=capability, must=musts)
        ],
    )
    path = module_contracts_path(project_root, module_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))
    return contracts


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def _init_worktree(
    worktree: Path,
    *,
    base_files: dict[str, str] | None = None,
    head_files: dict[str, str] | None = None,
    base_branch: str = "main",
    feature_branch: str = "jig/tb-catalog-ingest",
) -> None:
    """Initialize a fixture repo and lay down a base + HEAD commit.

    Mirrors the layout ``jig.worktree.create_worktree`` produces: a
    base commit on ``base_branch`` then a feature branch on top with
    one or more HEAD commits. ``git diff base_branch..HEAD`` returns
    the delta.

    If ``head_files`` is empty, no feature commit is made and the
    HEAD remains on ``base_branch`` — that's the empty-diff fixture.
    """
    base_files = base_files or {}
    head_files = head_files or {}
    worktree.mkdir(parents=True, exist_ok=True)
    _git(worktree, "init", "-b", base_branch)
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")
    _git(worktree, "config", "commit.gpgsign", "false")
    if base_files:
        for path, content in base_files.items():
            f = worktree / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        _git(worktree, "add", "-A")
    _git(worktree, "commit", "--allow-empty", "-m", "base")
    if head_files:
        # Branch off base so `git diff base..HEAD` shows only the feature work.
        _git(worktree, "checkout", "-b", feature_branch)
        for path, content in head_files.items():
            f = worktree / path
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(content)
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-m", "head")


def _ticket(
    *,
    ticket_id: str = "tb-catalog-ingest",
    module_id: str | None = "catalog-ingest",
    capability_ids: list[str] | None = None,
    layer: str | None = "bones",
    reviewer_set: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="bones tracer bullet",
        created_by="coordinator-v2",
        module_id=module_id,
        capability_ids=capability_ids if capability_ids is not None else ["shopify-connect"],
        layer=layer,
        reviewer_set=reviewer_set if reviewer_set is not None else [],
    )


# ---- token helper --------------------------------------------------------


def test_significant_tokens_strips_stopwords_and_short_words():
    text = "The product must flow into the collection with a normalized payload"
    toks = _significant_tokens(text)
    # short words ('the', 'a') and stopwords ('must', 'with', 'into') dropped
    assert "must" not in toks
    assert "with" not in toks
    assert "into" not in toks
    # length-3 words dropped
    assert "the" not in toks
    # length>=4 non-stopwords retained, lowercased
    assert "product" in toks
    assert "collection" in toks
    assert "normalized" in toks
    assert "payload" in toks


def test_significant_tokens_handles_empty_and_punctuation():
    """Numerics, punctuation, and length<4 words don't survive the filter."""
    assert _significant_tokens("") == set()
    toks = _significant_tokens("foo, bars; baz!! 1234 widget-shape")
    # length-3 words ('foo', 'baz') drop; length-4+ words ('bars',
    # 'widget', 'shape') survive; numerics ('1234') drop.
    assert "bars" in toks
    assert "widget" in toks
    assert "shape" in toks
    assert "foo" not in toks
    assert "baz" not in toks
    assert "1234" not in toks


# ---- happy path ----------------------------------------------------------


@pytest.mark.asyncio
async def test_review_returns_no_comments_when_diff_satisfies_acs(tmp_path: Path):
    project_root = tmp_path
    _write_contracts(
        project_root,
        musts=[
            "Shopify catalog data flows into the products collection",
            "catalog-ingested event published with normalized payload",
        ],
    )
    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # Diff text mentions 'shopify' (from must #1) and 'normalized'
            # / 'payload' / 'catalog' / 'event' (from must #2). Both
            # MUSTs have at least one significant token in the diff.
            "ingest.py": (
                "def fetch_shopify_products():\n"
                "    return {'products': []}\n"
                "\n"
                "def publish_catalog_ingested_event(payload):\n"
                "    return {'normalized': True}\n"
            ),
        },
    )

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(_ticket(), project_root)

    assert comments == []


# ---- empty-diff ----------------------------------------------------------


@pytest.mark.asyncio
async def test_review_flags_empty_diff_as_critical(tmp_path: Path):
    project_root = tmp_path
    _write_contracts(project_root)
    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    # Base only — no HEAD changes; git diff main..HEAD is empty.
    _init_worktree(worktree)

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(_ticket(), project_root)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.EMPTY_DIFF.value
    assert c.severity == Severity.CRITICAL.value
    assert c.reviewer == BONES_REVIEWER_ID
    assert c.confidence == 1.0


@pytest.mark.asyncio
async def test_review_flags_missing_worktree_as_empty_diff(tmp_path: Path):
    """No worktree dir → no diff to review → empty-diff comment.

    The synthetic operator can hit this if it invokes the reviewer
    before the orchestrator's worktree-create step lands; bones
    should surface it cleanly rather than crash.
    """
    project_root = tmp_path
    _write_contracts(project_root)

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(_ticket(), project_root)

    assert len(comments) == 1
    assert comments[0].type == ReviewerCommentType.EMPTY_DIFF.value


# ---- AC-reference miss ---------------------------------------------------


@pytest.mark.asyncio
async def test_review_flags_missing_ac_reference(tmp_path: Path):
    project_root = tmp_path
    _write_contracts(
        project_root,
        musts=[
            "Shopify catalog data flows into the products collection",
        ],
    )
    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # Diff has content but mentions none of the AC tokens
            # (no 'shopify', no 'catalog', no 'products', no 'collection').
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(_ticket(), project_root)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.INTEGRATION_AC_NOT_REFERENCED.value
    assert c.severity == Severity.IMPORTANT.value
    assert c.contract_uri is not None
    assert "shopify-connect" in c.contract_uri


@pytest.mark.asyncio
async def test_review_flags_only_unreferenced_acs_among_many(tmp_path: Path):
    """Multi-AC: only the missing-reference one fires."""
    project_root = tmp_path
    _write_contracts(
        project_root,
        musts=[
            "Shopify catalog data flows into the products collection",
            "Webhook signature validates against secret",
        ],
    )
    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # Mentions shopify/catalog/products (must #1 satisfied) but
            # not webhook/signature/validates/secret (must #2 missing).
            "ingest.py": "shopify_products_in_catalog = []\n",
        },
    )

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(_ticket(), project_root)

    assert len(comments) == 1
    assert comments[0].type == ReviewerCommentType.INTEGRATION_AC_NOT_REFERENCED.value
    # Comment text quotes the missing AC, not the satisfied one
    assert "Webhook" in comments[0].prose


@pytest.mark.asyncio
async def test_review_skips_acs_for_capabilities_not_on_ticket(tmp_path: Path):
    """A module's other-capability AC isn't this ticket's responsibility."""
    project_root = tmp_path
    contracts = ContractsFile(
        spec_version=1,
        module="catalog-ingest",
        integration_ac=[
            IntegrationAcceptance(
                capability="shopify-connect",
                must=["Shopify catalog into products"],
            ),
            IntegrationAcceptance(
                capability="csv-upload",
                must=["CSV file uploaded by operator processed into products"],
            ),
        ],
    )
    path = module_contracts_path(project_root, "catalog-ingest")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))

    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # References shopify-connect AC tokens; csv-upload AC
            # tokens absent. Ticket only claims shopify-connect →
            # csv-upload AC isn't checked.
            "ingest.py": "shopify_products_in_catalog = []\n",
        },
    )

    reviewer = ContractComplianceReviewer()
    ticket = _ticket(capability_ids=["shopify-connect"])
    comments = await reviewer.review(ticket, project_root)

    assert comments == []


# ---- absence handling ----------------------------------------------------


@pytest.mark.asyncio
async def test_review_handles_missing_contracts_file(tmp_path: Path):
    """No contracts.yaml → notable comment, no crash."""
    project_root = tmp_path
    # No _write_contracts() call
    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(worktree, head_files={"ingest.py": "x = 1\n"})

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(_ticket(), project_root)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.CONTRACT_VIOLATION.value
    assert c.severity == Severity.NOTABLE.value
    assert c.contract_uri is not None
    assert "catalog-ingest" in c.contract_uri


@pytest.mark.asyncio
async def test_review_handles_ticket_without_module_id(tmp_path: Path):
    """Ticket with no module_id → notable, no crash."""
    project_root = tmp_path
    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = ContractComplianceReviewer()
    ticket = _ticket(module_id=None)
    comments = await reviewer.review(ticket, project_root)

    assert len(comments) == 1
    assert comments[0].type == ReviewerCommentType.CONTRACT_VIOLATION.value
    assert comments[0].severity == Severity.NOTABLE.value


# ---- explicit worktree path / base ref overrides ------------------------


@pytest.mark.asyncio
async def test_review_honors_explicit_worktree_and_base_ref(tmp_path: Path):
    """Tests / synthetic operator can override the worktree convention."""
    project_root = tmp_path
    _write_contracts(project_root)
    custom_worktree = tmp_path / "custom-place"
    _init_worktree(
        custom_worktree,
        base_branch="develop",
        head_files={
            "ingest.py": "shopify_products_catalog_normalized_event_payload = 1\n",
        },
    )

    reviewer = ContractComplianceReviewer()
    comments = await reviewer.review(
        _ticket(),
        project_root,
        worktree_path=custom_worktree,
        base_ref="develop",
    )
    assert comments == []


# ---- bones dispatch ------------------------------------------------------


def test_select_reviewers_for_ticket_defaults_for_bones_layer_empty_set():
    """Bones default-on set: contract-compliance + cross-cutting-policy.

    Cross-cutting policies are universal rules per design — they apply
    at every layer including bones. Intent-compliance and
    spec-compliance start at MVP.
    """
    t = _ticket(layer="bones", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert BONES_REVIEWER_ID in ids
    assert "cross-cutting-policy" in ids


def test_select_reviewers_for_ticket_returns_judgment_defaults_for_layer_unset():
    """Tickets with no layer and empty reviewer_set still get the three
    judgment reviewers (error-handling, pattern-conformance, test-adequacy)
    which are default-on for every ticket regardless of layer.
    """
    from jig.reviewers.dispatch import (
        ERROR_HANDLING_REVIEWER_ID,
        PATTERN_CONFORMANCE_REVIEWER_ID,
        TEST_ADEQUACY_REVIEWER_ID,
    )
    t = _ticket(layer=None, reviewer_set=[])
    result = select_reviewers_for_ticket(t)
    assert set(result) == {
        ERROR_HANDLING_REVIEWER_ID,
        PATTERN_CONFORMANCE_REVIEWER_ID,
        TEST_ADEQUACY_REVIEWER_ID,
    }


def test_select_reviewers_for_ticket_honors_explicit_reviewer_set():
    """Explicit reviewer_set is honored; judgment defaults are appended on top."""
    from jig.reviewers.dispatch import _JUDGMENT_DEFAULTS
    t = _ticket(layer="bones", reviewer_set=["contract-compliance", "spec-compliance"])
    result = select_reviewers_for_ticket(t)
    assert result[:2] == ["contract-compliance", "spec-compliance"]
    for jid in _JUDGMENT_DEFAULTS:
        assert jid in result


def test_select_reviewers_for_ticket_honors_explicit_set_on_non_bones():
    """Explicit reviewer_set is honored; judgment defaults are appended on top."""
    from jig.reviewers.dispatch import _JUDGMENT_DEFAULTS
    t = _ticket(layer="final", reviewer_set=["pattern-conformance"])
    result = select_reviewers_for_ticket(t)
    assert "pattern-conformance" in result
    for jid in _JUDGMENT_DEFAULTS:
        assert jid in result


# ---- comment serialization ----------------------------------------------


def test_reviewer_comment_round_trips_through_json():
    """Synthetic operator may persist comments via JSON; round-trip preserves shape."""
    c = ReviewerComment(
        type=ReviewerCommentType.INTEGRATION_AC_NOT_REFERENCED,
        severity=Severity.IMPORTANT,
        reviewer=BONES_REVIEWER_ID,
        prose="AC text not referenced",
        contract_uri="project://arch/modules/catalog-ingest/contracts#integration_ac/shopify-connect",
        file="ingest.py",
        line=42,
    )
    payload = c.model_dump(mode="json")
    restored = ReviewerComment.model_validate(payload)
    assert restored.type == ReviewerCommentType.INTEGRATION_AC_NOT_REFERENCED.value
    assert restored.severity == Severity.IMPORTANT.value
    assert restored.line == 42
    assert restored.confidence == 1.0
    assert restored.contract_uri == c.contract_uri


def test_reviewer_comment_rejects_unknown_fields():
    """Strict shape: extra='forbid' catches typos in operator hand-authored fixtures."""
    with pytest.raises(Exception):
        ReviewerComment.model_validate(
            {
                "type": ReviewerCommentType.EMPTY_DIFF.value,
                "severity": Severity.CRITICAL.value,
                "reviewer": "contract-compliance",
                "prose": "x",
                "unknown_field": "boom",
            }
        )


def test_reviewer_comment_clamps_confidence_to_unit_interval():
    with pytest.raises(Exception):
        ReviewerComment(
            type=ReviewerCommentType.EMPTY_DIFF,
            severity=Severity.CRITICAL,
            reviewer=BONES_REVIEWER_ID,
            prose="x",
            confidence=1.5,
        )


# ---- intent + capability id integration ---------------------------------
# These wire Intent / capability fields into the test fixtures so it's
# obvious the reviewer interprets the v2 ticket fields correctly even
# when they're populated from realistic inputs.


@pytest.mark.asyncio
async def test_review_with_capability_intent_workflow(tmp_path: Path):
    """End-to-end with a realistic Intent on the contracts file."""
    project_root = tmp_path
    contracts = ContractsFile(
        spec_version=1,
        module="catalog-ingest",
        integration_ac=[
            IntegrationAcceptance(
                capability="shopify-connect",
                must=["Shopify products mirror into the local catalog"],
            ),
        ],
    )
    # ContractsFile's behavioral_contracts / data_contracts each
    # require Intent — but they're empty here (defaults). Exercise
    # Intent on the ticket-side via capability_ids.
    _ = Intent(problem="x", simplest_solution="y")  # imported, used elsewhere
    path = module_contracts_path(project_root, "catalog-ingest")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))

    worktree = project_root / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            "shopify_mirror.py": (
                "def mirror_products_into_catalog():\n    return True\n"
            )
        },
    )

    reviewer = ContractComplianceReviewer()
    ticket = _ticket(capability_ids=["shopify-connect"])
    comments = await reviewer.review(ticket, project_root)
    assert comments == []
