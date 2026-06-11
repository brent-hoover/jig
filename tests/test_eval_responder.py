"""Unit tests for the eval auto-responder (jig/eval/responder.py)."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from jig.eval.responder import (
    CANNED_ANSWER,
    CannedAnswerPolicy,
    auto_responder,
)


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))


def _prompt_frame(
    prompt_id: str,
    ticket_id: str = "planning",
    prompt_type: str = "question_answer",
    question: str = "Approve the plan?",
) -> dict:
    return {
        "type": "event",
        "topic": "prompts",
        "kind": "request",
        "data": {
            "prompt_id": prompt_id,
            "prompt_type": prompt_type,
            "ticket_id": ticket_id,
            "asker": "pm",
            "question_text": question,
            "question": question,
        },
    }


async def _run_responder(frames: list[dict], **kwargs) -> _FakeWS:
    """Feed frames through the responder, cancel it once the queue drains."""
    queue: asyncio.Queue[dict] = asyncio.Queue()
    for frame in frames:
        queue.put_nowait(frame)
    ws = _FakeWS()
    task = asyncio.create_task(
        auto_responder(queue, ws, policy=CannedAnswerPolicy(), **kwargs)
    )
    while not queue.empty():
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)  # let the responder finish the last frame
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    return ws


async def test_question_prompt_gets_one_reply() -> None:
    ws = await _run_responder([_prompt_frame("p1")])
    assert len(ws.sent) == 1
    cmd = ws.sent[0]
    assert cmd["type"] == "command"
    assert cmd["name"] == "prompt_reply"
    assert cmd["args"]["args"] == ["p1", CANNED_ANSWER]


async def test_duplicate_prompt_id_replied_once() -> None:
    ws = await _run_responder([_prompt_frame("p1"), _prompt_frame("p1")])
    assert len(ws.sent) == 1


async def test_cap_per_ticket(caplog: pytest.LogCaptureFixture) -> None:
    frames = [_prompt_frame(f"p{i}") for i in range(4)]
    # Re-deliver the capped prompt: must dedupe, not log a second ERROR.
    frames.append(_prompt_frame("p3"))
    with caplog.at_level(logging.ERROR, logger="jig.eval.responder"):
        ws = await _run_responder(frames)
    assert len(ws.sent) == 3
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "cap" in errors[0].getMessage()
    assert "planning" in errors[0].getMessage()


async def test_caps_are_per_ticket() -> None:
    frames = [_prompt_frame(f"a{i}", ticket_id="t-a") for i in range(3)]
    frames += [_prompt_frame(f"b{i}", ticket_id="t-b") for i in range(3)]
    ws = await _run_responder(frames)
    assert len(ws.sent) == 6


async def test_unhandled_prompt_type_ignored(
    caplog: pytest.LogCaptureFixture,
) -> None:
    frame = _prompt_frame("p1", prompt_type="confirm_gate")
    with caplog.at_level(logging.WARNING, logger="jig.eval.responder"):
        ws = await _run_responder([frame])
    assert ws.sent == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "confirm_gate" in warnings[0].getMessage()


async def test_failed_result_logged_no_send(
    caplog: pytest.LogCaptureFixture,
) -> None:
    frame = {"type": "result", "ok": False, "error": "no pending prompt"}
    with caplog.at_level(logging.WARNING, logger="jig.eval.responder"):
        ws = await _run_responder([frame])
    assert ws.sent == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "no pending prompt" in warnings[0].getMessage()


async def test_unrelated_frames_ignored() -> None:
    frames = [
        {"type": "snapshot", "topic": "tickets", "data": []},
        {"type": "event", "topic": "tickets", "kind": "updated", "data": {}},
        {"type": "result", "ok": True, "data": {"prompt_id": "p9"}},
        {"type": "event", "topic": "prompts", "kind": "request", "data": {}},
    ]
    ws = await _run_responder(frames)
    assert ws.sent == []


async def test_send_failure_logged_and_task_ends(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A broken socket must log a WARNING and end the task, not crash or spin."""

    class _BrokenWS:
        async def send(self, raw: str) -> None:
            raise ConnectionError("socket closed")

    queue: asyncio.Queue[dict] = asyncio.Queue()
    queue.put_nowait(_prompt_frame("p1"))
    task = asyncio.create_task(
        auto_responder(queue, _BrokenWS(), policy=CannedAnswerPolicy())
    )
    with caplog.at_level(logging.WARNING, logger="jig.eval.responder"):
        await asyncio.wait_for(task, timeout=2)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "p1" in warnings[0].getMessage()
    assert "socket closed" in warnings[0].getMessage()
