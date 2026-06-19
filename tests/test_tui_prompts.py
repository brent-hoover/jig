import asyncio
from pathlib import Path

import pytest

from jig.events import EventEmitter, JigEvent
from jig.init_workflow import (
    BranchChoice,
    BriefApprovalChoice,
    ConfirmChoice,
)
from jig.prompt_registry import PromptRegistry
from jig.tui.tui_prompts import TuiPromptHandler


def _scripted_replier(
    emitter: EventEmitter, registry: PromptRegistry, replies: list[str]
):
    """Helper: subscribe to emitter, deliver scripted replies in order
    when prompt_request events arrive. Returns a task you can await on
    or cancel."""
    queue = emitter.subscribe()
    reply_iter = iter(replies)

    async def loop():
        try:
            while True:
                event: JigEvent = await queue.get()
                if event.type != "prompt_request":
                    continue
                pid = event.data["prompt_id"]
                try:
                    reply = next(reply_iter)
                except StopIteration:
                    return
                # Deliver on the next tick so the awaiter has parked
                await asyncio.sleep(0)
                registry.deliver(pid, reply)
        finally:
            try:
                emitter.unsubscribe(queue)
            except ValueError:
                pass

    return asyncio.create_task(loop())


@pytest.mark.asyncio
async def test_brief_approval_resolves(tmp_path: Path):
    emitter = EventEmitter()
    registry = PromptRegistry()
    handler = TuiPromptHandler(emitter=emitter, registry=registry)

    replier = _scripted_replier(emitter, registry, ["Y"])
    try:
        from rich.console import Console

        choice = await asyncio.wait_for(
            handler.ask_brief_approval(project_path=tmp_path, console=Console()),
            timeout=2.0,
        )
        assert choice == BriefApprovalChoice.YES
    finally:
        replier.cancel()


@pytest.mark.asyncio
async def test_branch_choice_default_yes_maps_to_sa():
    emitter = EventEmitter()
    registry = PromptRegistry()
    handler = TuiPromptHandler(emitter=emitter, registry=registry)
    replier = _scripted_replier(emitter, registry, ["Y"])
    try:
        from rich.console import Console

        choice = await asyncio.wait_for(
            handler.ask_branch_choice(console=Console()),
            timeout=2.0,
        )
        assert choice == BranchChoice.SA
    finally:
        replier.cancel()


@pytest.mark.asyncio
async def test_sa_confirm_swap_round_trips():
    emitter = EventEmitter()
    registry = PromptRegistry()
    handler = TuiPromptHandler(emitter=emitter, registry=registry)
    replier = _scripted_replier(emitter, registry, ["swap"])
    try:
        from rich.console import Console

        choice = await asyncio.wait_for(
            handler.ask_sa_confirm(
                template_name="python-cli",
                rationale="reason",
                tech_decisions=[],
                size="S",
                console=Console(),
            ),
            timeout=2.0,
        )
        assert choice == ConfirmChoice.SWAP
    finally:
        replier.cancel()


@pytest.mark.asyncio
async def test_direct_template_loops_on_bad_input():
    from unittest.mock import patch

    emitter = EventEmitter()
    registry = PromptRegistry()
    handler = TuiPromptHandler(emitter=emitter, registry=registry)
    # First reply: not a number; second: out of range; third: valid (2)
    replier = _scripted_replier(emitter, registry, ["abc", "99", "2"])
    try:
        from rich.console import Console

        # Patch render_template_list so we don't need real templates on disk
        with patch("jig.tui.tui_prompts.render_template_list", return_value="(list)"):
            name = await asyncio.wait_for(
                handler.ask_direct_template(
                    template_names=["alpha", "beta", "gamma"],
                    console=Console(),
                ),
                timeout=2.0,
            )
        assert name == "beta"
    finally:
        replier.cancel()


@pytest.mark.asyncio
async def test_force_confirm_no_returns_false(tmp_path: Path):
    emitter = EventEmitter()
    registry = PromptRegistry()
    handler = TuiPromptHandler(emitter=emitter, registry=registry)
    replier = _scripted_replier(emitter, registry, ["N"])
    try:
        from rich.console import Console

        ok = await asyncio.wait_for(
            handler.ask_force_confirm(target=tmp_path, console=Console()),
            timeout=2.0,
        )
        assert ok is False
    finally:
        replier.cancel()


@pytest.mark.asyncio
async def test_question_answer_passes_through(tmp_path: Path):
    from jig.thread import Question

    emitter = EventEmitter()
    registry = PromptRegistry()
    handler = TuiPromptHandler(emitter=emitter, registry=registry)
    replier = _scripted_replier(emitter, registry, ["my answer"])
    try:
        from rich.console import Console

        q = Question(
            ticket_id="brief", author="po", question="What is X?", target="any_human"
        )
        ans = await asyncio.wait_for(
            handler.ask_question_answer(
                question=q, index=1, total=1, console=Console()
            ),
            timeout=2.0,
        )
        assert ans == "my answer"
    finally:
        replier.cancel()
