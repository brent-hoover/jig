"""Cross-cutting-policy reviewer (Track G MVP).

Mechanical, end-of-ticket / per-commit eligible, deterministic, no LLM.
Two checks: negative-polarity violations (token intersection trips
critical), positive-polarity-with-auto-AC reference miss (token disjoint
trips important). Default-on for every layer.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.intent import Intent
from jig.reviewers import (
    CROSS_CUTTING_REVIEWER_ID,
    CrossCuttingPolicyReviewer,
    ReviewerCommentType,
    Severity,
    select_reviewers_for_ticket,
)
from jig.schemas.arch import (
    Architecture,
    ContractPolarity,
    CrossCuttingPolicy,
    Module,
    TierHint,
)
from jig.spec_loader import architecture_path
from jig.ticket import Ticket, WorkType


# ---- helpers -------------------------------------------------------------


def _intent() -> Intent:
    return Intent(
        problem=(
            "We need a place to keep universal rules so the federation "
            "reviewer has something to enforce against the diff."
        ),
        simplest_solution=(
            "Author the rules in architecture.yaml and let the reviewer "
            "scan for token matches against the diff."
        ),
    )


def _module() -> Module:
    return Module(
        id="catalog-ingest",
        title="Catalog Ingest",
        summary="Ingests catalogs.",
        tier_hint=TierHint.STANDARD,
        intent=_intent(),
    )


def _write_architecture(
    project_root: Path,
    *,
    policies: list[CrossCuttingPolicy] | None = None,
) -> Architecture:
    arch = Architecture(
        modules=[_module()],
        cross_cutting_policies=policies or [],
    )
    path = architecture_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(arch.model_dump(mode="json")))
    return arch


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True
    )


def _init_worktree(
    worktree: Path,
    *,
    head_files: dict[str, str] | None = None,
    base_branch: str = "main",
    feature_branch: str = "jig/tb-catalog-ingest",
) -> None:
    head_files = head_files or {}
    worktree.mkdir(parents=True, exist_ok=True)
    _git(worktree, "init", "-b", base_branch)
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")
    _git(worktree, "config", "commit.gpgsign", "false")
    _git(worktree, "commit", "--allow-empty", "-m", "base")
    if head_files:
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
    layer: str | None = "bones",
    reviewer_set: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="bones tracer bullet",
        created_by="coordinator-v2",
        module_id="catalog-ingest",
        capability_ids=["shopify-connect"],
        layer=layer,
        reviewer_set=reviewer_set if reviewer_set is not None else [],
    )


# ---- absence handling ----------------------------------------------------


@pytest.mark.asyncio
async def test_returns_empty_when_no_architecture(tmp_path: Path):
    """No architecture.yaml → silently empty (contract-compliance owns the warning)."""
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


@pytest.mark.asyncio
async def test_returns_empty_when_no_policies_declared(tmp_path: Path):
    _write_architecture(tmp_path, policies=[])
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


@pytest.mark.asyncio
async def test_returns_empty_when_diff_is_empty(tmp_path: Path):
    """Empty diff is the contract-compliance reviewer's responsibility."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="no-secrets-in-logs",
                polarity=ContractPolarity.NEGATIVE,
                rule="Modules MUST NOT log credentials, tokens, or secrets",
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(worktree)  # no head files = empty diff

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


# ---- negative polarity ---------------------------------------------------


@pytest.mark.asyncio
async def test_negative_polarity_token_match_emits_critical(tmp_path: Path):
    """Diff mentions prohibited vocabulary → critical violation comment."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="no-secrets-in-logs",
                polarity=ContractPolarity.NEGATIVE,
                rule="Modules MUST NOT log credentials tokens or secrets",
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # Mentions both "credentials" and "tokens" — token intersection trips.
            "ingest.py": (
                "def login(credentials):\n"
                "    log.info('using tokens %s', credentials)\n"
            ),
        },
    )

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.CROSS_CUTTING_POLICY_VIOLATION.value
    assert c.severity == Severity.CRITICAL.value
    assert c.reviewer == CROSS_CUTTING_REVIEWER_ID
    assert c.contract_uri is not None
    assert "no-secrets-in-logs" in c.contract_uri


@pytest.mark.asyncio
async def test_negative_polarity_no_match_passes(tmp_path: Path):
    """Diff doesn't reference any prohibited token → pass."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="no-cross-module-writes",
                polarity=ContractPolarity.NEGATIVE,
                rule="Modules MUST NOT write directly to another module's owned collections",
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # Diff has content but no policy-rule vocabulary.
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


# ---- positive polarity with auto-AC --------------------------------------


@pytest.mark.asyncio
async def test_positive_auto_ac_missing_reference_emits_important(tmp_path: Path):
    """Positive policy with auto_generates_integration_ac → token-disjoint trips important."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="every-endpoint-emits-audit",
                polarity=ContractPolarity.POSITIVE,
                rule="Every public endpoint MUST emit an audit event",
                auto_generates_integration_ac=True,
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # Diff has no audit/endpoint/event tokens.
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.CROSS_CUTTING_POLICY_NOT_REFERENCED.value
    assert c.severity == Severity.IMPORTANT.value
    assert c.reviewer == CROSS_CUTTING_REVIEWER_ID
    assert c.contract_uri is not None
    assert "every-endpoint-emits-audit" in c.contract_uri


@pytest.mark.asyncio
async def test_positive_auto_ac_with_reference_passes(tmp_path: Path):
    """Diff references at least one significant token → pass."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="every-endpoint-emits-audit",
                polarity=ContractPolarity.POSITIVE,
                rule="Every public endpoint MUST emit an audit event",
                auto_generates_integration_ac=True,
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            "endpoints.py": (
                "def public_endpoint():\n"
                "    emit_audit_event('login')\n"
            ),
        },
    )

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


# ---- positive polarity without auto-AC (skipped) -------------------------


@pytest.mark.asyncio
async def test_positive_without_auto_ac_is_skipped(tmp_path: Path):
    """Positive informational policies are not enforced in MVP."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="ids-are-opaque",
                polarity=ContractPolarity.POSITIVE,
                rule="Treat IDs as opaque strings; never parse them",
                auto_generates_integration_ac=False,
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # No mention of opaque/strings/parse — would fire if enforced.
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


# ---- multiple policies ---------------------------------------------------


@pytest.mark.asyncio
async def test_multiple_policies_each_evaluated_independently(tmp_path: Path):
    """One pass + one fail across policies → exactly one comment."""
    _write_architecture(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="ok-rule",
                polarity=ContractPolarity.POSITIVE,
                rule="Every endpoint MUST emit an audit event",
                auto_generates_integration_ac=True,
            ),
            CrossCuttingPolicy(
                id="fail-rule",
                polarity=ContractPolarity.NEGATIVE,
                rule="Modules MUST NOT log credentials",
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-catalog-ingest"
    _init_worktree(
        worktree,
        head_files={
            # References "endpoint" + "audit" + "event" (positive passes)
            # AND "credentials" (negative fails).
            "ingest.py": (
                "def endpoint():\n"
                "    emit_audit_event(credentials)\n"
            ),
        },
    )

    reviewer = CrossCuttingPolicyReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert len(comments) == 1
    assert comments[0].type == ReviewerCommentType.CROSS_CUTTING_POLICY_VIOLATION.value
    assert "fail-rule" in (comments[0].contract_uri or "")


# ---- dispatch wiring -----------------------------------------------------


def test_cross_cutting_default_on_for_bones_layer():
    """Bones default-on set must include cross-cutting-policy.

    Cross-cutting policies are universal rules — they apply at every
    layer including bones, per design §"Reviewer federation".
    """
    t = _ticket(layer="bones", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert CROSS_CUTTING_REVIEWER_ID in ids


def test_cross_cutting_default_on_for_mvp_layer():
    t = _ticket(layer="mvp", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert CROSS_CUTTING_REVIEWER_ID in ids


def test_explicit_reviewer_set_overrides_default():
    """Authored reviewer_set wins over the default-on set."""
    t = _ticket(layer="mvp", reviewer_set=["spec-compliance"])
    ids = select_reviewers_for_ticket(t)
    assert CROSS_CUTTING_REVIEWER_ID not in ids
