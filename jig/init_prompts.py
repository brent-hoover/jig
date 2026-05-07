"""PromptHandler protocol — extracts interactive prompt sites from init_workflow.

The init flow asks the operator a small fixed set of questions:
brief approval, branch choice, SA confirm, gap decision, direct
template pick, free-form answers to agent questions, and (rarely)
force-reset confirmation. Each is funneled through PromptHandler so
the daemon can swap in a TUI-aware implementation that round-trips
prompts over WebSocket.

The default CliPromptHandler wraps click.prompt + rich.Panel exactly
the way the workflow used to do inline.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import click

if TYPE_CHECKING:
    from rich.console import Console

    from jig.spec_generator import Gap
    from jig.thread import Question


@runtime_checkable
class PromptHandler(Protocol):
    async def ask_brief_approval(
        self, *, project_path: Path, console: "Console"
    ) -> "BriefApprovalChoice": ...

    async def ask_branch_choice(
        self, *, console: "Console"
    ) -> "BranchChoice": ...

    async def ask_sa_confirm(
        self, *, template_name: str, rationale: str, console: "Console"
    ) -> "ConfirmChoice": ...

    async def ask_gap_decision(
        self, *, gaps: "list[Gap]", console: "Console"
    ) -> str: ...  # "R" or "Q"

    async def ask_direct_template(
        self, *, template_names: list[str], console: "Console"
    ) -> str: ...  # the chosen name

    async def ask_question_answer(
        self, *, question: "Question", index: int, total: int, console: "Console"
    ) -> str: ...  # the answer text

    async def ask_force_confirm(
        self, *, target: Path, console: "Console"
    ) -> bool: ...  # True = proceed with reset

    async def ask_init_complete(
        self, *, console: "Console"
    ) -> None: ...


# Import enums here so init_prompts.py can be imported without circular issues.
# They live in init_workflow; we re-export them for convenience but the
# canonical definition stays in init_workflow.
from jig.init_workflow import BranchChoice, BriefApprovalChoice, ConfirmChoice  # noqa: E402


class CliPromptHandler:
    """Default PromptHandler: wraps click.prompt + rich Panel rendering.

    All current CLI behavior lives here. The init_workflow callers go
    through this handler in the default (CLI) path; in TUI mode the
    daemon swaps in a TuiPromptHandler.
    """

    async def ask_brief_approval(
        self, *, project_path: Path, console: "Console"
    ) -> BriefApprovalChoice:
        from rich.text import Text

        from jig.init_workflow import render_brief_for_approval

        # render_brief_for_approval returns a string with embedded ANSI codes
        # (it pre-renders the brief markdown to a StringIO with force_terminal).
        # Rich's console.print(..., markup=False) writes the ESC bytes
        # literally instead of re-interpreting them, so we'd see raw codes
        # like "[4;35m_intro[0m". Text.from_ansi parses the codes back into
        # styled Rich Text so the operator's terminal renders the styling.
        console.print(Text.from_ansi(render_brief_for_approval(project_path)))
        console.print(
            "Approve brief?\n"
            "  [Y] Hand off to spec-generator   (default)\n"
            "  [r] Resume PO — more changes needed\n"
            "  [n] Cancel — exit, state saved\n",
            markup=False,
        )
        reply = click.prompt("Choice", default="Y", show_default=False)
        return BriefApprovalChoice.parse(reply)

    async def ask_branch_choice(self, *, console: "Console") -> BranchChoice:
        from jig.init_workflow import render_branch_prompt

        console.print(render_branch_prompt(), markup=False)
        reply = click.prompt("Choice", default="Y", show_default=False)
        return BranchChoice.parse(reply)

    async def ask_sa_confirm(
        self, *, template_name: str, rationale: str, console: "Console"
    ) -> ConfirmChoice:
        from jig.init_workflow import render_sa_confirm_prompt

        console.print(
            render_sa_confirm_prompt(template_name=template_name, rationale=rationale),
            markup=False,
        )
        reply = click.prompt("Choice", default="Y", show_default=False)
        return ConfirmChoice.parse(reply)

    async def ask_gap_decision(
        self, *, gaps: "list[Gap]", console: "Console"
    ) -> str:
        from jig.init_workflow import render_gap_prompt

        console.print(render_gap_prompt(gaps), markup=False)
        reply = click.prompt(
            "Choice", default="R", show_default=False
        ).strip().upper()
        return reply if reply in ("R", "Q") else "R"

    async def ask_direct_template(
        self, *, template_names: list[str], console: "Console"
    ) -> str:
        from jig.init_workflow import render_template_list

        while True:
            console.print(render_template_list(template_names), markup=False)
            reply = click.prompt(
                f"Pick (1-{len(template_names)})",
                default="1",
                show_default=False,
            ).strip()
            try:
                idx = int(reply)
            except ValueError:
                console.print("Please enter a number.", markup=False)
                continue
            if 1 <= idx <= len(template_names):
                return template_names[idx - 1]
            console.print("Out of range.", markup=False)

    async def ask_question_answer(
        self, *, question: "Question", index: int, total: int, console: "Console"
    ) -> str:
        from rich.panel import Panel
        from rich.text import Text

        title_suffix = f" ({index}/{total})" if total > 1 else ""
        console.print()
        console.print(
            Panel(
                Text(question.question, style="bold"),
                title=f"[cyan]{question.author} asks{title_suffix}[/cyan]",
                title_align="left",
                border_style="cyan",
                padding=(0, 2),
            )
        )
        return click.prompt(
            click.style("›", fg="cyan"),
            default="",
            show_default=False,
        )

    async def ask_force_confirm(
        self, *, target: Path, console: "Console"
    ) -> bool:
        reply = click.prompt(
            f"This will wipe {target}/.jig. Type 'force' to continue",
            default="",
            show_default=False,
        )
        return reply == "force"

    async def ask_init_complete(self, *, console: "Console") -> None:
        click.prompt(
            "Project ready. Press Enter to continue",
            default="",
            show_default=False,
            prompt_suffix="",
        )


class AutoPromptHandler:
    """Non-interactive PromptHandler — picks each default for unattended runs.

    Used by ``jig init --auto`` for eval harnesses where there's no operator
    to answer. Choices match the ``[Y]`` defaults of the CLI handler:
    approve the brief, hand off to SA, accept the SA scaffold. Free-text
    questions raise — an unattended run that needs an answer is a
    misconfiguration, not something to silently default.
    """

    async def ask_brief_approval(
        self, *, project_path: Path, console: "Console"
    ) -> BriefApprovalChoice:
        return BriefApprovalChoice.YES

    async def ask_branch_choice(self, *, console: "Console") -> BranchChoice:
        return BranchChoice.SA

    async def ask_sa_confirm(
        self, *, template_name: str, rationale: str, console: "Console"
    ) -> ConfirmChoice:
        return ConfirmChoice.YES

    async def ask_gap_decision(
        self, *, gaps: "list[Gap]", console: "Console"
    ) -> str:
        # Quit — re-running PO unattended would loop forever. Fail loud
        # by exiting; the eval harness can inspect the gaps in state.
        return "Q"

    async def ask_direct_template(
        self, *, template_names: list[str], console: "Console"
    ) -> str:
        return template_names[0]

    async def ask_question_answer(
        self, *, question: "Question", index: int, total: int, console: "Console"
    ) -> str:
        raise RuntimeError(
            f"unattended init cannot answer agent question: {question.question!r} "
            "(brief is incomplete or spec-gen needs follow-up)"
        )

    async def ask_force_confirm(
        self, *, target: Path, console: "Console"
    ) -> bool:
        return True

    async def ask_init_complete(self, *, console: "Console") -> None:
        return
