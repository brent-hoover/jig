"""TuiPromptHandler — PromptHandler impl that round-trips via WebSocket.

Each ask_* method:
  1. Allocates a prompt_id via PromptRegistry.register()
  2. Emits a JigEvent(type="prompt_request", data={prompt_id, prompt_type, ...})
  3. Awaits the registered Future (resolved by cmd_prompt_reply when
     the operator replies)
  4. Parses the reply into the right return type and returns it

The init_workflow callers pass this instance via run_init(prompts=...).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jig.events import JigEvent
from jig.init_workflow import (
    BranchChoice,
    BriefApprovalChoice,
    ConfirmChoice,
    Gap,
    render_brief_for_approval,
    render_branch_prompt,
    render_gap_prompt,
    render_sa_confirm_prompt,
    render_template_list,
)

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from rich.console import Console

    from jig.events import EventEmitter
    from jig.prompt_registry import PromptRegistry
    from jig.thread import Question


class TuiPromptHandler:
    """PromptHandler that sends prompt_request events and awaits replies.

    Each prompt request includes:
      - ``prompt_id``: correlation UUID
      - ``prompt_type``: discriminator (brief_approval, branch_choice, ...)
      - ``rendered``: pre-rendered text the TUI should show inline (so
        the TUI doesn't have to re-implement render_*)
      - ``options``: machine-readable choices (when applicable) so the
        TUI can validate operator input client-side if it wants

    Reply parsing mirrors CliPromptHandler exactly — same enums, same
    fallbacks — so init_workflow can't tell which handler it's using.
    """

    def __init__(self, *, emitter: "EventEmitter", registry: "PromptRegistry") -> None:
        self._emitter = emitter
        self._registry = registry

    async def _round_trip(self, payload: dict[str, Any]) -> str:
        prompt_id, future = self._registry.register()
        prompt_type = payload.get("prompt_type", "?")
        _logger.debug(
            "prompt round-trip start id=%s type=%s",
            prompt_id,
            prompt_type,
        )
        await self._emitter.emit(
            JigEvent(
                type="prompt_request",
                data={"prompt_id": prompt_id, **payload},
            )
        )
        reply = await future
        _logger.debug(
            "prompt round-trip done id=%s type=%s",
            prompt_id,
            prompt_type,
        )
        return reply

    async def ask_brief_approval(
        self, *, project_path: Path, console: "Console"
    ) -> BriefApprovalChoice:
        rendered = render_brief_for_approval(project_path)
        reply = await self._round_trip(
            {
                "prompt_type": "brief_approval",
                "rendered": rendered,
                "question": "Approve brief?",
                "options": [
                    {
                        "key": "Y",
                        "label": "Hand off to spec-generator",
                        "default": True,
                    },
                    {"key": "r", "label": "Resume PO — more changes needed"},
                    {"key": "n", "label": "Cancel — exit, state saved"},
                ],
            }
        )
        return BriefApprovalChoice.parse(reply)

    async def ask_branch_choice(self, *, console: "Console") -> BranchChoice:
        reply = await self._round_trip(
            {
                "prompt_type": "branch_choice",
                "rendered": render_branch_prompt(),
                "question": "Choose your path:",
                "options": [
                    {"key": "Y", "label": "Hand off to SA", "default": True},
                    {"key": "p", "label": "Pick a template yourself"},
                    {"key": "s", "label": "Stay on PO"},
                ],
            }
        )
        return BranchChoice.parse(reply)

    async def ask_sa_confirm(
        self, *, template_name: str, rationale: str, console: "Console"
    ) -> ConfirmChoice:
        rendered = render_sa_confirm_prompt(
            template_name=template_name, rationale=rationale
        )
        reply = await self._round_trip(
            {
                "prompt_type": "sa_confirm",
                "rendered": rendered,
                "question": "Confirm template?",
                "template_name": template_name,
                "options": [
                    {"key": "Y", "label": "Accept", "default": True},
                    {"key": "n", "label": "Cancel"},
                    {"key": "swap", "label": "Re-consult SA"},
                ],
            }
        )
        return ConfirmChoice.parse(reply)

    async def ask_profile_confirm(
        self, *, name: str, rationale: str, console: "Console"
    ) -> ConfirmChoice:
        from jig.init_workflow import render_profile_confirm_prompt

        rendered = render_profile_confirm_prompt(name=name, rationale=rationale)
        reply = await self._round_trip(
            {
                "prompt_type": "profile_confirm",
                "rendered": rendered,
                "question": "Confirm profile?",
                "profile_name": name,
                "options": [
                    {"key": "Y", "label": "Accept", "default": True},
                    {"key": "swap", "label": "Use the other profile"},
                    {"key": "n", "label": "Cancel"},
                ],
            }
        )
        return ConfirmChoice.parse(reply)

    async def ask_gap_decision(self, *, gaps: list[Gap], console: "Console") -> str:
        reply = await self._round_trip(
            {
                "prompt_type": "gap_decision",
                "rendered": render_gap_prompt(gaps),
                "question": "Resume PO or Quit?",
                "gaps": [g.model_dump() for g in gaps],
                "options": [
                    {"key": "R", "label": "Resume PO conversation", "default": True},
                    {"key": "Q", "label": "Quit (state saved)"},
                ],
            }
        )
        choice = reply.strip().upper()
        return choice if choice in ("R", "Q") else "R"

    async def ask_direct_template(
        self, *, template_names: list[str], console: "Console"
    ) -> str:
        # Loop until the operator picks a valid index. Each round trip
        # is one prompt; on bad input we re-prompt with a hint.
        while True:
            reply = await self._round_trip(
                {
                    "prompt_type": "direct_template",
                    "rendered": render_template_list(template_names),
                    "question": f"Pick (1-{len(template_names)})",
                    "templates": template_names,
                }
            )
            try:
                idx = int(reply.strip())
            except ValueError:
                # Re-prompt with a hint embedded
                continue
            if 1 <= idx <= len(template_names):
                return template_names[idx - 1]
            # Out of range — re-prompt

    async def ask_question_answer(
        self,
        *,
        question: "Question",
        index: int,
        total: int,
        console: "Console",
    ) -> str:
        # The TUI renders this as a Panel client-side using the data;
        # the operator's input field captures the answer.
        return await self._round_trip(
            {
                "prompt_type": "question_answer",
                "question_id": question.id,
                "question_text": question.question,
                "asker": question.author,
                "index": index,
                "total": total,
            }
        )

    async def ask_force_confirm(self, *, target: Path, console: "Console") -> bool:
        reply = await self._round_trip(
            {
                "prompt_type": "force_confirm",
                "question": (
                    f"Reset {target}/.jig? This will wipe all jig state "
                    "for this project."
                ),
                "target": str(target),
                "options": [
                    {"key": "Y", "label": "Yes, reset"},
                    {"key": "N", "label": "No, abort", "default": True},
                ],
            }
        )
        return reply.strip().lower() == "y"

    async def ask_init_complete(self, *, console: "Console") -> None:
        await self._round_trip(
            {
                "prompt_type": "init_complete",
                "question": "Project is ready. The orchestrator will begin dispatching tickets.",
                "options": [
                    {"key": "Y", "label": "Continue", "default": True},
                ],
            }
        )
