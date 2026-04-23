"""Tests for ``jig.helper_spawn`` — helper-agent spawning for
``human_with_helper`` proposal routing (Phase 5 Task N).

Uses the same SDK-mocking pattern as
``tests/test_agent_check_runner.py``: replace ``query`` and the MCP-
server factory in the spawn module's namespace with a fake async
generator that drives the scoped tool handler directly, so the test
layer never boots a real agent.
"""

from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from jig import helper_spawn as helper_spawn_mod
from jig.helper_spawn import (
    build_helper_draft_tool,
    spawn_helper_for_proposal,
)
from jig.ownership import OwnerRouting
from jig.store.threads import ThreadStore
from jig.thread import Note, Proposal


# ---- SDK fake --------------------------------------------------------------


class _FakeSDK:
    """Install-once patch target for ``query`` + the MCP-server factory.

    The spawn module imports both symbols at module level, so the
    patch targets live on ``helper_spawn_mod``. ``create_helper_mcp_server``
    is wrapped so the test can grab the live ``captured`` dict; ``query``
    is replaced by an async generator that drives the
    ``submit_helper_draft`` tool handler directly against that dict.
    """

    def __init__(
        self,
        *,
        drafts: list[str] | None = None,
        raise_exc: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.drafts = drafts if drafts is not None else []
        self.raise_exc = raise_exc
        self.hang = hang
        self._captured: dict | None = None
        self._orig = helper_spawn_mod.create_helper_mcp_server

    def create_helper_mcp_server(self, captured):
        self._captured = captured
        return self._orig(captured)

    async def query(self, prompt, options, **kwargs):
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.hang:
            await asyncio.sleep(10)
            yield None  # pragma: no cover — unreachable after sleep
            return

        assert self._captured is not None, "create_helper_mcp_server not called"
        handler = build_helper_draft_tool(self._captured).handler
        for text in self.drafts:
            await handler({"text": text})

        class _Msg:
            pass

        yield _Msg()

    def install(self, stack):
        stack.enter_context(
            patch.object(
                helper_spawn_mod,
                "create_helper_mcp_server",
                self.create_helper_mcp_server,
            )
        )
        stack.enter_context(patch.object(helper_spawn_mod, "query", self.query))


@contextmanager
def _fake_sdk(**kwargs):
    fake = _FakeSDK(**kwargs)
    with ExitStack() as stack:
        fake.install(stack)
        yield fake


# ---- fixtures --------------------------------------------------------------


@pytest.fixture
async def threads(tmp_path: Path) -> ThreadStore:
    store = ThreadStore(tmp_path / "threads.jsonl")
    await store.load()
    return store


def _routing(
    *, assignment: str = "human_with_helper", helper: str | None = "pm"
) -> OwnerRouting:
    """Routing pointing at the shipped ``pm`` role by default — it's
    loadable in tests without any project config."""
    return OwnerRouting(
        role="po",
        assignee="alice",
        helper_template=helper,
        assignment=assignment,  # type: ignore[arg-type]
    )


async def _post_proposal(
    threads: ThreadStore, *, ticket_id: str = "t-1", author: str = "human"
) -> Proposal:
    pid = await threads.post(
        Proposal(
            ticket_id=ticket_id,
            author=author,
            target="ticket://spec.description",
            section=None,
            change="new description",
            rationale="why",
            state="pending",
            owners=["po"],
        )
    )
    stored = await threads.get(pid)
    assert isinstance(stored, Proposal)
    return stored


# ---- submit_helper_draft tool (unit) ---------------------------------------


class TestHelperDraftTool:
    async def test_first_call_captures(self) -> None:
        slot = helper_spawn_mod._fresh_slot()
        handler = build_helper_draft_tool(slot).handler
        await handler({"text": "draft response"})
        assert slot["text"] == "draft response"
        assert slot["call_count"] == 1

    async def test_second_call_raises(self) -> None:
        slot = helper_spawn_mod._fresh_slot()
        handler = build_helper_draft_tool(slot).handler
        await handler({"text": "first"})
        with pytest.raises(RuntimeError, match="already called"):
            await handler({"text": "second"})


# ---- skip conditions -------------------------------------------------------


class TestSkipConditions:
    @pytest.mark.asyncio
    async def test_skip_when_assignment_not_human_with_helper(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        routing = _routing(assignment="human", helper=None)
        proposal = await _post_proposal(threads)
        note_id = await spawn_helper_for_proposal(
            project_path=tmp_path,
            threads=threads,
            proposal=proposal,
            routing=routing,
        )
        assert note_id is None
        # Nothing posted beyond the original proposal.
        thread = await threads.for_ticket("t-1")
        assert [e.kind for e in thread] == ["proposal"]

    @pytest.mark.asyncio
    async def test_skip_when_helper_template_missing(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        routing = _routing(helper=None)
        proposal = await _post_proposal(threads)
        note_id = await spawn_helper_for_proposal(
            project_path=tmp_path,
            threads=threads,
            proposal=proposal,
            routing=routing,
        )
        assert note_id is None

    @pytest.mark.asyncio
    async def test_skip_when_helper_role_not_found(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        routing = _routing(helper="ghost-role-does-not-exist")
        proposal = await _post_proposal(threads)
        note_id = await spawn_helper_for_proposal(
            project_path=tmp_path,
            threads=threads,
            proposal=proposal,
            routing=routing,
        )
        assert note_id is None


# ---- happy path ------------------------------------------------------------


class TestSpawnHappyPath:
    @pytest.mark.asyncio
    async def test_draft_posted_as_note_linked_to_proposal(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        """The helper's draft lands as a Note on the same ticket's
        thread with ``responds_to`` pointing at the originating
        proposal's id."""
        routing = _routing(helper="pm")
        proposal = await _post_proposal(threads)

        with _fake_sdk(drafts=["suggested: elaborate on X"]):
            note_id = await spawn_helper_for_proposal(
                project_path=tmp_path,
                threads=threads,
                proposal=proposal,
                routing=routing,
            )

        assert note_id is not None
        note = await threads.get(note_id)
        assert isinstance(note, Note)
        assert note.text == "suggested: elaborate on X"
        assert note.responds_to == proposal.id
        assert note.ticket_id == proposal.ticket_id
        # Author is the helper role name so readers can tell it's a
        # helper draft at a glance, even before Task C's prompt
        # composition lands.
        assert note.author == "pm"


# ---- failure paths ---------------------------------------------------------


class TestSpawnFailure:
    @pytest.mark.asyncio
    async def test_timeout_skips_note(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        routing = _routing(helper="pm")
        proposal = await _post_proposal(threads)

        with _fake_sdk(hang=True):
            note_id = await spawn_helper_for_proposal(
                project_path=tmp_path,
                threads=threads,
                proposal=proposal,
                routing=routing,
                timeout_s=0.1,
            )

        assert note_id is None
        thread = await threads.for_ticket("t-1")
        assert [e.kind for e in thread] == ["proposal"]

    @pytest.mark.asyncio
    async def test_exception_skips_note(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        routing = _routing(helper="pm")
        proposal = await _post_proposal(threads)

        with _fake_sdk(raise_exc=RuntimeError("boom")):
            note_id = await spawn_helper_for_proposal(
                project_path=tmp_path,
                threads=threads,
                proposal=proposal,
                routing=routing,
            )

        assert note_id is None

    @pytest.mark.asyncio
    async def test_agent_exits_without_drafting_skips_note(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        """Agent returns without ever calling ``submit_helper_draft``
        — no Note is posted. The proposal flow is unaffected; the human
        just doesn't get a draft."""
        routing = _routing(helper="pm")
        proposal = await _post_proposal(threads)

        with _fake_sdk(drafts=[]):
            note_id = await spawn_helper_for_proposal(
                project_path=tmp_path,
                threads=threads,
                proposal=proposal,
                routing=routing,
            )

        assert note_id is None
        thread = await threads.for_ticket("t-1")
        assert [e.kind for e in thread] == ["proposal"]

    @pytest.mark.asyncio
    async def test_empty_draft_skips_note(
        self, tmp_path: Path, threads: ThreadStore
    ) -> None:
        """A whitespace-only draft is indistinguishable from no draft."""
        routing = _routing(helper="pm")
        proposal = await _post_proposal(threads)

        with _fake_sdk(drafts=["   "]):
            note_id = await spawn_helper_for_proposal(
                project_path=tmp_path,
                threads=threads,
                proposal=proposal,
                routing=routing,
            )

        assert note_id is None
