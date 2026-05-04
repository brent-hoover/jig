"""Two-cadence dispatch + per-commit cadence wiring (Track G MVP).

Per design §"Two-cadence review":

- Per-commit dispatch runs only the mechanical reviewers
  (contract-compliance, cross-cutting-policy, spec-compliance) and
  tags emitted comments with ``cadence: per_commit``.
- End-of-ticket dispatch runs the full default-on set per
  ``select_reviewers_for_ticket`` and tags comments
  ``cadence: end_of_ticket`` (the field default).

Comments emitted at per-commit cadence with critical severity feed
the ``PerCommitCheckFailed`` analytics event when the dispatch is
wired through ``EventEmitter`` (separate orchestrator follow-on);
this test module pins the dispatcher's contract directly.
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from jig.analytics.events import PerCommitCheckFailed
from jig.intent import Intent
from jig.reviewers import (
    BONES_REVIEWER_ID,
    CROSS_CUTTING_REVIEWER_ID,
    INTENT_REVIEWER_ID,
    SPEC_COMPLIANCE_REVIEWER_ID,
    ReviewerComment,
    ReviewerCommentType,
    Severity,
    dispatch_for_cadence,
)
from jig.schemas.arch import (
    Architecture,
    ContractPolarity,
    ContractsFile,
    CrossCuttingPolicy,
    IntegrationAcceptance,
    Module,
    TierHint,
)
from jig.spec_loader import (
    architecture_path,
    module_contracts_path,
    suite_structured_path,
)
from jig.spec_schema import (
    Capability,
    CapabilityState,
    StructuredSpec,
)
from jig.ticket import Ticket, WorkType


# ---- helpers -------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _intent() -> Intent:
    return Intent(
        problem="Need a place to keep universal rules for the federation reviewer.",
        simplest_solution="Author rules in architecture.yaml; let the reviewer scan tokens.",
    )


def _write_arch(
    project_root: Path,
    *,
    policies: list[CrossCuttingPolicy] | None = None,
) -> Architecture:
    arch = Architecture(
        modules=[
            Module(
                id="catalog-ingest",
                title="Catalog Ingest",
                summary="Ingests catalogs.",
                tier_hint=TierHint.STANDARD,
                intent=_intent(),
            ),
        ],
        cross_cutting_policies=policies or [],
    )
    path = architecture_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(arch.model_dump(mode="json")))
    return arch


def _write_contracts(
    project_root: Path,
    *,
    musts: list[str] | None = None,
) -> ContractsFile:
    contracts = ContractsFile(
        spec_version=1,
        module="catalog-ingest",
        integration_ac=[
            IntegrationAcceptance(
                capability="shopify-connect",
                must=musts or ["Shopify catalog flows into products collection"],
            ),
        ],
    )
    path = module_contracts_path(project_root, "catalog-ingest")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))
    return contracts


def _write_spec(
    project_root: Path,
    *,
    suite_id: str = "catalog",
    capabilities: list[Capability] | None = None,
) -> StructuredSpec:
    spec = StructuredSpec(
        name=suite_id,
        summary="Test suite",
        capabilities=capabilities
        or [
            Capability(
                id="shopify-connect",
                title="Shopify Connect",
                state=CapabilityState.PLANNED,
                summary="Integrate Shopify",
                acceptance_criteria=["Webhook signature validates against secret"],
                created_at=_now(),
                last_updated=_now(),
                state_changed_at=_now(),
            ),
        ],
        generated_at=_now(),
    )
    path = suite_structured_path(project_root, suite_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.model_dump(mode="json")))
    return spec


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True
    )


def _init_worktree(
    worktree: Path,
    *,
    head_files: dict[str, str] | None = None,
    base_branch: str = "main",
    feature_branch: str = "jig/tb-dispatch",
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
    ticket_id: str = "tb-dispatch",
    layer: str | None = "mvp",
    suite_id: str | None = "catalog",
    capability_ids: list[str] | None = None,
    reviewer_set: list[str] | None = None,
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="dispatch test",
        created_by="planner-pm",
        module_id="catalog-ingest",
        suite_id=suite_id,
        capability_ids=capability_ids if capability_ids is not None else ["shopify-connect"],
        layer=layer,
        reviewer_set=reviewer_set if reviewer_set is not None else [],
    )


# ---- comment cadence field ----------------------------------------------


def test_reviewer_comment_default_cadence_is_end_of_ticket():
    """Existing bones-era callers (no cadence field) keep working."""
    c = ReviewerComment(
        type=ReviewerCommentType.EMPTY_DIFF,
        severity=Severity.CRITICAL,
        reviewer="contract-compliance",
        prose="x",
    )
    assert c.cadence == "end_of_ticket"


def test_reviewer_comment_cadence_round_trips():
    """Per-commit cadence persists through JSON round-trip."""
    c = ReviewerComment(
        type=ReviewerCommentType.EMPTY_DIFF,
        severity=Severity.CRITICAL,
        reviewer="contract-compliance",
        prose="x",
        cadence="per_commit",
    )
    payload = c.model_dump(mode="json")
    restored = ReviewerComment.model_validate(payload)
    assert restored.cadence == "per_commit"


def test_reviewer_comment_rejects_unknown_cadence():
    with pytest.raises(Exception):
        ReviewerComment(
            type=ReviewerCommentType.EMPTY_DIFF,
            severity=Severity.CRITICAL,
            reviewer="contract-compliance",
            prose="x",
            cadence="hourly",  # type: ignore[arg-type]
        )


# ---- per-commit dispatch -------------------------------------------------


@pytest.mark.asyncio
async def test_per_commit_runs_only_mechanical_reviewers(tmp_path: Path):
    """Per-commit dispatch returns mechanical reviewers; intent is excluded."""
    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(
        worktree,
        head_files={
            "ingest.py": (
                "def shopify_webhook_signature_validates_secret_catalog_products():\n"
                "    return True\n"
            ),
        },
    )

    out = await dispatch_for_cadence(_ticket(), tmp_path, "per_commit")

    # Mechanical reviewers all present.
    assert BONES_REVIEWER_ID in out
    assert CROSS_CUTTING_REVIEWER_ID in out
    assert SPEC_COMPLIANCE_REVIEWER_ID in out
    # Intent reviewer not present at per-commit cadence.
    assert INTENT_REVIEWER_ID not in out


@pytest.mark.asyncio
async def test_per_commit_comments_carry_cadence_per_commit(tmp_path: Path):
    """Per-commit emitted comments are tagged with cadence=per_commit."""
    _write_arch(tmp_path)
    _write_contracts(
        tmp_path,
        musts=["webhook signature must validate against secret"],
    )
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(
        worktree,
        head_files={
            # Diff exists but doesn't reference the AC tokens — trips
            # contract-compliance's integration-AC reference miss.
            "ingest.py": "def helper():\n    return None\n",
        },
    )

    out = await dispatch_for_cadence(_ticket(), tmp_path, "per_commit")

    # At least one comment fired across mechanical reviewers; all
    # carry per_commit cadence.
    all_comments = [c for comments in out.values() for c in comments]
    assert all_comments  # at least one fired
    assert all(c.cadence == "per_commit" for c in all_comments)


@pytest.mark.asyncio
async def test_per_commit_ignores_explicit_reviewer_set(tmp_path: Path):
    """Per-commit cadence runs the fixed mechanical subset; reviewer_set doesn't gate it."""
    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    # Ticket explicitly only wants intent-compliance — per-commit
    # ignores the reviewer_set and runs the mechanical subset anyway.
    ticket = _ticket(reviewer_set=["intent-compliance"])
    out = await dispatch_for_cadence(ticket, tmp_path, "per_commit")

    assert BONES_REVIEWER_ID in out
    assert CROSS_CUTTING_REVIEWER_ID in out
    assert SPEC_COMPLIANCE_REVIEWER_ID in out


# ---- end-of-ticket dispatch ----------------------------------------------


@pytest.mark.asyncio
async def test_end_of_ticket_runs_full_default_on_set(tmp_path: Path):
    """End-of-ticket dispatch returns the full MVP default-on set."""
    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    out = await dispatch_for_cadence(_ticket(), tmp_path, "end_of_ticket")

    # All four MVP defaults present.
    assert BONES_REVIEWER_ID in out
    assert CROSS_CUTTING_REVIEWER_ID in out
    assert SPEC_COMPLIANCE_REVIEWER_ID in out
    assert INTENT_REVIEWER_ID in out


@pytest.mark.asyncio
async def test_end_of_ticket_comments_carry_default_cadence(tmp_path: Path):
    """End-of-ticket emitted comments default to cadence=end_of_ticket."""
    _write_arch(tmp_path)
    _write_contracts(
        tmp_path,
        musts=["webhook signature must validate against secret"],
    )
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(
        worktree,
        head_files={"ingest.py": "def helper():\n    return None\n"},
    )

    out = await dispatch_for_cadence(_ticket(), tmp_path, "end_of_ticket")

    all_comments = [c for comments in out.values() for c in comments]
    assert all_comments  # at least one fired
    assert all(c.cadence == "end_of_ticket" for c in all_comments)


@pytest.mark.asyncio
async def test_end_of_ticket_honors_explicit_reviewer_set(tmp_path: Path):
    """End-of-ticket dispatch follows ticket.reviewer_set if non-empty."""
    _write_arch(tmp_path)
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(worktree, head_files={"x.py": "x = 1\n"})

    ticket = _ticket(reviewer_set=["contract-compliance"])
    out = await dispatch_for_cadence(ticket, tmp_path, "end_of_ticket")

    assert BONES_REVIEWER_ID in out
    assert CROSS_CUTTING_REVIEWER_ID not in out
    assert SPEC_COMPLIANCE_REVIEWER_ID not in out


# ---- per-commit critical detection (PerCommitCheckFailed source) ---------


@pytest.mark.asyncio
async def test_per_commit_critical_comments_drive_per_commit_failure_event(tmp_path: Path):
    """When per-commit dispatch returns critical comments, the caller can
    map them onto ``PerCommitCheckFailed`` events.

    The dispatcher itself doesn't emit events (that's the orchestrator
    hook's job); this test pins the contract that critical comments
    carrying per_commit cadence are observable so the hook can wire
    them into ``EventEmitter``.
    """
    _write_arch(
        tmp_path,
        policies=[
            CrossCuttingPolicy(
                id="no-secrets-in-logs",
                polarity=ContractPolarity.NEGATIVE,
                rule="Modules MUST NOT log credentials tokens or secrets",
            ),
        ],
    )
    _write_contracts(tmp_path)
    _write_spec(tmp_path)
    worktree = tmp_path / ".jig" / "worktrees" / "tb-dispatch"
    _init_worktree(
        worktree,
        head_files={
            "ingest.py": (
                "def login(credentials):\n"
                "    log.info('using tokens %s', credentials)\n"
            ),
        },
    )

    out = await dispatch_for_cadence(_ticket(), tmp_path, "per_commit")

    # Walk the dispatch output for critical-severity per-commit comments.
    critical_per_commit = [
        c
        for comments in out.values()
        for c in comments
        if c.severity == "critical" and c.cadence == "per_commit"
    ]
    assert critical_per_commit, "expected at least one critical per-commit comment"
    # Verify the PerCommitCheckFailed event shape accepts the data we'd
    # emit from this comment — the orchestrator hook code that wires
    # the dispatcher to the emitter would do this construction.
    c = critical_per_commit[0]
    event = PerCommitCheckFailed(
        ticket_id="tb-dispatch",
        agent_id="dev-agent",
        commit_sha="abc123",
        reviewer_role="cross_cutting_policy",
        violation_category=c.type,
        contract_uri=c.contract_uri,
        severity=c.severity,  # type: ignore[arg-type]
        auto_applied=False,
    )
    assert event.kind == "per_commit_check_failed"
