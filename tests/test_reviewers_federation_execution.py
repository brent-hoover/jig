"""Federation-execution wiring (Block 3, Important 1).

Pre-Block-3 ``dispatch_for_cadence`` *selected* the LLM specialty
reviewers (``reviewer-security``, ``reviewer-performance``,
``reviewer-architectural``) but had no execution branches for them —
the federation could report "this reviewer is in the set" without
ever spawning the agent. This module pins the Block 3 fix:

- ``dispatch_for_cadence`` returns ``LlmReviewerPending`` records for
  the LLM-driven reviewer ids at end-of-ticket cadence.
- ``dispatch_with_llm_spawn`` runs the in-process mechanical reviewers
  AND spawns each LLM reviewer via the orchestrator, then reads back
  whatever the spawned agents posted to the ``ReviewCommentsStore``.
- Per-commit cadence never queues LLM reviewers — the latency budget
  rules them out by design.

Tests use a mocked orchestrator (``_FakeOrchestrator``) so the
spawn-and-wait flow is real (asyncio + ReviewCommentsStore) but no
LLM tokens are burned. Real LLM execution is operator-driven.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from jig.intent import Intent
from jig.reviewers import (
    ARCHITECTURAL_REVIEWER_ID,
    BONES_REVIEWER_ID,
    LlmReviewerPending,
    PERFORMANCE_REVIEWER_ID,
    SECURITY_REVIEWER_ID,
    ReviewerComment,
    ReviewerCommentType,
    Severity,
    dispatch_for_cadence,
    dispatch_with_llm_spawn,
)
from jig.schemas.arch import (
    Architecture,
    ContractsFile,
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
from jig.store.review_comments import ReviewCommentsStore
from jig.ticket import Ticket, WorkType


# ---- helpers -------------------------------------------------------------


def _intent() -> Intent:
    return Intent(
        problem="Federation-execution tests need a parseable architecture.",
        simplest_solution="Hand-write modules with the tier_hint we want to test.",
    )


def _write_arch(project_root: Path, *, tier: TierHint = TierHint.STANDARD) -> None:
    arch = Architecture(
        modules=[
            Module(
                id="catalog-ingest",
                title="Catalog Ingest",
                summary="Test module.",
                tier_hint=tier,
                intent=_intent(),
            )
        ],
    )
    path = architecture_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(arch.model_dump(mode="json")))


def _write_contracts(project_root: Path) -> None:
    contracts = ContractsFile(
        spec_version=1,
        module="catalog-ingest",
        integration_ac=[
            IntegrationAcceptance(
                capability="shopify-connect",
                must=["catalog flows into products collection"],
            )
        ],
    )
    path = module_contracts_path(project_root, "catalog-ingest")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(contracts.model_dump(mode="json")))


def _write_spec(project_root: Path, *, suite_id: str = "catalog") -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    spec = StructuredSpec(
        name=suite_id,
        summary="Test suite",
        capabilities=[
            Capability(
                id="shopify-connect",
                title="Shopify Connect",
                state=CapabilityState.PLANNED,
                summary="Integrate Shopify",
                acceptance_criteria=["Webhook signature validates"],
                created_at=now,
                last_updated=now,
                state_changed_at=now,
            ),
        ],
        generated_at=now,
    )
    path = suite_structured_path(project_root, suite_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(spec.model_dump(mode="json")))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_worktree(worktree: Path) -> None:
    worktree.mkdir(parents=True, exist_ok=True)
    _git(worktree, "init", "-b", "main")
    _git(worktree, "config", "user.email", "test@example.com")
    _git(worktree, "config", "user.name", "Test")
    _git(worktree, "config", "commit.gpgsign", "false")
    _git(worktree, "commit", "--allow-empty", "-m", "base")
    _git(worktree, "checkout", "-b", "jig/tb-fed")
    f = worktree / "x.py"
    f.write_text("x = 1\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-m", "head")


def _ticket(
    *,
    ticket_id: str = "tb-fed",
    layer: str | None = "mvp",
    labels: list[str] | None = None,
    dev_tier: str | None = None,
    contract_amendment: str | None = None,
    suite_id: str | None = "catalog",
) -> Ticket:
    return Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="federation-execution test",
        created_by="planner-pm",
        module_id="catalog-ingest",
        suite_id=suite_id,
        capability_ids=["shopify-connect"],
        layer=layer,
        labels=labels or [],
        dev_tier=dev_tier,
        contract_amendment=contract_amendment,
    )


class _FakeOrchestrator:
    """Mock orchestrator used to exercise the spawn-and-wait flow.

    Records each ``spawn_review_agent_for_id`` call and optionally
    posts canned comments to the ``ReviewCommentsStore`` to mimic an
    LLM reviewer's ``reviewer_post_comment`` MCP calls. Real LLM
    execution is operator-driven; tests verify the dispatcher's
    behaviour against this stub.
    """

    def __init__(
        self,
        *,
        injections: dict[str, list[ReviewerComment]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str, str]] = []  # (id, ticket_id, role_file)
        self._injections = injections or {}

    async def spawn_review_agent_for_id(
        self,
        *,
        reviewer_id: str,
        ticket,
        role_file: str,
        project_root: Path,
        worktree_path: Path | None = None,
    ) -> None:
        self.calls.append((reviewer_id, ticket.id, role_file))
        canned = self._injections.get(reviewer_id, [])
        if not canned:
            return
        store = ReviewCommentsStore(
            project_root / ".jig" / "store" / "review_comments.jsonl"
        )
        await store.load()
        for comment in canned:
            stamped = comment.model_copy(update={"ticket_id": ticket.id})
            await store.append(stamped)


# ---- LlmReviewerPending model -------------------------------------------


class TestLlmReviewerPendingModel:
    def test_roundtrip(self) -> None:
        pending = LlmReviewerPending(
            reviewer_id="reviewer-security",
            ticket_id="tb-fed",
            role_config_path="reviewer_security",
            project_root="/tmp/x",
            cadence="end_of_ticket",
        )
        payload = pending.model_dump(mode="json")
        restored = LlmReviewerPending.model_validate(payload)
        assert restored == pending

    def test_extra_forbid(self) -> None:
        with pytest.raises(Exception):
            LlmReviewerPending(
                reviewer_id="reviewer-security",
                ticket_id="tb-fed",
                role_config_path="reviewer_security",
                project_root="/tmp/x",
                stowaway="not-allowed",  # type: ignore[call-arg]
            )

    def test_default_cadence(self) -> None:
        pending = LlmReviewerPending(
            reviewer_id="reviewer-security",
            ticket_id="tb-fed",
            role_config_path="reviewer_security",
            project_root="/tmp/x",
        )
        assert pending.cadence == "end_of_ticket"


# ---- dispatch_for_cadence: pendings on end-of-ticket --------------------


class TestDispatchForCadenceQueuesLlmReviewers:
    @pytest.mark.asyncio
    async def test_security_label_queues_pending(self, tmp_path: Path) -> None:
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        out = await dispatch_for_cadence(
            _ticket(labels=["touches-auth"]),
            tmp_path,
            "end_of_ticket",
            worktree_path=worktree,
        )

        assert SECURITY_REVIEWER_ID in out
        pending = out[SECURITY_REVIEWER_ID]
        assert isinstance(pending, LlmReviewerPending)
        assert pending.reviewer_id == SECURITY_REVIEWER_ID
        assert pending.ticket_id == "tb-fed"
        assert pending.role_config_path == "reviewer_security"
        assert pending.cadence == "end_of_ticket"

    @pytest.mark.asyncio
    async def test_per_commit_never_queues_llm_reviewers(
        self, tmp_path: Path
    ) -> None:
        """Per-commit latency budget rules out LLM reviewers."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        out = await dispatch_for_cadence(
            _ticket(labels=["touches-auth"]),
            tmp_path,
            "per_commit",
            worktree_path=worktree,
        )

        # Mechanical reviewers come back as comment lists; no LLM
        # pendings at per_commit.
        for value in out.values():
            assert isinstance(value, list), (
                f"per-commit dispatch must not queue LLM pendings; got "
                f"{type(value).__name__}"
            )
        assert SECURITY_REVIEWER_ID not in out
        assert PERFORMANCE_REVIEWER_ID not in out
        assert ARCHITECTURAL_REVIEWER_ID not in out

    @pytest.mark.asyncio
    async def test_architectural_amendment_queues_pending(
        self, tmp_path: Path
    ) -> None:
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        out = await dispatch_for_cadence(
            _ticket(contract_amendment="extends ingest contract"),
            tmp_path,
            "end_of_ticket",
            worktree_path=worktree,
        )

        assert ARCHITECTURAL_REVIEWER_ID in out
        assert isinstance(out[ARCHITECTURAL_REVIEWER_ID], LlmReviewerPending)


# ---- dispatch_with_llm_spawn: end-to-end with mocked orchestrator -------


class TestDispatchWithLlmSpawn:
    @pytest.mark.asyncio
    async def test_calls_orchestrator_for_each_pending(
        self, tmp_path: Path
    ) -> None:
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        orch = _FakeOrchestrator()

        await dispatch_with_llm_spawn(
            _ticket(labels=["touches-auth"]),
            tmp_path,
            "end_of_ticket",
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
        )

        # The mock orchestrator was asked to spawn the security reviewer.
        ids_spawned = [c[0] for c in orch.calls]
        assert SECURITY_REVIEWER_ID in ids_spawned

    @pytest.mark.asyncio
    async def test_merges_mechanical_and_llm_results(
        self, tmp_path: Path
    ) -> None:
        """Mechanical comments + LLM-spawned comments come back in one map."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        canned = ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.IMPORTANT,
            reviewer=SECURITY_REVIEWER_ID,
            prose=(
                "Block 3 verification: spawned reviewer-security agent "
                "posted a comment about missing token validation."
            ),
            file="ingest.py",
            line=5,
            confidence=0.85,
        )

        orch = _FakeOrchestrator(injections={SECURITY_REVIEWER_ID: [canned]})

        out = await dispatch_with_llm_spawn(
            _ticket(labels=["touches-auth"]),
            tmp_path,
            "end_of_ticket",
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
        )

        # Mechanical reviewer (contract-compliance is in the MVP defaults)
        # came back as a list — proves the mechanical branch ran.
        assert BONES_REVIEWER_ID in out
        assert isinstance(out[BONES_REVIEWER_ID], list)

        # LLM reviewer's canned comment landed via spawn-and-read.
        assert SECURITY_REVIEWER_ID in out
        assert isinstance(out[SECURITY_REVIEWER_ID], list)
        assert len(out[SECURITY_REVIEWER_ID]) == 1
        assert "missing token validation" in out[SECURITY_REVIEWER_ID][0].prose
        assert out[SECURITY_REVIEWER_ID][0].ticket_id == "tb-fed"

    @pytest.mark.asyncio
    async def test_pending_with_no_comments_yields_empty_list(
        self, tmp_path: Path
    ) -> None:
        """A reviewer that runs and finds nothing still appears in result."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        orch = _FakeOrchestrator()  # no injections

        out = await dispatch_with_llm_spawn(
            _ticket(labels=["touches-auth"]),
            tmp_path,
            "end_of_ticket",
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
        )

        assert SECURITY_REVIEWER_ID in out
        assert out[SECURITY_REVIEWER_ID] == []

    @pytest.mark.asyncio
    async def test_no_pendings_no_orchestrator_calls(
        self, tmp_path: Path
    ) -> None:
        """Vanilla MVP ticket with no triggers → no orchestrator spawns."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        orch = _FakeOrchestrator()

        out = await dispatch_with_llm_spawn(
            _ticket(),
            tmp_path,
            "end_of_ticket",
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
        )

        assert orch.calls == []
        # Mechanical reviewers still ran.
        assert BONES_REVIEWER_ID in out

    @pytest.mark.asyncio
    async def test_multiple_pendings_all_spawned(
        self, tmp_path: Path
    ) -> None:
        """A ticket triggering security AND architectural gets both spawned."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        orch = _FakeOrchestrator()

        await dispatch_with_llm_spawn(
            _ticket(
                labels=["touches-auth"],
                contract_amendment="touches contract",
            ),
            tmp_path,
            "end_of_ticket",
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
        )

        ids = sorted(c[0] for c in orch.calls)
        assert SECURITY_REVIEWER_ID in ids
        assert ARCHITECTURAL_REVIEWER_ID in ids

    @pytest.mark.asyncio
    async def test_does_not_double_count_pre_existing_comments(
        self, tmp_path: Path
    ) -> None:
        """A stale row from a prior cycle must not be re-attributed."""
        _write_arch(tmp_path)
        _write_contracts(tmp_path)
        _write_spec(tmp_path)
        worktree = tmp_path / ".jig" / "worktrees" / "tb-fed"
        _init_worktree(worktree)

        # Pre-populate the store with a stale comment from an earlier
        # cycle. A correct dispatch_with_llm_spawn must not return this.
        stale = ReviewerComment(
            type=ReviewerCommentType.PATTERN_DIVERGENCE,
            severity=Severity.NOTABLE,
            reviewer=SECURITY_REVIEWER_ID,
            prose="Stale finding from prior cycle",
            ticket_id="tb-fed",
            file="ingest.py",
            line=1,
            confidence=0.7,
        )
        store_path = tmp_path / ".jig" / "store" / "review_comments.jsonl"
        store_path.parent.mkdir(parents=True, exist_ok=True)
        prev_store = ReviewCommentsStore(store_path)
        await prev_store.load()
        await prev_store.append(stale)

        orch = _FakeOrchestrator()  # no fresh injections

        out = await dispatch_with_llm_spawn(
            _ticket(labels=["touches-auth"]),
            tmp_path,
            "end_of_ticket",
            orch,  # type: ignore[arg-type]
            worktree_path=worktree,
        )

        # The stale comment must NOT appear in this cycle's result.
        assert out[SECURITY_REVIEWER_ID] == []
