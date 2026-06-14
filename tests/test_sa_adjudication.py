"""SA adjudication of persistent blocking findings (review-severity-binary, step 6).

The behavior flip: a blocking finding that survives SURVIVAL_THRESHOLD
fix attempts — or a round-cap trip — escalates to the profile's SA for
adjudication instead of failing the ticket. A reviewer can no longer
unilaterally fail a ticket.

Pins, per feature-work/review-severity-binary/plan.md step 6:

- each verdict path: dismissed binds across cycles AND across a
  simulated restart (the ack carries the persistence key and is
  JSONL-persisted); uphold_guidance grants a round and resets survival
  only; uphold_fail fails the ticket;
- the gate's binding-dismissal filter keys off the persisted ack, not
  in-memory state (negative state-lifetime test);
- SA spawn errors and zero-verdict runs fail closed;
- the sa_adjudicate_finding MCP tool validates finding ids and verdict
  values and records into the collector.
"""

from __future__ import annotations

from pathlib import Path

import pytest


from jig.config import Config, OrchestratorSection, save_config
from jig.orchestrator import (
    AdjudicationOutcome,
    Orchestrator,
    _persistence_key,
)
from jig.project import Project
from jig.reviewers.comment import (
    ReviewerComment,
    ReviewerCommentType,
    Severity,
)
from jig.store import MessageBus
from jig.store.finding_acks import FindingAcksStore
from jig.store.memory import MemoryStore
from jig.store.review_comments import ReviewCommentsStore
from jig.store.tickets import TicketStore
from jig.store.threads import ThreadStore
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _seed_project(root: Path) -> None:
    cfg_dir = root / ".jig"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(
        project=Project(id="test-adjudicate", name="test-adjudicate", path=str(root)),
        orchestrator=OrchestratorSection(run_review_federation=True),
    )
    save_config(root, cfg)


async def _make_orch(tmp_path: Path) -> Orchestrator:
    _seed_project(tmp_path)
    store_dir = tmp_path / ".jig" / "store"
    store_dir.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator(project_path=tmp_path)
    orch.tickets = TicketStore(store_dir / "tickets.jsonl")
    orch.threads = ThreadStore(store_dir / "comments.jsonl")
    orch.bus = MessageBus(store_dir / "messages.jsonl")
    orch.review_comments = ReviewCommentsStore(store_dir / "review_comments.jsonl")
    orch.memory = MemoryStore(store_dir)
    orch._project = Project(
        id="test-adjudicate", name="test-adjudicate", path=str(tmp_path)
    )
    await orch.tickets.load()
    await orch.threads.load()
    await orch.bus.load()
    await orch.review_comments.load()
    await orch.memory.load()
    return orch


async def _make_ticket(orch: Orchestrator, ticket_id: str = "tb-adj") -> Ticket:
    assert orch.tickets is not None
    t = Ticket(
        id=ticket_id,
        work_type=WorkType.FEATURE,
        title="adjudication test",
        created_by="planner-pm",
        description=TICKET_AC_PLACEHOLDER,
    )
    await orch.tickets.create(t)
    fresh = await orch.tickets.get(ticket_id)
    assert fresh is not None
    return fresh


def _important(file: str = "src/a.py", cycle: int = 0) -> ReviewerComment:
    return ReviewerComment(
        type=ReviewerCommentType("pattern-divergence"),
        severity=Severity("important"),
        reviewer="reviewer-generalist",
        prose="persistent defect " + "p" * 40,
        confidence=0.8,
        file=file,
        cycle=cycle,
    )


async def _seed_finding_history(
    orch: Orchestrator, ticket_id: str, comment: ReviewerComment, cycles: int = 3
) -> None:
    """Post the same finding across N cycles into the review store."""
    assert orch.review_comments is not None
    for c in range(cycles):
        dup = comment.model_copy(update={"cycle": c, "ticket_id": ticket_id})
        await orch.review_comments.append(dup)
    await orch.review_comments.load()


class _FakeSAResult:
    """Patch for _run_agent_with_analytics: write canned verdicts into
    the spawn context's adjudication collector."""

    def __init__(self, verdicts_by_rc: dict | None = None, raises: bool = False):
        self.verdicts_by_rc = verdicts_by_rc or {}
        self.raises = raises
        self.spawned_with = None

    async def __call__(self, ctx, **kwargs):
        self.spawned_with = ctx
        if self.raises:
            raise RuntimeError("sa spawn exploded")
        collector = ctx.adjudication_collector
        assert collector is not None
        for rc_n in collector["escalated"]:
            if rc_n in self.verdicts_by_rc:
                collector["verdicts"][rc_n] = self.verdicts_by_rc[rc_n]
            elif "*" in self.verdicts_by_rc:
                collector["verdicts"][rc_n] = self.verdicts_by_rc["*"]

        from jig.agent import RunAgentResult

        return RunAgentResult(status="success", final_text="adjudicated")


async def _adjudicate(
    orch: Orchestrator,
    ticket: Ticket,
    tmp_path: Path,
    fake: _FakeSAResult,
    *,
    keys: set[str] | None = None,
    cap_trip: bool = False,
) -> AdjudicationOutcome:
    orch._run_agent_with_analytics = fake  # type: ignore[method-assign]
    finding = _important()
    await _seed_finding_history(orch, ticket.id, finding)
    key = _persistence_key(finding)
    phase_key = (ticket.id, "review")
    orch._survival_counts[phase_key] = {key: 2}
    orch._prev_blocking_keys[phase_key] = {key}
    return await orch._run_sa_adjudication(
        ticket_id=ticket.id,
        ticket=ticket,
        phase_name="review",
        worktree=tmp_path,
        escalated_keys=keys if keys is not None else {key},
        cycle=3,
        cap_trip=cap_trip,
    )


class TestVerdictPaths:
    async def test_dismissed_persists_binding_ack(self, tmp_path: Path) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        fake = _FakeSAResult(
            {"*": {"verdict": "dismissed", "rationale": "taste, not defect"}}
        )

        outcome = await _adjudicate(orch, ticket, tmp_path, fake)

        assert not outcome.fail
        assert outcome.dismissed_keys == {_persistence_key(_important())}
        # The ack is on disk with the coarse key — binding across restarts.
        acks_store = FindingAcksStore(
            tmp_path / ".jig" / "store" / "finding_acks.jsonl"
        )
        await acks_store.load()
        acks = await acks_store.for_ticket(ticket.id)
        dismissals = [a for a in acks if a.kind == "dismissed"]
        assert len(dismissals) == 1
        assert dismissals[0].persistence_key == _persistence_key(_important())

    async def test_dismissal_filters_gate_after_simulated_restart(
        self, tmp_path: Path
    ) -> None:
        """Negative state-lifetime test: in-memory counters reset, but the
        gate filter keys off the persisted ack and still binds."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        fake = _FakeSAResult({"*": {"verdict": "dismissed", "rationale": "no"}})
        await _adjudicate(orch, ticket, tmp_path, fake)

        # Simulated restart: a fresh orchestrator instance, no in-memory state.
        orch2 = await _make_orch(tmp_path)
        assert orch2._survival_counts == {}
        blocking = [_important(cycle=4)]
        kept = await orch2._filter_dismissed_keys(ticket.id, blocking)
        assert kept == []

    async def test_uphold_guidance_resets_survival_and_carries_guidance(
        self, tmp_path: Path
    ) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        fake = _FakeSAResult(
            {
                "*": {
                    "verdict": "uphold_guidance",
                    "rationale": "real",
                    "guidance": "extract the helper into util.py",
                }
            }
        )

        outcome = await _adjudicate(orch, ticket, tmp_path, fake)

        assert not outcome.fail
        assert not outcome.dismissed_keys
        assert len(outcome.guidance) == 1
        assert "util.py" in outcome.guidance[0]["guidance"]
        key = _persistence_key(_important())
        assert orch._survival_counts[(ticket.id, "review")][key] == 0

    async def test_uphold_fail_fails(self, tmp_path: Path) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        fake = _FakeSAResult(
            {"*": {"verdict": "uphold_fail", "rationale": "unresolvable here"}}
        )

        outcome = await _adjudicate(orch, ticket, tmp_path, fake)
        assert outcome.fail

    async def test_missing_verdict_treated_as_uphold_guidance(
        self, tmp_path: Path
    ) -> None:
        """Partial SA response: the missing finding is upheld (never waved
        through), granting the round without failing on SA sloppiness."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        # Zero verdicts overall fails closed, so seed one real verdict
        # for one finding and leave a second finding unanswered.
        f1 = _important(file="src/a.py")
        f2 = _important(file="src/b.py")
        assert orch.review_comments is not None
        for c in range(3):
            await orch.review_comments.append(
                f1.model_copy(update={"cycle": c, "ticket_id": ticket.id})
            )
            await orch.review_comments.append(
                f2.model_copy(update={"cycle": c, "ticket_id": ticket.id})
            )
        await orch.review_comments.load()
        k1, k2 = _persistence_key(f1), _persistence_key(f2)
        phase_key = (ticket.id, "review")
        orch._survival_counts[phase_key] = {k1: 2, k2: 2}
        orch._prev_blocking_keys[phase_key] = {k1, k2}

        class _PartialSA(_FakeSAResult):
            async def __call__(self, ctx, **kwargs):
                collector = ctx.adjudication_collector
                # Answer exactly one of the two escalated findings.
                first = sorted(collector["escalated"])[0]
                collector["verdicts"][first] = {
                    "verdict": "dismissed",
                    "rationale": "fine",
                }
                from jig.agent import RunAgentResult

                return RunAgentResult(status="success", final_text="partial")

        orch._run_agent_with_analytics = _PartialSA()  # type: ignore[method-assign]
        outcome = await orch._run_sa_adjudication(
            ticket_id=ticket.id,
            ticket=ticket,
            phase_name="review",
            worktree=tmp_path,
            escalated_keys={k1, k2},
            cycle=3,
            cap_trip=False,
        )

        assert not outcome.fail
        assert len(outcome.dismissed_keys) == 1
        assert len(outcome.guidance) == 1  # the unanswered one, upheld


class TestFailClosed:
    async def test_spawn_error_fails_closed(self, tmp_path: Path) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        fake = _FakeSAResult(raises=True)
        outcome = await _adjudicate(orch, ticket, tmp_path, fake)
        assert outcome.fail

    async def test_zero_verdicts_fails_closed(self, tmp_path: Path) -> None:
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        fake = _FakeSAResult({})  # SA never calls the tool
        outcome = await _adjudicate(orch, ticket, tmp_path, fake)
        assert outcome.fail


class TestMcpTool:
    def test_handler_validates_and_records(self) -> None:
        from jig.mcp_server import handle_sa_adjudicate_finding

        collector: dict = {"escalated": {"RC-1": "r|t|f"}, "verdicts": {}}

        # Unknown finding id → error, nothing recorded.
        err = handle_sa_adjudicate_finding(
            collector, {"finding_id": "RC-9", "verdict": "dismissed"}
        )
        assert err.get("is_error")
        assert collector["verdicts"] == {}

        # Bad verdict → error, nothing recorded.
        err = handle_sa_adjudicate_finding(
            collector, {"finding_id": "RC-1", "verdict": "maybe"}
        )
        assert err.get("is_error")
        assert collector["verdicts"] == {}

        # Valid call records the verdict shape.
        ok = handle_sa_adjudicate_finding(
            collector,
            {
                "finding_id": "RC-1",
                "verdict": "uphold_guidance",
                "rationale": "real",
                "guidance": "do x",
            },
        )
        assert not ok.get("is_error")
        assert collector["verdicts"]["RC-1"] == {
            "verdict": "uphold_guidance",
            "rationale": "real",
            "guidance": "do x",
        }


class TestEscalationTrigger:
    async def test_keys_past_survival_threshold(self, tmp_path: Path) -> None:
        from jig.orchestrator import SURVIVAL_THRESHOLD

        orch = await _make_orch(tmp_path)
        orch._survival_counts[("t1", "review")] = {
            "k-fresh": 0,
            "k-once": 1,
            "k-persistent": SURVIVAL_THRESHOLD,
            "k-very": SURVIVAL_THRESHOLD + 1,
        }
        keys = orch._keys_past_survival_threshold("t1", "review")
        assert keys == {"k-persistent", "k-very"}
        assert orch._keys_past_survival_threshold("t2", "review") == set()

    async def test_cap_trip_widens_escalation_to_all_outstanding(
        self, tmp_path: Path
    ) -> None:
        """On a cap trip the SA adjudicates every outstanding blocking key,
        not only the persistent ones."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)
        f1 = _important(file="src/a.py")
        f2 = _important(file="src/b.py")
        assert orch.review_comments is not None
        for c in range(2):
            await orch.review_comments.append(
                f1.model_copy(update={"cycle": c, "ticket_id": ticket.id})
            )
            await orch.review_comments.append(
                f2.model_copy(update={"cycle": c, "ticket_id": ticket.id})
            )
        await orch.review_comments.load()
        k1, k2 = _persistence_key(f1), _persistence_key(f2)
        phase_key = (ticket.id, "review")
        orch._survival_counts[phase_key] = {k1: 2, k2: 0}
        orch._prev_blocking_keys[phase_key] = {k1, k2}

        fake = _FakeSAResult({"*": {"verdict": "dismissed", "rationale": "ok"}})
        orch._run_agent_with_analytics = fake  # type: ignore[method-assign]
        outcome = await orch._run_sa_adjudication(
            ticket_id=ticket.id,
            ticket=ticket,
            phase_name="review",
            worktree=tmp_path,
            escalated_keys={k1},  # only the persistent one passed in...
            cycle=2,
            cap_trip=True,  # ...but the cap trip widens to k2 as well
        )
        assert outcome.dismissed_keys == {k1, k2}


class TestSAToolAllowlisted:
    @pytest.mark.parametrize("role", ["sa", "sa_mvp", "sa_v2"])
    def test_sa_roles_allow_adjudicate_tool(self, tmp_path: Path, role: str) -> None:
        """Every strict_tools SA-family role must allow-list
        sa_adjudicate_finding. The SDK gates MCP tools by allowed_tools under
        bypassPermissions, so a missing entry means the agent cannot call the
        tool and adjudication fails closed (jobs 548 small-profile sa, 566
        medium-profile sa_mvp; sa_v2 defensively)."""
        from jig.persistence import load_role

        cfg = load_role(tmp_path, role)
        if cfg.strict_tools:
            assert "sa_adjudicate_finding" in cfg.allowed_tools, (
                f"{role} is strict_tools but does not allow-list "
                "sa_adjudicate_finding — adjudication would fail closed"
            )


class TestAdjudicationPromptCap:
    def test_history_is_capped_with_overflow_pointer(self) -> None:
        """A finding re-raised many times must not produce an unbounded
        adjudication prompt — history is capped, prose truncated, and the
        omitted count points at the stores (roborev job 590)."""
        from jig.prompt_builder import (
            _ADJUDICATION_PROSE_MAX_CHARS,
            _MAX_ADJUDICATION_HISTORY,
            _adjudication_section,
        )

        n = _MAX_ADJUDICATION_HISTORY + 5
        bundle = {
            "findings": [
                {
                    "finding_id": "RC-1",
                    "file": "src/a.py",
                    "severity": "important",
                    "reviewer": "reviewer-generalist",
                    "survival_count": n,
                    "prose": "P" * 2000,
                    "history": [
                        {
                            "cycle": c,
                            "kind": "raised",
                            "author": "reviewer-generalist",
                            "prose": "H" * 2000,
                        }
                        for c in range(n)
                    ],
                }
            ]
        }

        out = _adjudication_section(bundle)

        # Only the most-recent N history entries are rendered.
        assert out.count("- cycle ") == _MAX_ADJUDICATION_HISTORY
        # Overflow is summarized with a pointer to the stores.
        assert "earlier history entr" in out
        assert "review_comments.jsonl" in out
        # The most recent cycle is kept; the oldest is dropped.
        assert f"cycle {n - 1} " in out
        assert "cycle 0 " not in out
        # No rendered prose exceeds the truncation budget. The line prefix
        # ("  - cycle N (kind, author): ") adds bounded overhead; allow for it.
        for line in out.splitlines():
            assert len(line) <= _ADJUDICATION_PROSE_MAX_CHARS + 100

    def test_short_history_unchanged(self) -> None:
        from jig.prompt_builder import _adjudication_section

        bundle = {
            "findings": [
                {
                    "finding_id": "RC-1",
                    "file": "src/a.py",
                    "severity": "important",
                    "reviewer": "reviewer-generalist",
                    "survival_count": 2,
                    "prose": "short finding",
                    "history": [
                        {"cycle": 0, "kind": "raised", "author": "r", "prose": "x"},
                        {
                            "cycle": 1,
                            "kind": "addressed",
                            "author": "dev",
                            "prose": "y",
                        },
                    ],
                }
            ]
        }
        out = _adjudication_section(bundle)
        assert "omitted" not in out
        assert out.count("- cycle ") == 2


class TestAdjudicationAnalytics:
    async def test_sa_adjudication_event_carries_cycle(self, tmp_path: Path) -> None:
        """The SAAdjudication analytics event records the cycle it ran on, so
        the event log can place it on the ticket timeline (issue #175)."""
        orch = await _make_orch(tmp_path)
        ticket = await _make_ticket(orch)

        emitted: list = []

        class _Emitter:
            def emit_nowait(self, event) -> None:
                emitted.append(event)

        orch._analytics_emitter = _Emitter()  # type: ignore[assignment]
        fake = _FakeSAResult({"*": {"verdict": "dismissed", "rationale": "no"}})
        await _adjudicate(orch, ticket, tmp_path, fake)  # passes cycle=3

        adj = [e for e in emitted if e.kind == "sa_adjudication"]
        assert len(adj) == 1
        assert adj[0].cycle == 3


class TestBotReviewFixes:
    async def test_dismissal_rerun_does_not_inflate_survival(
        self, tmp_path: Path
    ) -> None:
        """P0 (#173 bot item 1): when the SA dismisses all escalated findings
        and the review phase re-runs, _prev_blocking_keys must be cleared so a
        surviving non-dismissed finding is not counted as having survived a
        fix attempt on the system-driven re-run."""
        from jig.orchestrator import _persistence_key

        def _imp(file: str) -> ReviewerComment:
            return ReviewerComment(
                type=ReviewerCommentType("pattern-divergence"),
                severity=Severity("important"),
                reviewer="reviewer-generalist",
                prose="finding " + "d" * 40,
                confidence=0.8,
                file=file,
            )

        orch = await _make_orch(tmp_path)
        phase_key = ("t", "review")
        a, b = _imp("a.py"), _imp("b.py")
        key_a, key_b = _persistence_key(a), _persistence_key(b)
        # key-A dismissed, key-B still blocking with survival=1 from a real round.
        orch._prev_blocking_keys[phase_key] = {key_a, key_b}
        orch._survival_counts[phase_key] = {key_a: 2, key_b: 1}

        # Simulate the dismiss-all reset (what the branch now does before continue).
        orch._prev_blocking_keys[phase_key] = set()

        # The next blocked round re-raises key-B. With _prev_blocking_keys
        # cleared, key-B is treated as fresh (not a survival), so its count
        # does not climb on the system-only re-run.
        orch._record_blocking_persistence(
            ticket_id="t", phase_key=phase_key, blocking=[b], cycle=4
        )
        assert orch._survival_counts[phase_key][key_b] == 1

    def test_duplicate_verdict_rejected(self) -> None:
        """#173 bot item 2: a second sa_adjudicate_finding call for the same
        finding must error, not silently overwrite a binding verdict."""
        from jig.mcp_server import handle_sa_adjudicate_finding

        collector: dict = {"escalated": {"RC-1": "k"}, "verdicts": {}}
        ok = handle_sa_adjudicate_finding(
            collector, {"finding_id": "RC-1", "verdict": "dismissed", "rationale": "x"}
        )
        assert not ok.get("is_error")
        # Second call (e.g. SA reconsiders) is rejected; the dismissal stands.
        dup = handle_sa_adjudicate_finding(
            collector,
            {"finding_id": "RC-1", "verdict": "uphold_fail", "rationale": "y"},
        )
        assert dup.get("is_error")
        assert collector["verdicts"]["RC-1"]["verdict"] == "dismissed"
