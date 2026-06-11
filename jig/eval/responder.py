"""Auto-responder for zero-touch eval runs.

In an attended run, the orchestrator's needs_info gate emits a
``prompt_request`` event and parks on a PromptRegistry future until the
operator answers in the TUI via the typed ``prompt_reply`` command. An
eval run has no operator, so every blocking agent question would stall
the run at that gate.

The responder consumes the runner's WS frame stream (fed by ``_dispatch``
fan-out) and replies to ``question_answer`` prompts with a deterministic
canned answer over the same socket. The orchestrator then posts the
Answer, resolves the question, and resumes the ticket through its
production path — identical to a human answering.

Answers are deterministic by design (no LLM): eval failures must be
attributable to the pipeline, not to answer variance. ``AnswerPolicy``
is the seam for scripted (answers.yaml) or LLM policies later.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Protocol

log = logging.getLogger(__name__)

CANNED_ANSWER = (
    "Approved — proceed. Use your best judgment on any open details; "
    "pick reasonable defaults and note them in the ticket."
)

DEFAULT_MAX_REPLIES_PER_TICKET = 3


class AnswerPolicy(Protocol):
    def answer(self, ticket_id: str, question: str) -> str: ...


class WSSender(Protocol):
    """The slice of a websocket connection the responder uses."""

    async def send(self, data: str) -> None: ...


class CannedAnswerPolicy:
    """Constant license-to-proceed answer, regardless of the question."""

    def answer(self, ticket_id: str, question: str) -> str:
        return CANNED_ANSWER


async def auto_responder(
    queue: asyncio.Queue[dict],
    ws: WSSender,
    *,
    policy: AnswerPolicy,
    max_replies_per_ticket: int = DEFAULT_MAX_REPLIES_PER_TICKET,
) -> None:
    """Reply to question_answer prompt_request frames until cancelled.

    Frames that are not question prompts or command results are ignored.
    A reply failure leaves the prompt's future parked server-side; the
    runner's stall backstop bounds the run, so there is no retry here.
    """
    answered: set[str] = set()
    replies_per_ticket: dict[str, int] = {}

    while True:
        msg = await queue.get()

        # Failure results carry no prompt_id/topic/kind — log uncorrelated.
        if msg.get("type") == "result" and msg.get("ok") is False:
            log.warning("auto-responder: command failed: %s", msg.get("error"))
            continue

        if msg.get("topic") != "prompts" or msg.get("kind") != "request":
            continue

        data = msg.get("data") or {}
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            continue

        if data.get("prompt_type") != "question_answer":
            log.warning(
                "auto-responder: ignoring unhandled prompt_type=%r (prompt_id=%s) — "
                "if the run stalls here, this gate needs a policy",
                data.get("prompt_type"),
                prompt_id,
            )
            continue

        if prompt_id in answered:
            continue

        ticket_id = data.get("ticket_id") or ""
        rounds = replies_per_ticket.get(ticket_id, 0)
        if rounds >= max_replies_per_ticket:
            log.error(
                "auto-responder: cap (%d) hit for ticket %s — leaving it blocked",
                max_replies_per_ticket,
                ticket_id,
            )
            continue

        question = data.get("question_text") or data.get("question") or ""
        answer = policy.answer(ticket_id, question)
        log.info(
            "auto-responder: answering ticket=%s prompt_id=%s\n  Q: %s\n  A: %s",
            ticket_id,
            prompt_id,
            question,
            answer,
        )
        try:
            await ws.send(
                json.dumps(
                    {
                        "type": "command",
                        "name": "prompt_reply",
                        "args": {"args": [prompt_id, answer]},
                    }
                )
            )
        except Exception as exc:
            # Socket closed or broken mid-run. No retry: the parked prompt
            # leaves the run to the stall backstop. Return (not continue) so
            # the task ends cleanly instead of spinning on a dead socket.
            log.warning(
                "auto-responder: send failed for prompt_id=%s: %s", prompt_id, exc
            )
            return
        answered.add(prompt_id)
        replies_per_ticket[ticket_id] = rounds + 1
