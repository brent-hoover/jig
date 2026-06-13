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
    orch.memory = MemoryStore(store_dir / "memory.jsonl")
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
    def test_sa_role_allows_adjudicate_tool(self, tmp_path: Path) -> None:
        """The SA role is strict_tools; the SDK gates MCP tools by the
        allowed_tools allowlist. sa_adjudicate_finding must be listed or the
        agent cannot call it and every adjudication fails closed (job 548).
        This is the allowlist path the handler-only unit test missed."""
        from jig.persistence import load_role

        cfg = load_role(tmp_path, "sa")
        assert cfg.strict_tools is True
        assert "sa_adjudicate_finding" in cfg.allowed_tools
