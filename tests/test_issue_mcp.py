"""Standalone stdio MCP front door.

Exposes issue CRUD/link over IssueService for external (non-jig) agents. It
must NOT expose approve — promotion to OPEN is operator-only — and its update
tool must not be able to bypass the gate either (the store rejects it).
"""

from pathlib import Path

import pytest

from jig.issues import mcp as issue_mcp
from jig.issues.service import IssueService
from tests._test_ticket import TICKET_AC_PLACEHOLDER


def _service(tmp_path: Path) -> IssueService:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    return IssueService(tmp_path)


@pytest.mark.asyncio
async def test_create_tool_returns_proposed_dict(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    result = await issue_mcp.create_issue(
        svc,
        title="x",
        work_type="feature",
        description=TICKET_AC_PLACEHOLDER,
        created_by="agent-x",
    )
    assert result["key"] == "jig-1"
    assert result["status"] == "proposed"
    assert result["created_by"] == "agent-x"


@pytest.mark.asyncio
async def test_create_tool_missing_ac_raises(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    with pytest.raises(ValueError, match="(?i)acceptance"):
        await issue_mcp.create_issue(
            svc, title="x", work_type="feature", description="no ac"
        )


@pytest.mark.asyncio
async def test_update_tool_cannot_open_proposed(tmp_path: Path) -> None:
    svc = _service(tmp_path)
    created = await issue_mcp.create_issue(
        svc, title="x", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )
    with pytest.raises(ValueError, match="approv"):
        await issue_mcp.update_issue(svc, ref=created["key"], status="open")


@pytest.mark.asyncio
async def test_tool_set_excludes_approve(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    server = issue_mcp.build_server(tmp_path)
    names = {t.name for t in await server.list_tools()}
    assert names == {
        "issue_create",
        "issue_list",
        "issue_show",
        "issue_update",
        "issue_close",
        "issue_comment",
        "issue_link",
    }
    assert "issue_approve" not in names


@pytest.mark.asyncio
async def test_tools_reflect_external_writes(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    server = issue_mcp.build_server(tmp_path)

    await server.call_tool(
        "issue_create",
        {"title": "a", "work_type": "feature", "description": TICKET_AC_PLACEHOLDER},
    )
    # A separate writer appends another issue AFTER the first tool call. A
    # server that cached one IssueService would miss this; a fresh service per
    # call reloads from disk and sees it.
    external = IssueService(tmp_path)
    await external.create(
        title="b", work_type="feature", description=TICKET_AC_PLACEHOLDER
    )

    result = await server.call_tool("issue_list", {})
    blob = str(result)
    assert "jig-1" in blob and "jig-2" in blob
