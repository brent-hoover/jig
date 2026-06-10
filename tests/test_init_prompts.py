"""Unit tests for jig.init_prompts.

CliPromptHandler is exercised through monkeypatched click.prompt;
the fancy rich rendering is verified by checking what was printed
to a captured Console.
"""

from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from rich.console import Console

from jig.init_prompts import CliPromptHandler
from jig.init_workflow import BranchChoice, BriefApprovalChoice, ConfirmChoice


def make_console() -> tuple[Console, StringIO]:
    buf = StringIO()
    c = Console(file=buf, force_terminal=False, width=120, color_system=None)
    return c, buf


@pytest.mark.asyncio
async def test_brief_approval_yes(tmp_path: Path):
    # Brief file doesn't have to exist; render_brief_for_approval handles missing
    handler = CliPromptHandler()
    console, buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="Y"):
        choice = await handler.ask_brief_approval(
            project_path=tmp_path, console=console
        )
    assert choice == BriefApprovalChoice.YES
    assert "Approve brief?" in buf.getvalue()


@pytest.mark.asyncio
async def test_brief_approval_resume(tmp_path: Path):
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="r"):
        choice = await handler.ask_brief_approval(
            project_path=tmp_path, console=console
        )
    assert choice == BriefApprovalChoice.RESUME


@pytest.mark.asyncio
async def test_brief_approval_no(tmp_path: Path):
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="n"):
        choice = await handler.ask_brief_approval(
            project_path=tmp_path, console=console
        )
    assert choice == BriefApprovalChoice.NO


@pytest.mark.asyncio
async def test_branch_choice_default_sa():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="Y"):
        choice = await handler.ask_branch_choice(console=console)
    assert choice == BranchChoice.SA


@pytest.mark.asyncio
async def test_branch_choice_direct():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="p"):
        choice = await handler.ask_branch_choice(console=console)
    assert choice == BranchChoice.DIRECT


@pytest.mark.asyncio
async def test_branch_choice_stay():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="s"):
        choice = await handler.ask_branch_choice(console=console)
    assert choice == BranchChoice.STAY


@pytest.mark.asyncio
async def test_sa_confirm_yes():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="Y"):
        choice = await handler.ask_sa_confirm(
            template_name="python-cli",
            rationale="reason",
            tech_decisions=[],
            size="S",
            console=console,
        )
    assert choice == ConfirmChoice.YES


@pytest.mark.asyncio
async def test_sa_confirm_no():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="n"):
        choice = await handler.ask_sa_confirm(
            template_name="python-cli",
            rationale="reason",
            tech_decisions=[],
            size="S",
            console=console,
        )
    assert choice == ConfirmChoice.NO


@pytest.mark.asyncio
async def test_sa_confirm_swap():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="swap"):
        choice = await handler.ask_sa_confirm(
            template_name="python-cli",
            rationale="reason",
            tech_decisions=[],
            size="S",
            console=console,
        )
    assert choice == ConfirmChoice.SWAP


@pytest.mark.asyncio
async def test_gap_decision_resume():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="R"):
        decision = await handler.ask_gap_decision(gaps=[], console=console)
    assert decision == "R"


@pytest.mark.asyncio
async def test_gap_decision_quit():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="Q"):
        decision = await handler.ask_gap_decision(gaps=[], console=console)
    assert decision == "Q"


@pytest.mark.asyncio
async def test_gap_decision_invalid_defaults_to_r():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="X"):
        decision = await handler.ask_gap_decision(gaps=[], console=console)
    assert decision == "R"


@pytest.mark.asyncio
async def test_direct_template_picks_first():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with (
        patch("jig.init_prompts.click.prompt", return_value="1"),
        patch("jig.init_workflow.load_template_metadata") as mock_meta,
    ):
        mock_meta.return_value = type("M", (), {"description": None})()
        name = await handler.ask_direct_template(
            template_names=["alpha", "beta", "gamma"],
            console=console,
        )
    assert name == "alpha"


@pytest.mark.asyncio
async def test_direct_template_picks_third():
    handler = CliPromptHandler()
    console, _buf = make_console()
    with (
        patch("jig.init_prompts.click.prompt", return_value="3"),
        patch("jig.init_workflow.load_template_metadata") as mock_meta,
    ):
        mock_meta.return_value = type("M", (), {"description": None})()
        name = await handler.ask_direct_template(
            template_names=["alpha", "beta", "gamma"],
            console=console,
        )
    assert name == "gamma"


@pytest.mark.asyncio
async def test_direct_template_loops_on_bad_input():
    handler = CliPromptHandler()
    console, buf = make_console()
    # First reply: not a number; second: out of range; third: valid
    replies = iter(["abc", "99", "2"])
    with (
        patch(
            "jig.init_prompts.click.prompt", side_effect=lambda *a, **k: next(replies)
        ),
        patch("jig.init_workflow.load_template_metadata") as mock_meta,
    ):
        mock_meta.return_value = type("M", (), {"description": None})()
        name = await handler.ask_direct_template(
            template_names=["alpha", "beta"],
            console=console,
        )
    assert name == "beta"
    output = buf.getvalue()
    assert "Please enter a number." in output
    assert "Out of range." in output


@pytest.mark.asyncio
async def test_question_answer_returns_reply():
    from jig.thread import Question

    handler = CliPromptHandler()
    console, buf = make_console()
    q = Question(
        ticket_id="brief",
        author="po",
        target="any_human",
        question="What is the main use case?",
        blocking=True,
    )
    with patch("jig.init_prompts.click.prompt", return_value="run batch jobs"):
        answer = await handler.ask_question_answer(
            question=q, index=1, total=1, console=console
        )
    assert answer == "run batch jobs"
    output = buf.getvalue()
    assert "What is the main use case?" in output
    assert "po asks" in output


@pytest.mark.asyncio
async def test_question_answer_shows_index_when_multiple():
    from jig.thread import Question

    handler = CliPromptHandler()
    console, buf = make_console()
    q = Question(
        ticket_id="brief",
        author="po",
        target="any_human",
        question="Secondary question?",
        blocking=True,
    )
    with patch("jig.init_prompts.click.prompt", return_value="yes"):
        await handler.ask_question_answer(question=q, index=2, total=3, console=console)
    output = buf.getvalue()
    assert "(2/3)" in output


@pytest.mark.asyncio
async def test_force_confirm_yes(tmp_path: Path):
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="force"):
        ok = await handler.ask_force_confirm(target=tmp_path, console=console)
    assert ok is True


@pytest.mark.asyncio
async def test_force_confirm_no(tmp_path: Path):
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value=""):
        ok = await handler.ask_force_confirm(target=tmp_path, console=console)
    assert ok is False


@pytest.mark.asyncio
async def test_force_confirm_wrong_word(tmp_path: Path):
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value="yes"):
        ok = await handler.ask_force_confirm(target=tmp_path, console=console)
    assert ok is False


# ---- ask_project_size (up-front size selection) ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, expected",
    [
        ("small", "small"),
        ("s", "small"),
        ("medium", "medium"),
        ("m", "medium"),
        ("MEDIUM", "medium"),
        ("", "small"),  # default
        ("garbage", "small"),  # conservative fallback
    ],
)
async def test_ask_project_size_parses(reply, expected):
    handler = CliPromptHandler()
    console, _buf = make_console()
    with patch("jig.init_prompts.click.prompt", return_value=reply):
        size = await handler.ask_project_size(console=console)
    assert size == expected


@pytest.mark.asyncio
async def test_auto_handler_ask_project_size_defaults_small():
    from jig.init_prompts import AutoPromptHandler

    console, _buf = make_console()
    assert await AutoPromptHandler().ask_project_size(console=console) == "small"


def test_render_size_prompt_teaches_distinction():
    from jig.init_workflow import render_size_prompt

    text = render_size_prompt()
    assert "small" in text and "medium" in text
    assert "module" in text  # the load-bearing distinction
    assert "Example" in text  # teaches with examples
