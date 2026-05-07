from pathlib import Path

import click
import pytest

from jig.init_workflow import (
    BriefApprovalChoice, prompt_brief_approval, render_brief_for_approval,
)


def test_render_brief_for_approval_includes_section_headers(tmp_path: Path):
    """Rich-rendered output strips the literal `#` markdown markers but
    preserves heading text (with ANSI styling). Check the visible text
    content, not the markdown source."""
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "brief.md").write_text(
        "# todoapp\n\nintro\n\n## Built\n\n(empty)\n\n## Non-goals\n\n- {#ng} no\n"
    )
    out = render_brief_for_approval(tmp_path)
    assert "todoapp" in out
    assert "Built" in out
    assert "Non-goals" in out
    assert "Brief preview" in out  # the visual separator label survives


def test_render_brief_for_approval_handles_missing_file(tmp_path: Path):
    out = render_brief_for_approval(tmp_path)
    assert "missing" in out


@pytest.mark.asyncio
async def test_prompt_brief_approval_yes(monkeypatch, tmp_path):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "brief.md").write_text("# x\n")
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "Y")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    decision = await prompt_brief_approval(tmp_path)
    assert decision == BriefApprovalChoice.YES


@pytest.mark.asyncio
async def test_prompt_brief_approval_resume(monkeypatch, tmp_path):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "brief.md").write_text("# x\n")
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "r")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    decision = await prompt_brief_approval(tmp_path)
    assert decision == BriefApprovalChoice.RESUME


@pytest.mark.asyncio
async def test_prompt_brief_approval_no(monkeypatch, tmp_path):
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs" / "brief.md").write_text("# x\n")
    monkeypatch.setattr(click, "prompt", lambda *a, **kw: "n")
    monkeypatch.setattr(click, "echo", lambda *a, **kw: None)
    decision = await prompt_brief_approval(tmp_path)
    assert decision == BriefApprovalChoice.NO
