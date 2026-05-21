"""Spec-compliance reviewer (Track G MVP).

Mechanical, end-of-ticket / per-commit eligible, deterministic, no LLM.
Two checks: capability cited but not in spec (critical), behavior AC
not referenced in diff (important). Default-on for MVP+ layers.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from jig.reviewers import (
    SPEC_COMPLIANCE_REVIEWER_ID,
    ReviewerCommentType,
    Severity,
    SpecComplianceReviewer,
    select_reviewers_for_ticket,
)
from jig.spec_loader import suite_structured_path
from jig.spec_schema import (
    Behavior,
    Capability,
    CapabilityState,
    StructuredSpec,
)
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


# ---- helpers -------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _capability(
    *,
    cap_id: str = "shopify-connect",
    behaviors: list[Behavior] | None = None,
    acceptance_criteria: list[str] | None = None,
) -> Capability:
    return Capability(
        id=cap_id,
        title=cap_id.replace("-", " ").title(),
        state=CapabilityState.PLANNED,
        summary="A capability under spec.",
        behaviors=behaviors or [],
        acceptance_criteria=acceptance_criteria or ["The system handles requests"],
        created_at=_now(),
        last_updated=_now(),
        state_changed_at=_now(),
    )


def _write_spec(
    project_root: Path,
    *,
    suite_id: str = "catalog",
    capabilities: list[Capability] | None = None,
) -> StructuredSpec:
    spec = StructuredSpec(
        name=suite_id,
        summary="Test suite",
        capabilities=capabilities or [_capability()],
        generated_at=_now(),
    )
    path = suite_structured_path(project_root, suite_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.model_dump(mode="json")))
    return spec


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_worktree(
    worktree: Path,
    *,
    head_files: dict[str, str] | None = None,
    base_branch: str = "main",
    feature_branch: str = "jig/tb-spec",
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
    ticket_id: str = "tb-spec",
    suite_id: str | None = "catalog",
    capability_ids: list[str] | None = None,
    layer: str | None = "mvp",
    reviewer_set: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="MVP spec ticket",
        created_by="planner-pm",
        module_id="catalog-ingest",
        suite_id=suite_id,
        capability_ids=capability_ids
        if capability_ids is not None
        else ["shopify-connect"],
        layer=layer,
        reviewer_set=reviewer_set if reviewer_set is not None else [],
        description=TICKET_AC_PLACEHOLDER,
    )


# ---- absence handling ----------------------------------------------------


@pytest.mark.asyncio
async def test_returns_empty_when_no_suite_id(tmp_path: Path):
    """Bones tickets without suite_id legitimately have nothing to check."""
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = SpecComplianceReviewer()
    ticket = _ticket(suite_id=None)
    comments = await reviewer.review(ticket, tmp_path)

    assert comments == []


@pytest.mark.asyncio
async def test_returns_empty_when_no_capability_ids(tmp_path: Path):
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = SpecComplianceReviewer()
    ticket = _ticket(capability_ids=[])
    comments = await reviewer.review(ticket, tmp_path)

    assert comments == []


@pytest.mark.asyncio
async def test_returns_empty_when_spec_missing(tmp_path: Path):
    """No suite spec → silently empty (contract-compliance owns the warning)."""
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = SpecComplianceReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


@pytest.mark.asyncio
async def test_returns_empty_when_diff_is_empty(tmp_path: Path):
    """Empty diff is contract-compliance's responsibility."""
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(worktree)  # no head files = empty diff

    reviewer = SpecComplianceReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


# ---- capability not in spec ----------------------------------------------


@pytest.mark.asyncio
async def test_capability_not_in_spec_emits_critical(tmp_path: Path):
    """Ticket cites a capability the spec doesn't have → critical comment."""
    _write_spec(
        tmp_path,
        capabilities=[_capability(cap_id="other-capability")],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    reviewer = SpecComplianceReviewer()
    ticket = _ticket(capability_ids=["nonexistent-capability"])
    comments = await reviewer.review(ticket, tmp_path)

    assert any(
        c.type == ReviewerCommentType.CAPABILITY_NOT_FOUND_IN_SPEC.value
        for c in comments
    )
    cap_comment = next(
        c
        for c in comments
        if c.type == ReviewerCommentType.CAPABILITY_NOT_FOUND_IN_SPEC.value
    )
    assert cap_comment.severity == Severity.CRITICAL.value
    assert cap_comment.reviewer == SPEC_COMPLIANCE_REVIEWER_ID
    assert "nonexistent-capability" in cap_comment.contract_uri


# ---- AC reference miss ---------------------------------------------------


@pytest.mark.asyncio
async def test_capability_level_ac_not_referenced_emits_important(tmp_path: Path):
    """Top-level AC not referenced in diff → important comment."""
    _write_spec(
        tmp_path,
        capabilities=[
            _capability(
                acceptance_criteria=[
                    "Webhook signature validates against the configured secret",
                ],
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(
        worktree,
        head_files={
            # Diff has none of the AC tokens (no webhook/signature/secret).
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    reviewer = SpecComplianceReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.BEHAVIOR_AC_NOT_REFERENCED.value
    assert c.severity == Severity.IMPORTANT.value
    assert c.contract_uri is not None
    assert "shopify-connect" in c.contract_uri


@pytest.mark.asyncio
async def test_capability_level_ac_referenced_passes(tmp_path: Path):
    _write_spec(
        tmp_path,
        capabilities=[
            _capability(
                acceptance_criteria=[
                    "Webhook signature validates against the configured secret",
                ],
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(
        worktree,
        head_files={
            # References webhook + signature.
            "ingest.py": ("def validate_webhook_signature(secret):\n    return True\n"),
        },
    )

    reviewer = SpecComplianceReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert comments == []


@pytest.mark.asyncio
async def test_behavior_scoped_ac_not_referenced_emits_important(tmp_path: Path):
    """Behavior-level AC token miss → important comment scoped to the behavior."""
    cap = Capability(
        id="shopify-connect",
        title="Shopify Connect",
        state=CapabilityState.PLANNED,
        summary="Integrate with Shopify",
        behaviors=[
            Behavior(
                id="webhook-validation",
                description="Validate webhook signatures",
                acceptance_criteria=[
                    "Hash the payload with the configured secret HMAC",
                ],
            ),
        ],
        created_at=_now(),
        last_updated=_now(),
        state_changed_at=_now(),
    )
    _write_spec(tmp_path, capabilities=[cap])
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(
        worktree,
        head_files={
            # No mention of hash/payload/secret/HMAC.
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    reviewer = SpecComplianceReviewer()
    comments = await reviewer.review(_ticket(), tmp_path)

    assert len(comments) == 1
    c = comments[0]
    assert c.type == ReviewerCommentType.BEHAVIOR_AC_NOT_REFERENCED.value
    assert "behaviors/webhook-validation" in (c.contract_uri or "")


@pytest.mark.asyncio
async def test_skipped_capabilities_outside_ticket_scope(tmp_path: Path):
    """The reviewer doesn't check capabilities the ticket didn't claim."""
    _write_spec(
        tmp_path,
        capabilities=[
            _capability(
                cap_id="shopify-connect",
                acceptance_criteria=["Shopify integration handles webhook posts"],
            ),
            _capability(
                cap_id="csv-upload",
                acceptance_criteria=["CSV uploaded by operator gets processed"],
            ),
        ],
    )
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(
        worktree,
        head_files={
            # References shopify but not csv — and the ticket only claims shopify.
            "ingest.py": "def shopify_webhook_integration():\n    return None\n",
        },
    )

    reviewer = SpecComplianceReviewer()
    ticket = _ticket(capability_ids=["shopify-connect"])
    comments = await reviewer.review(ticket, tmp_path)

    assert comments == []


# ---- alias resolution ----------------------------------------------------


@pytest.mark.asyncio
async def test_capability_resolves_via_alias(tmp_path: Path):
    """Spec-renames stay tracking via aliases — ticket id may match an alias."""
    cap = Capability(
        id="shopify-integration",  # renamed from shopify-connect
        title="Shopify Integration",
        state=CapabilityState.PLANNED,
        summary="Renamed",
        acceptance_criteria=["Webhook signature validates"],
        aliases=["shopify-connect"],  # legacy id
        created_at=_now(),
        last_updated=_now(),
        state_changed_at=_now(),
    )
    _write_spec(tmp_path, capabilities=[cap])
    worktree = tmp_path / ".jig" / "worktrees" / "tb-spec"
    _init_worktree(
        worktree,
        head_files={
            "ingest.py": ("def validate_webhook_signature():\n    return True\n"),
        },
    )

    reviewer = SpecComplianceReviewer()
    # Ticket cites the legacy id; the reviewer should resolve it via alias.
    ticket = _ticket(capability_ids=["shopify-connect"])
    comments = await reviewer.review(ticket, tmp_path)

    assert comments == []


# ---- dispatch wiring -----------------------------------------------------


def test_spec_compliance_default_on_for_mvp_layer():
    """MVP default-on set must include spec-compliance."""
    t = _ticket(layer="mvp", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert SPEC_COMPLIANCE_REVIEWER_ID in ids


def test_spec_compliance_default_on_for_final_layer():
    t = _ticket(layer="final", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert SPEC_COMPLIANCE_REVIEWER_ID in ids


def test_spec_compliance_not_in_bones_defaults():
    """Bones budget skips spec-compliance per design — see Track G MVP."""
    t = _ticket(layer="bones", reviewer_set=[])
    ids = select_reviewers_for_ticket(t)
    assert SPEC_COMPLIANCE_REVIEWER_ID not in ids


def test_explicit_reviewer_set_overrides_default():
    """Authored reviewer_set wins over the default-on set."""
    t = _ticket(layer="mvp", reviewer_set=["contract-compliance"])
    ids = select_reviewers_for_ticket(t)
    assert SPEC_COMPLIANCE_REVIEWER_ID not in ids
