"""Auth-failure detection and the operator-facing messages it drives.

When the bundled ``claude`` CLI has no usable credentials it returns an
``is_error`` result whose text is ``"Not logged in · Please run /login"``
and then exits non-zero. The SDK rewrites that into the opaque
``Claude Code returned an error result: success``. These tests pin the
detection helper that recognises the auth failure and the ``jig onboard``
pre-flight that fails fast with an actionable message instead.
"""

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from click.testing import CliRunner

from jig.agent import _is_auth_failure


@pytest.mark.parametrize(
    "text",
    [
        "Not logged in · Please run /login",
        "Invalid API key · Please run /login",
        "OAuth token has expired · Please run /login",
        "NOT LOGGED IN",
        "Failed to authenticate. API Error: 401 Invalid bearer token",
    ],
)
def test_is_auth_failure_positive(text: str) -> None:
    assert _is_auth_failure(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "done",
        "completed the task",
        "error: file not found",
        "ran 3 turns",
        # A task error that merely mentions oauth must not be misread as an
        # auth failure (the marker is auth-specific, not any "oauth token").
        "error: oauth token field missing from config",
    ],
)
def test_is_auth_failure_negative(text: str | None) -> None:
    assert _is_auth_failure(text) is False


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)


def test_onboard_preflight_errors_when_token_unset(
    tmp_path: Path, monkeypatch
) -> None:
    from jig.cli import cli

    # The token check runs after target validation, so the path must be a
    # valid git repo root to reach it.
    _git_init(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    result = CliRunner().invoke(cli, ["onboard", str(tmp_path)])

    assert result.exit_code != 0
    assert "CLAUDE_CODE_OAUTH_TOKEN" in result.output
    assert "claude setup-token" in result.output


def test_onboard_preflight_passes_when_token_set(
    tmp_path: Path, monkeypatch
) -> None:
    """A set token clears the pre-flight; onboard proceeds (run_onboard
    stubbed) without emitting the token error."""
    from jig.cli import cli

    _git_init(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-ant-oat-test")

    called = {}

    async def fake_run_onboard(**kwargs):
        called["ok"] = True

    monkeypatch.setattr(
        "jig.onboard_workflow.run_onboard", fake_run_onboard, raising=True
    )

    result = CliRunner().invoke(cli, ["onboard", str(tmp_path)])

    assert called.get("ok") is True, result.output
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in result.output


def _auth_result_message(
    text: str, *, is_error: bool, api_error_status: int | None = None
):
    from claude_agent_sdk.types import ResultMessage

    msg = ResultMessage(
        subtype="success",
        duration_ms=10,
        duration_api_ms=8,
        is_error=is_error,
        num_turns=1,
        session_id="",
        total_cost_usd=0.0,
        usage={},
        result=text,
    )
    # api_error_status only exists on newer SDKs; set it as an attribute so
    # the test works regardless of the installed ResultMessage signature
    # (run_agent reads it via getattr).
    if api_error_status is not None:
        msg.api_error_status = api_error_status
    return msg


@pytest.mark.asyncio
async def test_run_agent_raises_agent_auth_error_on_login_result(
    tmp_path: Path, monkeypatch
) -> None:
    """An is_error result reading "Not logged in · Please run /login"
    surfaces as AgentAuthError, not the SDK's opaque exception."""
    from jig import agent as agent_module
    from tests.test_agent_streaming import _make_context

    ctx = await _make_context(tmp_path)

    async def fake_query(prompt, options, **kwargs):
        async for _ in prompt:
            break
        yield _auth_result_message("Not logged in · Please run /login", is_error=True)

    monkeypatch.setattr(agent_module, "query", fake_query)
    with pytest.raises(agent_module.AgentAuthError):
        await agent_module.run_agent(ctx)


@pytest.mark.asyncio
async def test_run_agent_raises_agent_auth_error_on_401_status(
    tmp_path: Path, monkeypatch
) -> None:
    """A rejected token surfaces as api_error_status=401 even when the
    result text doesn't match a known login phrase — detect it structurally."""
    from jig import agent as agent_module
    from tests.test_agent_streaming import _make_context

    ctx = await _make_context(tmp_path)

    async def fake_query(prompt, options, **kwargs):
        async for _ in prompt:
            break
        yield _auth_result_message(
            "the request was rejected", is_error=True, api_error_status=401
        )

    monkeypatch.setattr(agent_module, "query", fake_query)
    with pytest.raises(agent_module.AgentAuthError):
        await agent_module.run_agent(ctx)


@pytest.mark.asyncio
async def test_run_agent_does_not_raise_auth_error_on_ordinary_result(
    tmp_path: Path, monkeypatch
) -> None:
    """A non-auth result must not be misread as an auth failure, even
    when is_error is set."""
    from jig import agent as agent_module
    from tests.test_agent_streaming import _make_context

    ctx = await _make_context(tmp_path)

    async def fake_query(prompt, options, **kwargs):
        async for _ in prompt:
            break
        yield _auth_result_message("error: file not found", is_error=True)

    monkeypatch.setattr(agent_module, "query", fake_query)
    # Completes without raising AgentAuthError (the SDK would surface the
    # real failure downstream; the point here is we don't false-positive).
    await agent_module.run_agent(ctx)
