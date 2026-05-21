"""Tests for AgentCheckRunner + check_verdict MCP tool (Phase 5 Task B).

Covers the agent-check spawn loop with the SDK ``query`` mocked out:
verdict capture, duplicate-call errors, missing-verdict errors,
timeout handling, context resolution, excluded-path filtering, and
the scripted/agent type dispatch.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from jig import check_runner as check_runner_mod
from jig.check_mcp import (
    _fresh_slot,
    build_check_verdict_tool,
)
from jig.check_runner import AgentCheckRunner
from jig.checks import (
    BlackBoxAgentCheck,
    CheckCatalog,
    CheckSeverity,
    ImplementationAwareAgentCheck,
    ScriptedCheck,
)
from jig.store import MessageBus
from jig.store.check_results import CheckResultsStore
from jig.store.threads import ThreadStore
from jig.ticket import Ticket, WorkType
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _catalog(**checks) -> CheckCatalog:
    return CheckCatalog.model_validate(checks)


async def _make_store(tmp_path: Path) -> CheckResultsStore:
    store = CheckResultsStore(tmp_path / "results.jsonl")
    await store.load()
    return store


async def _make_threads(tmp_path: Path) -> ThreadStore:
    store = ThreadStore(tmp_path / "threads.jsonl")
    await store.load()
    return store


def _worktree(tmp_path: Path) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    return root


def _ticket(*, title: str = "do it") -> Ticket:
    return Ticket(
        work_type=WorkType.REFACTOR,
        title=title,
        created_by="o",
        description=TICKET_AC_PLACEHOLDER,
    )


# ---- Fake SDK query --------------------------------------------------------


class _FakeSDK:
    """Patch target that replaces both ``create_check_mcp_server`` and
    ``query`` in the runner's namespace.

    - ``create_check_mcp_server`` is spied so we can grab the live
      ``captured`` dict the runner is using.
    - ``query`` is replaced by an async generator that drives the
      ``check_verdict`` tool handler directly against that dict.
    """

    def __init__(
        self,
        *,
        calls: list[tuple[str, str]] | None = None,
        raise_exc: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.calls = calls or []
        self.raise_exc = raise_exc
        self.hang = hang
        self._captured: dict | None = None
        self._orig = check_runner_mod.create_check_mcp_server

    def create_check_mcp_server(self, captured):
        self._captured = captured
        return self._orig(captured)

    async def query(self, prompt, options, **kwargs):
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.hang:
            await asyncio.sleep(10)
            yield None  # pragma: no cover — unreachable after sleep
            return

        assert self._captured is not None, "create_check_mcp_server not called"
        handler = build_check_verdict_tool(self._captured).handler
        for verdict, reasoning in self.calls:
            await handler({"verdict": verdict, "reasoning": reasoning})

        class _Msg:
            pass

        yield _Msg()

    def install(self, stack):
        stack.enter_context(
            patch.object(
                check_runner_mod,
                "create_check_mcp_server",
                self.create_check_mcp_server,
            )
        )
        stack.enter_context(patch.object(check_runner_mod, "query", self.query))


@contextmanager
def _fake_sdk(**kwargs):
    """Install a ``_FakeSDK`` and tear it down on exit."""
    fake = _FakeSDK(**kwargs)
    with ExitStack() as stack:
        fake.install(stack)
        yield fake


# ---- check_verdict MCP tool (unit) -----------------------------------------


class TestCheckVerdictTool:
    async def test_first_call_captures(self) -> None:
        slot = _fresh_slot()
        handler = build_check_verdict_tool(slot).handler
        await handler({"verdict": "pass", "reasoning": "looks good"})
        assert slot["verdict"] == "pass"
        assert slot["reasoning"] == "looks good"
        assert slot["call_count"] == 1

    async def test_second_call_raises(self) -> None:
        slot = _fresh_slot()
        handler = build_check_verdict_tool(slot).handler
        await handler({"verdict": "pass", "reasoning": "ok"})
        with pytest.raises(RuntimeError, match="already called"):
            await handler({"verdict": "fail", "reasoning": "changed my mind"})

    async def test_invalid_verdict_raises(self) -> None:
        slot = _fresh_slot()
        handler = build_check_verdict_tool(slot).handler
        with pytest.raises(ValueError, match="must be 'pass' or 'fail'"):
            await handler({"verdict": "maybe", "reasoning": "unsure"})
        assert slot["verdict"] is None


# ---- AgentCheckRunner -------------------------------------------------------


class TestAgentCheckRunner:
    async def test_pass_verdict(self, tmp_path: Path) -> None:
        cat = _catalog(
            qa=BlackBoxAgentCheck(
                type="black_box_agent",
                template="Does the behavior match the rubric?",
                context=["ticket://description"],
            )
        )
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(calls=[("pass", "acceptance criteria met")]):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="qa"
            )
        assert result.verdict == "pass"
        assert result.check_type == "black_box_agent"
        assert "acceptance criteria met" in result.output

    async def test_fail_verdict(self, tmp_path: Path) -> None:
        cat = _catalog(
            qa=BlackBoxAgentCheck(
                type="black_box_agent",
                template="rubric",
            )
        )
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(calls=[("fail", "missing error path")]):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="qa"
            )
        assert result.verdict == "fail"
        assert "missing error path" in result.output

    async def test_missing_verdict_is_error(self, tmp_path: Path) -> None:
        cat = _catalog(qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(calls=[]):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="qa"
            )
        assert result.verdict == "error"
        assert "check_verdict" in result.output

    async def test_timeout_kills_agent(self, tmp_path: Path) -> None:
        cat = _catalog(
            slow=BlackBoxAgentCheck(
                type="black_box_agent",
                template="rubric",
                timeout_s=1,
            )
        )
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(hang=True):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="slow"
            )
        assert result.verdict == "timeout"

    async def test_spawn_error_is_recorded(self, tmp_path: Path) -> None:
        cat = _catalog(qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(raise_exc=RuntimeError("SDK down")):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="qa"
            )
        assert result.verdict == "error"
        assert "SDK down" in result.output


class TestRunForPhase:
    async def test_skips_scripted_checks(self, tmp_path: Path) -> None:
        cat = _catalog(
            unit=ScriptedCheck(type="scripted", command="true"),
            qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"),
        )
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(calls=[("pass", "ok")]):
            out = await runner.run_for_phase(
                ticket=_ticket(),
                phase="review",
                check_names=["unit", "qa"],
            )
        assert [r.check_name for r in out] == ["qa"]

    async def test_unknown_check_raises(self, tmp_path: Path) -> None:
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=_catalog(),
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with pytest.raises(KeyError):
            await runner.run_for_phase(
                ticket=_ticket(),
                phase="review",
                check_names=["missing"],
            )


class TestDispatch:
    async def test_wrong_type_raises(self, tmp_path: Path) -> None:
        cat = _catalog(lint=ScriptedCheck(type="scripted", command="true"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with pytest.raises(TypeError):
            await runner.run_check(ticket=_ticket(), phase="review", check_name="lint")

    async def test_unknown_check_raises(self, tmp_path: Path) -> None:
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=_catalog(),
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with pytest.raises(KeyError):
            await runner.run_check(ticket=_ticket(), phase="review", check_name="nope")


class TestExcludedPaths:
    def test_repo_uri_match(self) -> None:
        assert check_runner_mod._matches_excluded(
            "repo://src/auth/login.py", ["src/**"]
        )

    def test_repo_uri_miss(self) -> None:
        assert not check_runner_mod._matches_excluded(
            "repo://tests/test_x.py", ["src/**"]
        )

    def test_non_repo_uri_never_matches(self) -> None:
        assert not check_runner_mod._matches_excluded("ticket://description", ["**"])

    def test_empty_excluded(self) -> None:
        assert not check_runner_mod._matches_excluded("repo://anything", [])


class TestImplementationAwareAllowsFilesystem:
    """Implementation-aware checks get Read/Grep/Glob; black-box does not.

    We can't observe the allowed_tools list through the real SDK in
    a unit test, but we can intercept ``query`` and read what the
    runner passed in ``options``.
    """

    async def test_implementation_aware_includes_read(self, tmp_path: Path) -> None:
        cat = _catalog(
            impl=ImplementationAwareAgentCheck(
                type="implementation_aware_agent",
                template="rubric",
            )
        )
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        captured_options: dict = {}

        with _fake_sdk(calls=[("pass", "ok")]) as fake:
            orig_query = fake.query

            async def capturing_query(prompt, options, **kwargs):
                captured_options["tools"] = list(options.allowed_tools or [])
                async for msg in orig_query(prompt, options, **kwargs):
                    yield msg

            with patch.object(check_runner_mod, "query", capturing_query):
                await runner.run_check(
                    ticket=_ticket(), phase="review", check_name="impl"
                )
        tools = captured_options["tools"]
        assert "Read" in tools
        assert "Grep" in tools
        assert "Glob" in tools

    async def test_black_box_excludes_filesystem(self, tmp_path: Path) -> None:
        cat = _catalog(qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        captured_options: dict = {}

        with _fake_sdk(calls=[("pass", "ok")]) as fake:
            orig_query = fake.query

            async def capturing_query(prompt, options, **kwargs):
                captured_options["tools"] = list(options.allowed_tools or [])
                async for msg in orig_query(prompt, options, **kwargs):
                    yield msg

            with patch.object(check_runner_mod, "query", capturing_query):
                await runner.run_check(
                    ticket=_ticket(), phase="review", check_name="qa"
                )
        tools = captured_options["tools"]
        assert "Read" not in tools
        assert "Grep" not in tools
        assert "Glob" not in tools


async def _make_bus(tmp_path: Path) -> MessageBus:
    bus = MessageBus(tmp_path / "bus.jsonl")
    await bus.load()
    return bus


class TestCheckCompletedBusEvents:
    """Phase 5 Task O3 — AgentCheckRunner publishes ``check_completed``
    after each persisted result, same shape as the scripted runner."""

    async def test_publishes_on_pass(self, tmp_path: Path) -> None:
        cat = _catalog(qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        bus = await _make_bus(tmp_path)
        ticket = _ticket()
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
            bus=bus,
        )
        with _fake_sdk(calls=[("pass", "looks good")]):
            result = await runner.run_check(
                ticket=ticket, phase="review", check_name="qa"
            )
        history = await bus.get_history(f"tickets.{ticket.id}")
        events = [m for m in history if m.payload.get("kind") == "check_completed"]
        assert len(events) == 1
        evt = events[0]
        assert evt.payload["check_name"] == "qa"
        assert evt.payload["verdict"] == "pass"
        assert evt.payload["event_id"] == result.id
        # Sender matches the runner's author — distinguishes agent
        # checks from scripted ones in the event stream.
        assert evt.sender == "check-agent"

    async def test_publishes_on_missing_verdict(self, tmp_path: Path) -> None:
        """No ``check_verdict`` call → verdict=error + bus event."""
        cat = _catalog(qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        bus = await _make_bus(tmp_path)
        ticket = _ticket()
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
            bus=bus,
        )
        with _fake_sdk(calls=[]):
            await runner.run_check(ticket=ticket, phase="review", check_name="qa")
        history = await bus.get_history(f"tickets.{ticket.id}")
        events = [m for m in history if m.payload.get("kind") == "check_completed"]
        assert len(events) == 1
        assert events[0].payload["verdict"] == "error"

    async def test_no_bus_no_event(self, tmp_path: Path) -> None:
        """Back-compat: existing tests don't wire a bus."""
        cat = _catalog(qa=BlackBoxAgentCheck(type="black_box_agent", template="rubric"))
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(calls=[("pass", "ok")]):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="qa"
            )
        assert result.verdict == "pass"


class TestSeverityInherited:
    async def test_severity_copied_to_result(self, tmp_path: Path) -> None:
        cat = _catalog(
            qa=BlackBoxAgentCheck(
                type="black_box_agent",
                template="rubric",
                severity=CheckSeverity.WARNING,
            )
        )
        results = await _make_store(tmp_path)
        threads = await _make_threads(tmp_path)
        runner = AgentCheckRunner(
            catalog=cat,
            results=results,
            worktree_path=_worktree(tmp_path),
            project_path=tmp_path,
            threads=threads,
        )
        with _fake_sdk(calls=[("pass", "ok")]):
            result = await runner.run_check(
                ticket=_ticket(), phase="review", check_name="qa"
            )
        assert result.severity == CheckSeverity.WARNING
