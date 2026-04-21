"""Unit tests for run_agent under streaming input mode."""

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from jig.capabilities import (
    BashToolParams,
    CapabilityDeclaration,
    CapabilityPaths,
    CapabilityToolParams,
    CapabilityTools,
)
from jig.models import PhaseConfig, RoleConfig
from jig.project import Project
from jig.runtime import AgentSpawnContext, SpawnReason
from jig.store import MessageBus
from jig.store.memory import MemoryStore
from jig.store.threads import ThreadStore
from jig.store.tickets import TicketStore
from jig.ticket import Ticket, WorkType


async def _wait_for_subscription(bus, topic: str, timeout: float = 2.0) -> None:
    """Poll the bus's internal subscriber list until the topic has at least one
    queue registered.  Touching a private attribute is intentional here — this
    is test-only synchronisation that needs to observe internal state before
    publishing, to avoid a race where the publish precedes the subscribe."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if bus._subscribers.get(topic):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"no subscriber for {topic} within {timeout}s")


async def _make_context(tmp_path: Path) -> AgentSpawnContext:
    tickets = TicketStore(tmp_path / "tickets.jsonl")
    await tickets.load()
    threads = ThreadStore(tmp_path / "comments.jsonl")
    await threads.load()
    memory = MemoryStore(tmp_path)
    await memory.load()
    bus = MessageBus(tmp_path / "messages.jsonl")
    await bus.load()
    t = Ticket(
        work_type=WorkType.REFACTOR,
        title="t",
        created_by="o",
        description="do it",
    )
    tid = await tickets.create(t)
    loaded = await tickets.get(tid)
    assert loaded is not None
    return AgentSpawnContext(
        role="dev",
        role_cfg=RoleConfig(role="dev", phase_prompt="be dev"),
        spawn_reason=SpawnReason.PHASE_PRIMARY,
        ticket=loaded,
        parent=None,
        worktree_path=tmp_path / "worktree",
        project=Project(
            id="p",
            name="p",
            path=str(tmp_path),
            language="python",
            package_manager="uv",
        ),
        tickets=tickets,
        threads=threads,
        memory=memory,
        bus=bus,
    )


def _fake_result_message():
    class _M:
        result = "done"
        num_turns = 1
        duration_ms = 1
        total_cost_usd = 0.0

    return _M()


@pytest.mark.asyncio
async def test_run_agent_builds_initial_prompt(tmp_path: Path) -> None:
    ctx = await _make_context(tmp_path)

    captured_prompt_iter = None

    async def fake_query(prompt, options, **kwargs):
        nonlocal captured_prompt_iter
        captured_prompt_iter = prompt
        async for _turn in prompt:
            yield _fake_result_message()
            return

    from jig import agent as agent_module

    with patch.object(agent_module, "query", fake_query):
        await agent_module.run_agent(ctx)

    assert captured_prompt_iter is not None


@pytest.mark.asyncio
async def test_run_agent_yields_incoming_bus_events(tmp_path: Path) -> None:
    ctx = await _make_context(tmp_path)
    seen_turns: list[str] = []

    async def fake_query(prompt, options, **kwargs):
        async for turn in prompt:
            seen_turns.append(turn if isinstance(turn, str) else str(turn))
            if len(seen_turns) >= 2:
                yield _fake_result_message()
                return

    async def publish_delayed():
        await _wait_for_subscription(ctx.bus, f"tickets.{ctx.ticket.id}")
        from jig.store import Message, MessageType

        await ctx.bus.publish(
            Message(
                sender="qa",
                to="dev",
                type=MessageType.CONTEXT_UPDATE,
                payload={
                    "kind": "comment_posted",
                    "author": "qa",
                    "ticket_id": ctx.ticket.id,
                    "content": "did you handle edge X?",
                },
                topic=f"tickets.{ctx.ticket.id}",
            )
        )
        await ctx.bus.publish(
            Message(
                sender="dev",
                to="broadcast",
                type=MessageType.CONTEXT_UPDATE,
                payload={
                    "kind": "ticket_updated",
                    "ticket_id": ctx.ticket.id,
                    "status": "resolved",
                },
                topic=f"tickets.{ctx.ticket.id}",
            )
        )

    from jig import agent as agent_module

    with patch.object(agent_module, "query", fake_query):
        await asyncio.gather(
            agent_module.run_agent(ctx),
            publish_delayed(),
        )

    assert len(seen_turns) == 2
    assert "did you handle edge X" in seen_turns[1]


# ---- Phase 5 Task F: capability policy materialisation --------------------


class TestMaterializeCapabilityPolicy:
    """``_materialize_capability_policy`` is invoked pre-spawn so the
    hook scripts (Task G) and Claude Code (for settings.json) find
    their inputs already on disk before the agent starts."""

    @pytest.fixture(autouse=True)
    def _fake_sandbox(self, monkeypatch) -> None:
        """Make ``sandbox_available()`` return True for these tests.

        Materialisation is gated on the sandbox being present — the
        hook paths baked into ``.claude/settings.json`` are container-
        absolute (``/jig/bin/check-*``), so emitting them on the host
        would give Claude Code ENOENT on every guarded tool call.
        These tests exercise the success path, so we force the gate
        open; :class:`TestMaterializeWithoutSandbox` covers the
        short-circuit."""
        monkeypatch.setenv("JIG_IN_CONTAINER", "1")

    def test_noop_when_no_declarations(self, tmp_path: Path) -> None:
        """A spawn with neither role.capabilities nor
        phase.capability_overrides shouldn't touch the filesystem —
        materialising an empty settings.json would clobber any
        hand-maintained one in the worktree."""
        from jig import agent as agent_module

        ctx = AgentSpawnContext(
            role="dev",
            role_cfg=RoleConfig(role="dev", phase_prompt="x"),
            spawn_reason=SpawnReason.PHASE_PRIMARY,
            ticket=Ticket(
                work_type=WorkType.REFACTOR,
                title="t",
                created_by="o",
                description="d",
            ),
            parent=None,
            worktree_path=tmp_path / "worktree",
            project=Project(
                id="p",
                name="p",
                path=str(tmp_path),
                language="python",
                package_manager="uv",
            ),
            tickets=None,  # type: ignore[arg-type]
            threads=None,  # type: ignore[arg-type]
            memory=None,  # type: ignore[arg-type]
            bus=None,  # type: ignore[arg-type]
        )
        agent_module._materialize_capability_policy(ctx)

        # No policy files materialised.
        assert not (tmp_path / "worktree" / ".claude").exists()
        assert not (tmp_path / ".jig" / "runtime").exists()

    def test_role_capabilities_materialised(self, tmp_path: Path) -> None:
        """A role that declares capabilities gets both rules.json and
        .claude/settings.json written pre-spawn."""
        from jig import agent as agent_module

        role_cfg = RoleConfig(
            role="dev",
            phase_prompt="x",
            capabilities=CapabilityDeclaration(
                tools=CapabilityTools(allowed=["Read", "Write"]),
                tool_params=CapabilityToolParams(
                    Bash=BashToolParams(deny_patterns=["^rm -rf"])
                ),
                paths=CapabilityPaths(
                    writable=["ticket://worktree/**"],
                    denied=[".jig/spec/**"],
                ),
            ),
        )
        ticket = Ticket(
            work_type=WorkType.REFACTOR,
            title="t",
            created_by="o",
            description="d",
        )
        ctx = AgentSpawnContext(
            role="dev",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.PHASE_PRIMARY,
            ticket=ticket,
            parent=None,
            worktree_path=tmp_path / "worktree",
            project=Project(
                id="p",
                name="p",
                path=str(tmp_path),
                language="python",
                package_manager="uv",
            ),
            tickets=None,  # type: ignore[arg-type]
            threads=None,  # type: ignore[arg-type]
            memory=None,  # type: ignore[arg-type]
            bus=None,  # type: ignore[arg-type]
        )

        agent_module._materialize_capability_policy(ctx)

        rules_path = tmp_path / ".jig" / "runtime" / ticket.id / "policy" / "rules.json"
        settings_path = tmp_path / "worktree" / ".claude" / "settings.json"

        assert rules_path.exists()
        assert settings_path.exists()

        rules = json.loads(rules_path.read_text())
        assert rules["tools"]["allowed"] == ["Read", "Write"]
        assert rules["bash"]["deny_patterns"] == ["^rm -rf"]
        assert rules["paths"]["writable"] == ["ticket://worktree/**"]
        assert rules["paths"]["denied"] == [".jig/spec/**"]

        settings = json.loads(settings_path.read_text())
        # All three hooks register: bash (deny_patterns), write (writable +
        # denied), read (denied).
        commands = sorted(
            m["hooks"][0]["command"] for m in settings["hooks"]["PreToolUse"]
        )
        assert any(c.endswith("/check-bash") for c in commands)
        assert any(c.endswith("/check-write") for c in commands)
        assert any(c.endswith("/check-path") for c in commands)

    def test_phase_overrides_merged_into_rules(self, tmp_path: Path) -> None:
        """A phase's ``capability_overrides`` union with the role's
        base declaration at materialisation time — demonstrates the
        merge rule documented on ``merge_declarations``."""
        from jig import agent as agent_module

        role_cfg = RoleConfig(
            role="dev",
            phase_prompt="x",
            capabilities=CapabilityDeclaration(
                tools=CapabilityTools(allowed=["Read"]),
            ),
        )
        phase = PhaseConfig(
            name="tight-phase",
            role="dev",
            capability_overrides=CapabilityDeclaration(
                tools=CapabilityTools(allowed=["Write"]),
                paths=CapabilityPaths(denied=[".jig/spec/**"]),
            ),
        )
        ticket = Ticket(
            work_type=WorkType.REFACTOR,
            title="t",
            created_by="o",
            description="d",
        )
        ctx = AgentSpawnContext(
            role="dev",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.PHASE_PRIMARY,
            ticket=ticket,
            parent=None,
            worktree_path=tmp_path / "worktree",
            project=Project(
                id="p",
                name="p",
                path=str(tmp_path),
                language="python",
                package_manager="uv",
            ),
            tickets=None,  # type: ignore[arg-type]
            threads=None,  # type: ignore[arg-type]
            memory=None,  # type: ignore[arg-type]
            bus=None,  # type: ignore[arg-type]
            phase=phase,
        )

        agent_module._materialize_capability_policy(ctx)

        rules_path = tmp_path / ".jig" / "runtime" / ticket.id / "policy" / "rules.json"
        rules = json.loads(rules_path.read_text())
        assert rules["tools"]["allowed"] == ["Read", "Write"]
        assert rules["paths"]["denied"] == [".jig/spec/**"]

    def test_io_failure_is_non_fatal(self, tmp_path: Path, caplog) -> None:
        """Task F's error-handling contract: a materialisation glitch
        should log + continue, not break the spawn. Task G will tighten
        this once hooks are the primary enforcement layer."""
        from jig import agent as agent_module

        role_cfg = RoleConfig(
            role="dev",
            phase_prompt="x",
            capabilities=CapabilityDeclaration(
                tools=CapabilityTools(allowed=["Read"]),
            ),
        )
        ticket = Ticket(
            work_type=WorkType.REFACTOR,
            title="t",
            created_by="o",
            description="d",
        )
        ctx = AgentSpawnContext(
            role="dev",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.PHASE_PRIMARY,
            ticket=ticket,
            parent=None,
            worktree_path=tmp_path / "worktree",
            project=Project(
                id="p",
                name="p",
                path=str(tmp_path),
                language="python",
                package_manager="uv",
            ),
            tickets=None,  # type: ignore[arg-type]
            threads=None,  # type: ignore[arg-type]
            memory=None,  # type: ignore[arg-type]
            bus=None,  # type: ignore[arg-type]
        )
        # Force materialize to blow up.
        with patch.object(
            agent_module,
            "materialize_capabilities",
            side_effect=OSError("disk full"),
        ):
            # Must not raise.
            agent_module._materialize_capability_policy(ctx)


class TestMaterializeWithoutSandbox:
    """The sandbox-gate short-circuit from PR #4 review.

    When ``jig start --no-docker`` is in play, ``sandbox_available()``
    returns False and materialisation is skipped — the hook paths
    baked into ``.claude/settings.json`` are container-absolute
    (``/jig/bin/check-*``), so writing them on the host would hand
    Claude Code ENOENT on every guarded tool call."""

    def test_skipped_when_sandbox_unavailable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Even a role with declared capabilities gets a no-op when no
        sandbox is present."""
        from jig import agent as agent_module

        monkeypatch.delenv("JIG_IN_CONTAINER", raising=False)

        role_cfg = RoleConfig(
            role="dev",
            phase_prompt="x",
            capabilities=CapabilityDeclaration(
                tools=CapabilityTools(allowed=["Read"]),
                paths=CapabilityPaths(writable=["ticket://worktree/**"]),
            ),
        )
        ctx = AgentSpawnContext(
            role="dev",
            role_cfg=role_cfg,
            spawn_reason=SpawnReason.PHASE_PRIMARY,
            ticket=Ticket(
                work_type=WorkType.REFACTOR,
                title="t",
                created_by="o",
                description="d",
            ),
            parent=None,
            worktree_path=tmp_path / "worktree",
            project=Project(
                id="p",
                name="p",
                path=str(tmp_path),
                language="python",
                package_manager="uv",
            ),
            tickets=None,  # type: ignore[arg-type]
            threads=None,  # type: ignore[arg-type]
            memory=None,  # type: ignore[arg-type]
            bus=None,  # type: ignore[arg-type]
        )

        result = agent_module._materialize_capability_policy(ctx)

        # Returns None so the caller knows not to wire the policy
        # mount into BwrapConfig (there's no sandbox to mount it into
        # anyway).
        assert result is None
        # Nothing on disk — no settings.json to break Claude Code's
        # tool dispatch on the host.
        assert not (tmp_path / "worktree" / ".claude").exists()
        assert not (tmp_path / ".jig" / "runtime").exists()
