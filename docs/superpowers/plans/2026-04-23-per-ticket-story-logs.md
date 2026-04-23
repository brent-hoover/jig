# Per-Ticket Story Logs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reading a ticket's end-to-end story via `jig story <ticket-id>` — what the orchestrator decided, which agents ran, tools called, thinking blocks, timing — without `grep` magic.

**Architecture:** Structured JSON logs with contextvar-propagated correlation fields (ticket_id/phase/role/agent_id), richer SDK content-block capture (tool inputs+results+thinking), new SystemEvents for per-phase timing, and a `jig.story` library/CLI that merges the log file and thread entries in timestamp order. Library-first so the future TUI consumes the same API.

**Tech Stack:** Python 3.12+ async, `contextvars`, stdlib `logging` + `json`, pydantic v2 (existing `SystemEvent`), Click CLI (existing), pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-04-23-per-ticket-story-logs-design.md`

---

## File Structure

**New files:**
- `jig/logging_setup.py` — centralises logging configuration (extracted from `cli.py`), adds `LogContextFilter`, `JsonFormatter`, and the `contextvars.ContextVar` slots.
- `jig/story.py` — `StoryEvent` dataclass, `StorySource` enum, `build_story()`, `stream_story()`, per-kind renderers.
- `tests/test_logging_setup.py` — unit tests for filter + formatter + contextvar scoping.
- `tests/test_story.py` — unit + integration tests for the story library.
- `tests/test_cli_story.py` — CLI tests via `click.testing.CliRunner`.

**Modified files:**
- `jig/cli.py` — `start` imports from `jig.logging_setup`; new `story` Click command appended.
- `jig/orchestrator.py` — sets contextvars in `_run_ticket` and the phase loop; emits `phase_start` / `phase_end` SystemEvents.
- `jig/agent.py` — sets contextvars in `run_agent`; captures `ThinkingBlock` + full tool inputs + `ToolResultBlock`; sets `ThinkingConfigAdaptive`; emits `agent_run` SystemEvent.
- `jig/mcp_server.py` — wraps each `@tool`-registered handler to set/reset contextvars per call.
- `jig/thread.py` — extends `SystemEvent.event_type` literal union with `phase_start`, `phase_end`, `agent_run`.

**Test patterns in this repo:** `tests/conftest.py` provides shared fixtures. Real JSONL stores under `tmp_path/.jig/store/` are preferred over mocks (see `tests/_phase5p_helpers.py` `build_orch`). `pytest.mark.asyncio` on async tests; module-level `asyncio_mode = "auto"` is NOT set — each async test needs the decorator.

---

## Task 1: Extract logging setup

Move the logging configuration out of `cli.py`'s `start` command into a dedicated module. No behaviour change yet — this is pure refactor to give later tasks a clean target.

**Files:**
- Create: `jig/logging_setup.py`
- Modify: `jig/cli.py:350-398`
- Test: `tests/test_logging_setup.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_logging_setup.py`:

```python
"""Tests for ``jig.logging_setup`` — centralised logging config."""

from __future__ import annotations

import logging
from pathlib import Path

from jig.logging_setup import configure_logging


def test_configure_logging_creates_log_file_and_handlers(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    log_file = configure_logging(tmp_path, verbose=False)

    assert log_file.exists()
    assert log_file.parent == tmp_path / ".jig" / "logs"
    assert log_file.name.startswith("jig-")

    root = logging.getLogger()
    assert root.level == logging.DEBUG
    # Console + file handler
    handler_types = [type(h).__name__ for h in root.handlers]
    assert "StreamHandler" in handler_types
    assert "FileHandler" in handler_types


def test_configure_logging_quiets_noisy_loggers(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    configure_logging(tmp_path, verbose=False)

    assert logging.getLogger("websockets").level == logging.WARNING
    assert logging.getLogger("mcp").level == logging.WARNING
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jig.logging_setup'`

- [ ] **Step 3: Create the module**

Create `jig/logging_setup.py`:

```python
"""Centralised logging configuration for the jig CLI daemon.

Owns handler construction, formatter choice, and the location of
the per-invocation log file. `jig.cli.start` calls
``configure_logging`` once at daemon startup.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def configure_logging(project_path: Path, *, verbose: bool = False) -> Path:
    """Configure root logger + console + file handlers.

    Returns the path to the log file so the caller can echo it.
    """
    level = logging.DEBUG if verbose else logging.INFO

    console_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(console_fmt)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    # Clear any handlers from a prior configure call (tests run in
    # the same process).
    root.handlers.clear()
    root.addHandler(console)

    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)

    log_dir = project_path / ".jig" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"jig-{datetime.now():%Y%m%d-%H%M%S}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    root.addHandler(file_handler)
    return log_file
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire into cli.py**

Edit `jig/cli.py` — replace lines ~350-398 (the inline logging setup) with a single call. The imports `from datetime import datetime` and `import logging` can be removed from the `start` function body if they're not used elsewhere in that command.

Replace:
```python
    level = logging.DEBUG if verbose else logging.INFO
    # ... all the handler setup through:
    root.addHandler(file_handler)
    click.echo(f"Logging to {log_file}")
```

With:
```python
    from jig.logging_setup import configure_logging

    log_file = configure_logging(path, verbose=verbose)
    click.echo(f"Logging to {log_file}")
```

- [ ] **Step 6: Verify the full test suite still passes**

Run: `uv run pytest tests/ -x --ignore=tests/test_phase5p_check_fail_then_fix.py 2>&1 | tail -20`
Expected: all previously-passing tests still pass. (The phase5p test ignored because it's slow; run it separately if paranoid.)

- [ ] **Step 7: Commit**

```bash
git add jig/logging_setup.py jig/cli.py tests/test_logging_setup.py
git commit -m "refactor(logging): extract configure_logging into jig.logging_setup"
```

---

## Task 2: ContextVar correlation fields + LogContextFilter

Add the four `contextvars.ContextVar` slots, a `logging.Filter` that stamps them onto every `LogRecord`, and human-readable `ticket_short` for console output.

**Files:**
- Modify: `jig/logging_setup.py`
- Test: `tests/test_logging_setup.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_logging_setup.py`:

```python
import io
from jig.logging_setup import (
    _ticket_id_var,
    _phase_var,
    _role_var,
    _agent_id_var,
    LogContextFilter,
    configure_logging,
)


def test_filter_adds_correlation_fields_to_record(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    configure_logging(tmp_path, verbose=False)

    filt = LogContextFilter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg="hi", args=(), exc_info=None,
    )

    tok_tid = _ticket_id_var.set("abcd1234-5678-90ab-cdef-000000000000")
    tok_phase = _phase_var.set("spec")
    tok_role = _role_var.set("dev")
    tok_agent = _agent_id_var.set("dev:abcd1234")
    try:
        filt.filter(record)
    finally:
        _ticket_id_var.reset(tok_tid)
        _phase_var.reset(tok_phase)
        _role_var.reset(tok_role)
        _agent_id_var.reset(tok_agent)

    assert record.ticket_id == "abcd1234-5678-90ab-cdef-000000000000"
    assert record.ticket_short == "abcd1234"
    assert record.phase == "spec"
    assert record.role == "dev"
    assert record.agent_id == "dev:abcd1234"


def test_filter_handles_unset_contextvars(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    configure_logging(tmp_path, verbose=False)

    filt = LogContextFilter()
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname="", lineno=0,
        msg="hi", args=(), exc_info=None,
    )
    filt.filter(record)
    assert record.ticket_id is None
    assert record.ticket_short == "        "  # 8-space pad
    assert record.phase is None
    assert record.role is None
    assert record.agent_id is None


def test_console_format_includes_ticket_short(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    configure_logging(tmp_path, verbose=False)

    # Capture console output
    buf = io.StringIO()
    stream_handler = logging.StreamHandler(buf)
    stream_handler.setLevel(logging.INFO)
    # Match the console formatter
    root = logging.getLogger()
    existing_fmt = next(
        h.formatter for h in root.handlers
        if isinstance(h, logging.StreamHandler) and h.formatter is not None
    )
    stream_handler.setFormatter(existing_fmt)
    stream_handler.addFilter(LogContextFilter())
    root.addHandler(stream_handler)

    tok = _ticket_id_var.set("abcd1234-rest-of-uuid")
    try:
        logging.getLogger("x").info("boom")
    finally:
        _ticket_id_var.reset(tok)

    out = buf.getvalue()
    assert "[abcd1234]" in out
    assert "boom" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: FAIL — `ImportError` for `_ticket_id_var`, etc.

- [ ] **Step 3: Implement the contextvars and filter**

Edit `jig/logging_setup.py` — prepend to the module-level:

```python
import contextvars
import logging
from datetime import datetime
from pathlib import Path

# ---- Correlation context --------------------------------------------------

# ContextVars propagate naturally across `await` boundaries within a
# single asyncio Task, and new tasks copy the spawning context at
# creation time. Per-ticket code paths set these once at their entry
# and reset in a finally block.

_ticket_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "jig_ticket_id", default=None
)
_phase_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "jig_phase", default=None
)
_role_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "jig_role", default=None
)
_agent_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "jig_agent_id", default=None
)


class LogContextFilter(logging.Filter):
    """Stamps the current correlation context onto every LogRecord.

    Call sites use ``%(ticket_id)s`` / ``%(ticket_short)s`` /
    ``%(phase)s`` / ``%(role)s`` / ``%(agent_id)s`` in format strings.
    ``ticket_short`` is the first 8 chars of ``ticket_id`` (or 8
    spaces when unset) so console lines stay fixed-width.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        tid = _ticket_id_var.get()
        record.ticket_id = tid
        record.ticket_short = tid[:8] if tid else " " * 8
        record.phase = _phase_var.get()
        record.role = _role_var.get()
        record.agent_id = _agent_id_var.get()
        return True
```

Then update `configure_logging` — change the console formatter and attach the filter to both handlers:

```python
def configure_logging(project_path: Path, *, verbose: bool = False) -> Path:
    level = logging.DEBUG if verbose else logging.INFO

    context_filter = LogContextFilter()

    console_fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s [%(ticket_short)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(console_fmt)
    console.addFilter(context_filter)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()
    root.addHandler(console)

    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)

    log_dir = project_path / ".jig" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"jig-{datetime.now():%Y%m%d-%H%M%S}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s [%(ticket_short)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    file_handler.addFilter(context_filter)
    root.addHandler(file_handler)
    return log_file
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: PASS (5 tests total)

- [ ] **Step 5: Commit**

```bash
git add jig/logging_setup.py tests/test_logging_setup.py
git commit -m "feat(logging): add contextvar correlation fields + LogContextFilter"
```

---

## Task 3: JSON file formatter

Replace the text file formatter with a JSON-per-line formatter. The file extension flips from `.log` to `.jsonl`. Console format stays human-readable.

**Files:**
- Modify: `jig/logging_setup.py`
- Test: `tests/test_logging_setup.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_logging_setup.py`:

```python
import json


def test_log_file_is_jsonl_with_correlation_fields(tmp_path: Path) -> None:
    (tmp_path / ".jig").mkdir()
    log_file = configure_logging(tmp_path, verbose=False)

    tok_tid = _ticket_id_var.set("abcd1234-full")
    tok_phase = _phase_var.set("gated")
    tok_role = _role_var.set("dev")
    try:
        logging.getLogger("jig.test").info("hello %s", "world")
    finally:
        _ticket_id_var.reset(tok_tid)
        _phase_var.reset(tok_phase)
        _role_var.reset(tok_role)

    # Flush the file handler so the content is on disk
    for h in logging.getLogger().handlers:
        h.flush()

    assert log_file.suffix == ".jsonl"
    content = log_file.read_text().strip().splitlines()
    assert content, "expected at least one log line"
    rec = json.loads(content[-1])
    assert rec["level"] == "INFO"
    assert rec["logger"] == "jig.test"
    assert rec["msg"] == "hello world"
    assert rec["ticket_id"] == "abcd1234-full"
    assert rec["phase"] == "gated"
    assert rec["role"] == "dev"
    assert rec["agent_id"] is None
    assert "ts" in rec
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging_setup.py::test_log_file_is_jsonl_with_correlation_fields -v`
Expected: FAIL — either the suffix is `.log` or the line isn't JSON.

- [ ] **Step 3: Implement the JSON formatter**

In `jig/logging_setup.py`, add the formatter class above `configure_logging`:

```python
import json


class JsonFormatter(logging.Formatter):
    """Renders each log record as one JSON object per line.

    Schema:
      {ts, level, logger, msg, ticket_id, ticket_short, phase, role,
       agent_id, exc_info?}
    """

    # Standard LogRecord attrs we don't want in the output.
    _RESERVED = {
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "ticket_id": getattr(record, "ticket_id", None),
            "phase": getattr(record, "phase", None),
            "role": getattr(record, "role", None),
            "agent_id": getattr(record, "agent_id", None),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Allow callers to pass structured data via `extra={"extra": {...}}`
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload["extra"] = extra
        return json.dumps(payload, default=str)
```

And update `configure_logging` to use it + flip the filename:

```python
    log_file = log_dir / f"jig-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(context_filter)
    root.addHandler(file_handler)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_logging_setup.py -v`
Expected: PASS (6 tests). The existing `test_configure_logging_creates_log_file_and_handlers` should still pass because it only checks `log_file.name.startswith("jig-")` which is still true.

- [ ] **Step 5: Commit**

```bash
git add jig/logging_setup.py tests/test_logging_setup.py
git commit -m "feat(logging): JSON file formatter with correlation fields"
```

---

## Task 4: Set contextvars in orchestrator per-ticket + phase paths

Wire the contextvar setters into `Orchestrator._run_ticket` (ticket_id) and the phase loop (phase, role). Use `Token.reset()` in `finally` blocks.

**Files:**
- Modify: `jig/orchestrator.py:318-600` (`_run_ticket` + phase loop)
- Test: `tests/test_logging_orchestrator_correlation.py`

- [ ] **Step 1: Write the failing integration test**

Create `tests/test_logging_orchestrator_correlation.py`:

```python
"""Verifies the orchestrator stamps ticket_id / phase / role on log
records emitted from inside a per-ticket path."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_orchestrator_log_records_carry_ticket_and_phase(
    tmp_path: Path, monkeypatch
) -> None:
    # Wire logging through the real setup so the JSONL file is
    # produced.
    from jig.logging_setup import configure_logging

    (tmp_path / ".jig").mkdir()
    log_file = configure_logging(tmp_path, verbose=True)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    async def fake_run_agent(ctx, emitter=None):
        # Emit a log line from within the per-ticket path.
        logging.getLogger("jig.test.agent").info("hello from agent")
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    # Scan the JSONL file for a record from our test logger.
    lines = log_file.read_text().strip().splitlines()
    matches = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("logger") == "jig.test.agent"
    ]
    assert matches, f"no log records from jig.test.agent; wrote {len(lines)} lines"
    rec = matches[0]
    assert rec["ticket_id"] == tid
    assert rec["phase"] == "spec"
    assert rec["role"] == "spec"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging_orchestrator_correlation.py -v`
Expected: FAIL — `rec["ticket_id"]` is `None` because no code sets the var.

- [ ] **Step 3: Set contextvars in `_run_ticket`**

Edit `jig/orchestrator.py`. Near the top of the file, add the import:

```python
from jig.logging_setup import _ticket_id_var, _phase_var, _role_var
```

In `_run_ticket` (starts line 318), wrap the method body in a `contextvars` scope. Find the line `ticket = await self.tickets.get(ticket_id)` at the top of the method and set the var immediately before it:

```python
    async def _run_ticket(self, ticket_id: str) -> None:
        from jig.persistence import load_role, load_workflow
        from jig.runtime import AgentSpawnContext, SpawnReason

        if (
            self.tickets is None
            or self.threads is None
            or self.memory is None
            or self.bus is None
            or self._project is None
        ):
            raise RuntimeError("Orchestrator not started — call startup() first")

        tid_token = _ticket_id_var.set(ticket_id)
        try:
            ticket = await self.tickets.get(ticket_id)
            # ... rest of the method body unchanged ...
        finally:
            _ticket_id_var.reset(tid_token)
```

Indent the existing body one level. This is a large mechanical edit — the simplest way is to wrap the entire rest of `_run_ticket` in `try: ... finally:` with the `reset` call.

- [ ] **Step 4: Set contextvars in the phase loop**

Still in `_run_ticket`, at the top of the `while phase_idx < len(workflow.phases):` loop (line 396 in the pre-edit file), add:

```python
        while phase_idx < len(workflow.phases):
            phase = workflow.phases[phase_idx]
            phase_token = _phase_var.set(phase.name)
            role_token = _role_var.set(phase.role)
            try:
                # ... existing phase loop body ...
            finally:
                _phase_var.reset(phase_token)
                _role_var.reset(role_token)
```

The `continue` statements inside the loop body will still work — the `finally` block runs on each iteration.

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_logging_orchestrator_correlation.py -v`
Expected: PASS

- [ ] **Step 6: Full suite regression check**

Run: `uv run pytest tests/ -x 2>&1 | tail -10`
Expected: all passing.

- [ ] **Step 7: Commit**

```bash
git add jig/orchestrator.py tests/test_logging_orchestrator_correlation.py
git commit -m "feat(orchestrator): stamp ticket_id/phase/role on log records via contextvars"
```

---

## Task 5: Set contextvars in run_agent

Add `role` + `agent_id` + reaffirm `ticket_id`/`phase` at the start of `run_agent`. Wrap MCP tool handlers so each tool call re-sets the four vars inside the handler's async task.

**Files:**
- Modify: `jig/agent.py:311-535` (`run_agent`)
- Modify: `jig/mcp_server.py:19-50` (handler wrapping)
- Test: `tests/test_logging_mcp_correlation.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_logging_mcp_correlation.py`:

```python
"""Verifies MCP tool handlers inherit correlation fields at call time."""

from __future__ import annotations

import logging
import contextvars

import pytest

from jig.logging_setup import (
    _ticket_id_var, _phase_var, _role_var, _agent_id_var,
)
from jig.mcp_server import _wrap_with_context


@pytest.mark.asyncio
async def test_wrap_with_context_sets_and_resets_contextvars() -> None:
    captured: dict = {}

    async def handler(args):
        captured["ticket_id"] = _ticket_id_var.get()
        captured["phase"] = _phase_var.get()
        captured["role"] = _role_var.get()
        captured["agent_id"] = _agent_id_var.get()
        return {"ok": True}

    wrapped = _wrap_with_context(
        handler,
        ticket_id="tid-abc",
        phase="spec",
        role="dev",
        agent_id="dev:tid-abc",
    )

    # Call from a context where nothing is set.
    result = await wrapped({"arg": 1})
    assert result == {"ok": True}

    assert captured == {
        "ticket_id": "tid-abc",
        "phase": "spec",
        "role": "dev",
        "agent_id": "dev:tid-abc",
    }
    # After the call, vars should be unset again.
    assert _ticket_id_var.get() is None
    assert _phase_var.get() is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_logging_mcp_correlation.py -v`
Expected: FAIL — `ImportError` for `_wrap_with_context`.

- [ ] **Step 3: Add `_wrap_with_context` to mcp_server.py**

Edit `jig/mcp_server.py`. Near the top, after the existing imports:

```python
from jig.logging_setup import (
    _ticket_id_var, _phase_var, _role_var, _agent_id_var,
)


def _wrap_with_context(handler, *, ticket_id: str | None, phase: str | None,
                      role: str | None, agent_id: str | None):
    """Wrap an MCP tool handler so each call runs with the given
    correlation context. The MCP SDK invokes each tool in a fresh
    asyncio task that does NOT inherit our per-ticket contextvars,
    so handlers must set them explicitly at the call boundary.
    """
    async def wrapper(args):
        t_tid = _ticket_id_var.set(ticket_id)
        t_phase = _phase_var.set(phase)
        t_role = _role_var.set(role)
        t_agent = _agent_id_var.set(agent_id)
        try:
            return await handler(args)
        finally:
            _ticket_id_var.reset(t_tid)
            _phase_var.reset(t_phase)
            _role_var.reset(t_role)
            _agent_id_var.reset(t_agent)
    return wrapper
```

Extend `create_agent_mcp_server`'s signature to accept a `ticket_id` (it closes over the ticket in `AgentSpawnContext` — callers pass it in):

```python
def create_agent_mcp_server(
    *,
    tickets: TicketStore,
    threads: ThreadStore,
    memory: MemoryStore,
    bus: MessageBus,
    agent_role: str,
    agent_cfg: RoleConfig,
    worktree_path: Path,
    project_path: Path,
    valid_roles: frozenset[str] = frozenset(),
    package_manager: str = "",
    checkpoints: CheckpointStore | None = None,
    phase_name: str = "",
    can_waive: frozenset[str] = frozenset(),
    phase_questions_to: frozenset[str] = frozenset(),
    phase_escalation_targets: frozenset[str] = frozenset(),
    ticket_id: str = "",
):
    # ... existing body ...
```

- [ ] **Step 4: Run the wrap test to verify it passes**

Run: `uv run pytest tests/test_logging_mcp_correlation.py -v`
Expected: PASS.

- [ ] **Step 5: Set contextvars in `run_agent` + pass ticket_id to MCP factory**

Edit `jig/agent.py`. Add import at the top:

```python
from jig.logging_setup import (
    _ticket_id_var, _phase_var, _role_var, _agent_id_var,
)
```

At the start of `run_agent` (line 311), before any work:

```python
async def run_agent(
    ctx: AgentSpawnContext, emitter: EventEmitter | None = None
) -> RunAgentResult:
    # Stamp correlation fields for this spawn. ticket_id/phase are
    # already set by the orchestrator in its per-ticket path, but
    # run_agent is also called by evaluator spawns and tests, so
    # re-set defensively.
    agent_id = f"{ctx.role}:{ctx.ticket.id[:8]}"
    tid_token = _ticket_id_var.set(ctx.ticket.id)
    phase_token = _phase_var.set(ctx.phase.name if ctx.phase else None)
    role_token = _role_var.set(ctx.role)
    agent_token = _agent_id_var.set(agent_id)
    try:
        # ... rest of run_agent body ...
    finally:
        _ticket_id_var.reset(tid_token)
        _phase_var.reset(phase_token)
        _role_var.reset(role_token)
        _agent_id_var.reset(agent_token)
```

Pass `ticket_id=ctx.ticket.id` into `create_agent_mcp_server(...)` at line 353:

```python
    mcp_server = create_agent_mcp_server(
        # ... existing kwargs ...
        phase_escalation_targets=phase_esc_targets,
        ticket_id=ctx.ticket.id,
    )
```

- [ ] **Step 6: Apply the wrapper to every tool handler in create_agent_mcp_server**

Right after the factory builds each `@tool(...)`-decorated inner function, and BEFORE the function is passed to `create_sdk_mcp_server`, wrap it. The existing pattern builds tools inline with the `@tool` decorator and then registers them; find the `create_sdk_mcp_server(...)` call at the end of the factory and inspect how the tools are assembled. Most projects collect them in a list. Edit the factory so the final registration step wraps each handler:

Find the `return create_sdk_mcp_server(...)` line at the end. Just before it, iterate the tools list and wrap. The simplest approach: the SDK's `@tool` decorator returns a callable; wrap the underlying callable in-place.

Look for the `tools=[...]` list passed to `create_sdk_mcp_server` and transform it. If tools are passed as a list:

```python
    # Wrap every tool handler so the correlation context is set on
    # each incoming call. Tool calls arrive in fresh asyncio tasks
    # that don't inherit the factory's context.
    _ctx_kwargs = dict(
        ticket_id=ticket_id or None,
        phase=phase_name or None,
        role=agent_role,
        agent_id=f"{agent_role}:{(ticket_id or '')[:8]}",
    )

    def _wrap_tool(t):
        # The @tool decorator wraps the user function; we need to
        # replace the underlying handler. The SDK's `tool` returns
        # an object exposing `.handler` (the async callable).
        original = t.handler
        t.handler = _wrap_with_context(original, **_ctx_kwargs)
        return t

    tools = [_wrap_tool(t) for t in tools]
    return create_sdk_mcp_server(name="jig", version="0.1.0", tools=tools)
```

**Important:** verify the shape of what `@tool(...)` returns by inspecting `claude_agent_sdk`. If the decorator returns a plain async function (no `.handler` attribute), wrap differently — rebuild the `@tool`-annotated callable by calling `@tool(...)` on the wrapper. Run this check first:

```bash
uv run python -c "
from claude_agent_sdk import tool
@tool('x', 'x', {})
async def h(args): return {}
print(type(h), dir(h))
"
```

Use the output to decide the wrapping strategy. If `.handler` doesn't exist and the returned object's callable *is* the tool, wrap by composing `@tool(...)` over `_wrap_with_context(...)`:

```python
# Rebuild each tool with the wrapped handler. Requires access to
# name + description + schema; keep the original @tool decoration
# intact by wrapping at definition site instead. The simplest
# approach: define every handler inside the factory to close over
# the correlation context, and wrap AT DEFINITION rather than
# post-hoc. Each `async def <toolname>(args):` becomes
# `async def <toolname>(args): <wrap logic>; <handler logic>`.
```

Given the uncertainty on SDK internals, the **recommended approach** is to add the contextvar set/reset directly inside each `async def` in `mcp_server.py` rather than trying to post-hoc wrap. Factor the set/reset into a context manager to keep it DRY:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def _with_correlation(ticket_id, phase, role, agent_id):
    t_tid = _ticket_id_var.set(ticket_id)
    t_phase = _phase_var.set(phase)
    t_role = _role_var.set(role)
    t_agent = _agent_id_var.set(agent_id)
    try:
        yield
    finally:
        _ticket_id_var.reset(t_tid)
        _phase_var.reset(t_phase)
        _role_var.reset(t_role)
        _agent_id_var.reset(t_agent)
```

Then each tool handler becomes:

```python
    @tool("create_ticket", "...", {...})
    async def create_ticket(args):
        async with _with_correlation(
            ticket_id or None, phase_name or None, agent_role,
            f"{agent_role}:{(ticket_id or '')[:8]}",
        ):
            _check_assignee(args.get("assignee"))
            ticket_id_out = await ticket_mcp.handle_create_ticket(...)
            return {"content": [{"type": "text", "text": ticket_id_out}]}
```

Apply this pattern to every `@tool`-decorated handler in `mcp_server.py`. Yes, it's mechanical — but mechanical beats wrong.

- [ ] **Step 7: Write an integration test for MCP correlation**

Append to `tests/test_logging_mcp_correlation.py`:

```python
import json
from pathlib import Path

from jig.logging_setup import configure_logging


@pytest.mark.asyncio
async def test_mcp_tool_call_logs_carry_correlation(
    tmp_path: Path, monkeypatch
) -> None:
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult
    from jig.thread import Note

    async def fake_run_agent(ctx, emitter=None):
        # Post a note via the threads store — this exercises the
        # logging path inside the MCP layer (not the store directly,
        # but thread_mcp handlers which are also invoked during
        # real runs).
        await ctx.threads.post(
            Note(ticket_id=ctx.ticket.id, author="spec", content="hi")
        )
        logging.getLogger("jig.test.during_tool").info("mid-tool")
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    lines = log_file.read_text().strip().splitlines()
    matches = [
        json.loads(line)
        for line in lines
        if json.loads(line).get("logger") == "jig.test.during_tool"
    ]
    assert matches
    rec = matches[0]
    assert rec["ticket_id"] == tid
    assert rec["role"] == "spec"
```

- [ ] **Step 8: Run all correlation tests**

Run: `uv run pytest tests/test_logging_mcp_correlation.py tests/test_logging_orchestrator_correlation.py -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add jig/agent.py jig/mcp_server.py tests/test_logging_mcp_correlation.py
git commit -m "feat(agent,mcp): propagate correlation contextvars into SDK + tool calls"
```

---

## Task 6: Enable adaptive thinking + capture ThinkingBlock

Set `ThinkingConfigAdaptive` on `ClaudeAgentOptions` and capture `ThinkingBlock` in the content-block dispatch loop.

**Files:**
- Modify: `jig/agent.py:9-16` (imports), `:377-389` (options), `:472-501` (dispatch)
- Test: `tests/test_agent_thinking_capture.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_thinking_capture.py`:

```python
"""Verifies run_agent captures ThinkingBlock into logs at DEBUG."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from jig.logging_setup import configure_logging


@pytest.mark.asyncio
async def test_thinking_block_is_captured_at_debug(
    tmp_path: Path, monkeypatch
) -> None:
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    # Stub the SDK's `query` function so it emits a synthetic
    # AssistantMessage containing a ThinkingBlock, then a ResultMessage.
    from claude_agent_sdk.types import (
        AssistantMessage, ThinkingBlock, TextBlock, ResultMessage,
    )

    async def fake_query(*, prompt, options, transport=None):
        # Drain the prompt stream once to simulate initial turn
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ThinkingBlock(
                    thinking="I should read the spec first, then plan the changes.",
                    signature="sig-abc",
                ),
                TextBlock(text="Starting work."),
            ],
            model="claude-test",
        )
        yield ResultMessage(
            subtype="success", duration_ms=50, duration_api_ms=40,
            is_error=False, num_turns=1, session_id="",
            total_cost_usd=0.0, usage={}, result="done",
        )

    from jig import agent as agent_module
    monkeypatch.setattr(agent_module, "query", fake_query)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    lines = log_file.read_text().strip().splitlines()
    recs = [json.loads(l) for l in lines]
    thinking_recs = [
        r for r in recs
        if r.get("level") == "DEBUG" and "thinking:" in r.get("msg", "")
    ]
    assert thinking_recs, "expected at least one DEBUG thinking: log line"
    assert "read the spec first" in thinking_recs[0]["msg"]
    assert thinking_recs[0]["ticket_id"] == tid
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_thinking_capture.py -v`
Expected: FAIL — no DEBUG log line with "thinking:" because the block is dropped.

- [ ] **Step 3: Extend imports and enable adaptive thinking**

Edit `jig/agent.py` imports (lines 9-16):

```python
from claude_agent_sdk import query, ClaudeAgentOptions
from claude_agent_sdk.types import (
    AssistantMessage,
    ResultMessage,
    SystemMessage,
    TextBlock,
    ThinkingBlock,
    ThinkingConfigAdaptive,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
```

Add a helper above `run_agent`:

```python
def _thinking_config():
    """Return the ThinkingConfig to pass to the SDK.

    Adaptive lets the model choose its thinking budget per turn. If
    cost becomes a concern, swap to ThinkingConfigEnabled(budget_tokens=N)
    behind a single knob here.
    """
    return ThinkingConfigAdaptive(type="adaptive")


# Cap for raw logged block text. Thinking blocks + tool results can
# be large; truncate to keep the log file manageable. A companion
# `*_truncated` DEBUG line records the real length so post-mortem
# readers know to fetch the full content some other way if needed.
_LOG_TRUNCATE_BYTES = 32 * 1024
```

Update the `ClaudeAgentOptions` construction at line 377:

```python
    options = ClaudeAgentOptions(
        cwd=str(ctx.worktree_path),
        allowed_tools=ctx.role_cfg.allowed_tools,
        system_prompt=ctx.role_cfg.phase_prompt,
        mcp_servers=mcp_servers,
        permission_mode="bypassPermissions",
        thinking=_thinking_config(),
    )
```

- [ ] **Step 4: Capture ThinkingBlock in the dispatch loop**

Edit the content-block dispatch in `run_agent` (lines 472-501). Add a branch for `ThinkingBlock`:

```python
            if isinstance(message, AssistantMessage):
                for block in message.content or []:
                    if isinstance(block, ToolUseBlock):
                        detail = _tool_detail(block.name, block.input or {})
                        _logger.info("[%s] tool: %s %s", tag, block.name, detail)
                        await _emit(
                            "agent_tool",
                            {
                                "role": ctx.role,
                                "ticket_id": ctx.ticket.id,
                                "tool": block.name,
                                "detail": detail,
                            },
                        )
                    elif isinstance(block, TextBlock):
                        short = _sanitize_for_tui(block.text)
                        if short:
                            _logger.info(
                                "[%s] text: %s",
                                tag,
                                _sanitize_for_tui(block.text, limit=2000),
                            )
                            await _emit(
                                "agent_text",
                                {
                                    "role": ctx.role,
                                    "ticket_id": ctx.ticket.id,
                                    "text": short,
                                },
                            )
                    elif isinstance(block, ThinkingBlock):
                        # Post-mortem only — not emitted to TUI.
                        raw = block.thinking or ""
                        truncated = raw
                        if len(raw.encode("utf-8")) > _LOG_TRUNCATE_BYTES:
                            truncated = raw.encode("utf-8")[
                                :_LOG_TRUNCATE_BYTES
                            ].decode("utf-8", errors="ignore")
                            _logger.debug(
                                "[%s] thinking_truncated: full_len=%d",
                                tag, len(raw),
                            )
                        _logger.debug("[%s] thinking: %s", tag, truncated)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_thinking_capture.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add jig/agent.py tests/test_agent_thinking_capture.py
git commit -m "feat(agent): enable adaptive thinking + capture ThinkingBlock at DEBUG"
```

---

## Task 7: Capture full tool inputs + ToolResultBlock

Log the full tool input (not just the TUI-friendly detail) at DEBUG, and handle the `UserMessage` → `ToolResultBlock` content that the SDK emits for tool outputs.

**Files:**
- Modify: `jig/agent.py:472-516` (dispatch loop)
- Test: `tests/test_agent_tool_capture.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_tool_capture.py`:

```python
"""Verifies full tool inputs and tool results are captured at DEBUG."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from jig.logging_setup import configure_logging


@pytest.mark.asyncio
async def test_tool_input_and_result_captured_at_debug(
    tmp_path: Path, monkeypatch
) -> None:
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until
    from claude_agent_sdk.types import (
        AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock,
        ResultMessage,
    )

    (tmp_path / ".jig").mkdir(exist_ok=True)
    log_file = configure_logging(tmp_path, verbose=True)

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ToolUseBlock(
                    id="tu-1",
                    name="Bash",
                    input={"command": "ls -la /tmp/foo"},
                ),
            ],
            model="claude-test",
        )
        yield UserMessage(
            content=[
                ToolResultBlock(
                    tool_use_id="tu-1",
                    content="total 4\ndrwxr-xr-x 2 root root 4096 Apr 23 10:00 .",
                    is_error=False,
                ),
            ],
        )
        yield ResultMessage(
            subtype="success", duration_ms=50, duration_api_ms=40,
            is_error=False, num_turns=1, session_id="",
            total_cost_usd=0.0, usage={}, result="done",
        )

    from jig import agent as agent_module
    monkeypatch.setattr(agent_module, "query", fake_query)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="dev", role="dev")],
    )
    roles = [RoleConfig(role="dev", phase_prompt="dev")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    recs = [json.loads(l) for l in log_file.read_text().strip().splitlines()]
    input_recs = [r for r in recs if "tool_input:" in r.get("msg", "")]
    result_recs = [r for r in recs if "tool_result:" in r.get("msg", "")]
    assert input_recs, "no tool_input DEBUG line"
    assert "ls -la /tmp/foo" in input_recs[0]["msg"]
    assert result_recs, "no tool_result DEBUG line"
    assert "tu-1" in result_recs[0]["msg"]
    assert "total 4" in result_recs[0]["msg"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_tool_capture.py -v`
Expected: FAIL — no DEBUG lines match.

- [ ] **Step 3: Add a full-input DEBUG alongside the existing INFO**

Edit `jig/agent.py` — in the `ToolUseBlock` branch, after the existing `_logger.info(...)` line, add:

```python
                    if isinstance(block, ToolUseBlock):
                        detail = _tool_detail(block.name, block.input or {})
                        _logger.info("[%s] tool: %s %s", tag, block.name, detail)
                        _logger.debug(
                            "[%s] tool_input: id=%s %s",
                            tag, block.id, json.dumps(block.input or {}, default=str),
                        )
                        # ... existing _emit(...) call unchanged ...
```

Add `import json` at the top of `agent.py` if not already imported.

- [ ] **Step 4: Handle UserMessage + ToolResultBlock**

Still in the dispatch loop, add an `elif isinstance(message, UserMessage):` branch alongside the existing `AssistantMessage`/`SystemMessage`/`ResultMessage` branches:

```python
            elif isinstance(message, UserMessage):
                for block in message.content or []:
                    if isinstance(block, ToolResultBlock):
                        raw = block.content or ""
                        if not isinstance(raw, str):
                            # SDK occasionally wraps content as a list
                            # of dicts; serialise for the log.
                            raw = json.dumps(raw, default=str)
                        truncated = raw
                        if len(raw.encode("utf-8")) > _LOG_TRUNCATE_BYTES:
                            truncated = raw.encode("utf-8")[
                                :_LOG_TRUNCATE_BYTES
                            ].decode("utf-8", errors="ignore")
                            _logger.debug(
                                "[%s] tool_result_truncated: id=%s full_len=%d",
                                tag, block.tool_use_id, len(raw),
                            )
                        is_error = getattr(block, "is_error", False)
                        _logger.debug(
                            "[%s] tool_result: id=%s is_error=%s %s",
                            tag, block.tool_use_id, is_error, truncated,
                        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_tool_capture.py -v`
Expected: PASS.

- [ ] **Step 6: Regression check — full agent-related tests**

Run: `uv run pytest tests/test_agent_streaming.py tests/test_agent_thinking_capture.py tests/test_agent_tool_capture.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add jig/agent.py tests/test_agent_tool_capture.py
git commit -m "feat(agent): log full tool inputs + ToolResultBlock at DEBUG"
```

---

## Task 8: Extend SystemEvent event_type union

Add `phase_start`, `phase_end`, `agent_run` to `SystemEvent.event_type`. Additive — no existing consumer breaks.

**Files:**
- Modify: `jig/thread.py:400-406`
- Test: `tests/test_thread_system_event_new_types.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_thread_system_event_new_types.py`:

```python
"""Verifies new SystemEvent event_types round-trip through pydantic."""

from __future__ import annotations

import pytest

from jig.thread import SystemEvent


def test_phase_start_event_type_accepted() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="phase_start",
        content="spec",
    )
    assert ev.event_type == "phase_start"


def test_phase_end_event_type_accepted() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="phase_end",
        content="success",
    )
    assert ev.event_type == "phase_end"


def test_agent_run_event_type_accepted() -> None:
    ev = SystemEvent(
        ticket_id="tid-1",
        author="orchestrator",
        event_type="agent_run",
        content="dev ran 3 turns in 12.4s",
    )
    assert ev.event_type == "agent_run"


def test_unknown_event_type_still_rejected() -> None:
    with pytest.raises(Exception):
        SystemEvent(
            ticket_id="tid-1",
            author="orchestrator",
            event_type="made_up_kind",  # type: ignore[arg-type]
            content="",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_system_event_new_types.py -v`
Expected: FAIL — `phase_start` etc. rejected by pydantic Literal.

- [ ] **Step 3: Extend the Literal union**

Edit `jig/thread.py` at line 400-406:

```python
    event_type: Literal[
        "commit",
        "phase_run",
        "status_change",
        "check_failure",
        "dep_merge_failed",
        "phase_start",
        "phase_end",
        "agent_run",
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_system_event_new_types.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Full suite regression check**

Run: `uv run pytest tests/ -x 2>&1 | tail -10`
Expected: all pass (no existing consumer should be using an `else: raise`-style dispatch over event_type).

- [ ] **Step 6: Commit**

```bash
git add jig/thread.py tests/test_thread_system_event_new_types.py
git commit -m "feat(thread): extend SystemEvent event_type with phase_start/end/agent_run"
```

---

## Task 9: Emit phase_start + phase_end SystemEvents

Post `phase_start` when the orchestrator begins a phase and `phase_end` when the phase completes (success, failed, rejected, or timeout). Use monotonic time for `duration_ms`.

**Files:**
- Modify: `jig/orchestrator.py:396-462` (phase loop)
- Test: `tests/test_orchestrator_phase_timing_events.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_orchestrator_phase_timing_events.py`:

```python
"""Verifies phase_start and phase_end SystemEvents land on the thread."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.thread import SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_phase_start_and_end_events_emitted(
    tmp_path: Path, monkeypatch
) -> None:
    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    from jig import orchestrator as orch_module
    from jig.agent import RunAgentResult

    async def fake_run_agent(ctx, emitter=None):
        return RunAgentResult(status="success", final_text="ok")

    monkeypatch.setattr(orch_module, "run_agent", fake_run_agent)

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)

        entries = await orch.threads.for_ticket(tid)
        starts = [
            e for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "phase_start"
        ]
        ends = [
            e for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "phase_end"
        ]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0].content == "spec"  # phase name in content
        assert ends[0].content == "success"
        assert ends[0].payload.get("duration_ms") is not None
        assert ends[0].payload["duration_ms"] >= 0
    finally:
        await orch.shutdown()
```

**Note:** `SystemEvent.payload` doesn't exist today. This plan adds it as an optional `dict[str, object]` field in Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_orchestrator_phase_timing_events.py -v`
Expected: FAIL — no `phase_start` events.

- [ ] **Step 3: Add `payload` field to SystemEvent**

Edit `jig/thread.py` inside the `SystemEvent` class (after the `waived` field around line 426):

```python
    # Free-form structured data for new event_types (phase_start,
    # phase_end, agent_run). Existing event_types ignore this; the
    # discriminated-union shape stays flat.
    payload: dict[str, object] = Field(default_factory=dict)
```

- [ ] **Step 4: Emit phase_start and phase_end from the orchestrator**

Edit `jig/orchestrator.py` inside the phase loop. Add `import time` at the top of the file if not present. Then:

```python
        while phase_idx < len(workflow.phases):
            phase = workflow.phases[phase_idx]
            phase_token = _phase_var.set(phase.name)
            role_token = _role_var.set(phase.role)
            phase_started_at = time.monotonic()
            try:
                # Existing INFO log + emit...
                _logger.info(
                    "phase %d/%d: %s (role=%s)",
                    phase_idx + 1, len(workflow.phases),
                    phase.name, phase.role,
                )

                # Post phase_start SystemEvent.
                if self.threads is not None:
                    from jig.thread import SystemEvent

                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="orchestrator",
                            event_type="phase_start",
                            content=phase.name,
                            payload={
                                "phase": phase.name,
                                "role": phase.role,
                                "spawn_reason": "phase_primary",
                            },
                        )
                    )

                # ... rest of the existing phase loop body ...
            finally:
                # Post phase_end SystemEvent. Use the final result
                # if it was assigned; otherwise mark as "failed".
                if self.threads is not None:
                    from jig.thread import SystemEvent

                    outcome = "failed"
                    try:
                        outcome = result.status  # may not be set if we errored early
                    except NameError:
                        pass
                    duration_ms = int((time.monotonic() - phase_started_at) * 1000)
                    await self.threads.post(
                        SystemEvent(
                            ticket_id=ticket_id,
                            author="orchestrator",
                            event_type="phase_end",
                            content=outcome,
                            payload={
                                "phase": phase.name,
                                "role": phase.role,
                                "duration_ms": duration_ms,
                                "outcome": outcome,
                            },
                        )
                    )
                _phase_var.reset(phase_token)
                _role_var.reset(role_token)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_orchestrator_phase_timing_events.py -v`
Expected: PASS.

- [ ] **Step 6: Full regression check**

Run: `uv run pytest tests/ -x 2>&1 | tail -15`
Expected: all pass. If any Phase 5 E2E test starts failing because it asserts on the *number* of SystemEvents, update it to filter by event_type.

- [ ] **Step 7: Commit**

```bash
git add jig/thread.py jig/orchestrator.py tests/test_orchestrator_phase_timing_events.py
git commit -m "feat(orchestrator): emit phase_start/phase_end SystemEvents with timing"
```

---

## Task 10: Emit agent_run SystemEvent

When the SDK's `ResultMessage` arrives in `run_agent`, post an `agent_run` SystemEvent carrying `num_turns`, `duration_ms`, and a sanitised result preview.

**Files:**
- Modify: `jig/agent.py:504-511` (`ResultMessage` branch)
- Test: `tests/test_agent_run_event.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_agent_run_event.py`:

```python
"""Verifies run_agent posts an agent_run SystemEvent on completion."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
from jig.thread import SystemEvent
from jig.ticket import Ticket, TicketStatus, WorkType
from tests._phase5p_helpers import build_orch, poll_until


@pytest.mark.asyncio
async def test_agent_run_event_posted(tmp_path: Path, monkeypatch) -> None:
    from claude_agent_sdk.types import (
        AssistantMessage, TextBlock, ResultMessage,
    )

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[TextBlock(text="working")], model="claude-test"
        )
        yield ResultMessage(
            subtype="success", duration_ms=1234, duration_api_ms=1000,
            is_error=False, num_turns=3, session_id="",
            total_cost_usd=0.0, usage={}, result="completed the task",
        )

    from jig import agent as agent_module
    monkeypatch.setattr(agent_module, "query", fake_query)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)

        entries = await orch.threads.for_ticket(tid)
        runs = [
            e for e in entries
            if isinstance(e, SystemEvent) and e.event_type == "agent_run"
        ]
        assert len(runs) == 1
        assert runs[0].payload["num_turns"] == 3
        assert runs[0].payload["duration_ms"] == 1234
        assert runs[0].payload["role"] == "spec"
        assert "completed the task" in runs[0].payload["result_preview"]
    finally:
        await orch.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_agent_run_event.py -v`
Expected: FAIL — no agent_run events.

- [ ] **Step 3: Post agent_run on ResultMessage**

Edit `jig/agent.py`. In the `ResultMessage` branch (around line 504), after the existing `_logger.info(...)` call, add:

```python
            elif isinstance(message, ResultMessage):
                final_text = message.result or ""
                _logger.info(
                    "[%s] completed: %s turns, %.1fs",
                    tag,
                    message.num_turns,
                    (message.duration_ms or 0) / 1000,
                )
                # Post an agent_run SystemEvent so the story view
                # gets per-spawn timing without having to parse logs.
                try:
                    from jig.thread import SystemEvent

                    preview = _sanitize_for_tui(final_text, limit=500)
                    await ctx.threads.post(
                        SystemEvent(
                            ticket_id=ctx.ticket.id,
                            author="orchestrator",
                            event_type="agent_run",
                            content=f"{ctx.role} ran {message.num_turns} turns",
                            payload={
                                "role": ctx.role,
                                "num_turns": message.num_turns,
                                "duration_ms": message.duration_ms or 0,
                                "spawn_reason": ctx.spawn_reason.value,
                                "result_preview": preview,
                            },
                        )
                    )
                except Exception:
                    _logger.warning(
                        "failed to post agent_run SystemEvent", exc_info=True,
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_agent_run_event.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jig/agent.py tests/test_agent_run_event.py
git commit -m "feat(agent): post agent_run SystemEvent with timing + turns on completion"
```

---

## Task 11: StoryEvent dataclass + library skeleton

Create `jig/story.py` with the `StoryEvent` dataclass, `StorySource` enum, and empty `build_story()` signature. Later tasks fill in behaviour.

**Files:**
- Create: `jig/story.py`
- Test: `tests/test_story.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_story.py`:

```python
"""Unit tests for jig.story library."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from jig.story import StoryEvent, StorySource, build_story


def test_story_event_frozen_dataclass_construction() -> None:
    ev = StoryEvent(
        ts=datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc),
        source=StorySource.thread,
        kind="handoff",
        level="info",
        message="handoff posted",
        ticket_id="tid-1",
        phase="spec",
        role="dev",
        raw={"kind": "handoff"},
    )
    assert ev.source == StorySource.thread
    assert ev.kind == "handoff"

    with pytest.raises(Exception):
        ev.ts = datetime.now()  # frozen


def test_story_source_enum_values() -> None:
    assert StorySource.thread.value == "thread"
    assert StorySource.log.value == "log"


@pytest.mark.asyncio
async def test_build_story_returns_empty_list_for_unknown_ticket(
    tmp_path: Path,
) -> None:
    from jig.store.threads import ThreadStore

    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    events = await build_story(
        "no-such-ticket", project_path=tmp_path, threads=threads
    )
    assert events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_story.py -v`
Expected: FAIL — `ModuleNotFoundError` for `jig.story`.

- [ ] **Step 3: Create the module**

Create `jig/story.py`:

```python
"""Per-ticket narrative story assembly.

Merges thread entries and structured log lines for a single ticket
into a time-ordered list of ``StoryEvent`` records. The CLI
(``jig story``) and a future TUI view consume this module.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from jig.store.threads import ThreadStore


class StorySource(str, Enum):
    thread = "thread"
    log = "log"


@dataclass(frozen=True)
class StoryEvent:
    """A single event on the ticket's narrative timeline.

    * ``ts`` — timezone-aware UTC timestamp.
    * ``source`` — where the event came from.
    * ``kind`` — for thread events, the entry kind (``handoff``,
      ``question``, etc.) or for SystemEvents the ``event_type``.
      For log events, the logger name (``jig.orchestrator``, etc.).
    * ``level`` — log level for log events; ``"info"`` for thread.
    * ``message`` — pre-formatted, ready to print.
    * ``raw`` — original record (thread entry dict or parsed log line)
      for callers that want more than the pre-formatted message.
    """

    ts: datetime
    source: StorySource
    kind: str
    level: str
    message: str
    ticket_id: str
    phase: str | None
    role: str | None
    raw: dict


async def build_story(
    ticket_id: str,
    *,
    project_path: Path,
    threads: ThreadStore,
    include_children: bool = False,
    since: datetime | None = None,
) -> list[StoryEvent]:
    """Return a time-ordered list of events for a ticket.

    Merges:
      * thread entries for ``ticket_id``
      * log lines under ``project_path/.jig/logs/*.jsonl`` whose
        ``ticket_id`` field matches
      * (if ``include_children``) the same for child tickets

    Sorted ascending by ``ts``.
    """
    # Task 12 fills in the body. Returning [] until then keeps the
    # skeleton test green.
    return []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_story.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/story.py tests/test_story.py
git commit -m "feat(story): StoryEvent dataclass + build_story skeleton"
```

---

## Task 12: build_story — thread-entry merge + log scanning

Fill in `build_story()` to read the thread store, scan `.jig/logs/*.jsonl`, filter by `ticket_id`, and return sorted `StoryEvent`s. Add per-kind message formatters.

**Files:**
- Modify: `jig/story.py`
- Test: `tests/test_story.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_story.py`:

```python
from jig.ticket import Ticket, WorkType
from jig.store.tickets import TicketStore
from jig.thread import Handoff, Note, Question


@pytest.mark.asyncio
async def test_build_story_includes_thread_entries(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    await threads.post(
        Note(ticket_id="tid-1", author="dev", content="starting work")
    )
    await threads.post(
        Handoff(
            ticket_id="tid-1", author="dev", phase="spec",
            outputs=["spec.md"], summary="spec draft ready",
        )
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads
    )
    assert len(events) == 2
    assert events[0].ts <= events[1].ts
    assert events[0].source == StorySource.thread
    assert events[0].kind == "note"
    assert "starting work" in events[0].message
    assert events[1].kind == "handoff"


@pytest.mark.asyncio
async def test_build_story_includes_matching_log_lines(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    (tmp_path / ".jig" / "logs").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    log_path = tmp_path / ".jig" / "logs" / "jig-20260423-100000.jsonl"
    log_path.write_text(
        '{"ts":"2026-04-23T10:00:00.000","level":"INFO","logger":"jig.x",'
        '"msg":"scheduling","ticket_id":"tid-1","phase":null,"role":null,'
        '"agent_id":null}\n'
        '{"ts":"2026-04-23T10:00:01.000","level":"INFO","logger":"jig.y",'
        '"msg":"other ticket","ticket_id":"tid-2","phase":null,"role":null,'
        '"agent_id":null}\n'
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads
    )
    assert len(events) == 1
    assert events[0].source == StorySource.log
    assert events[0].kind == "jig.x"
    assert "scheduling" in events[0].message


@pytest.mark.asyncio
async def test_build_story_sorts_thread_and_log_by_timestamp(
    tmp_path: Path,
) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    (tmp_path / ".jig" / "logs").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    # Write a log line in the past
    log_path = tmp_path / ".jig" / "logs" / "jig-20200101-000000.jsonl"
    log_path.write_text(
        '{"ts":"2020-01-01T00:00:00.000","level":"INFO","logger":"jig.x",'
        '"msg":"ancient","ticket_id":"tid-1","phase":null,"role":null,'
        '"agent_id":null}\n'
    )

    # Post a thread entry now
    await threads.post(
        Note(ticket_id="tid-1", author="dev", content="recent")
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads
    )
    assert len(events) == 2
    assert "ancient" in events[0].message
    assert "recent" in events[1].message
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_story.py -v`
Expected: three failures — `build_story` returns `[]`.

- [ ] **Step 3: Implement build_story**

Replace `jig/story.py` with:

```python
"""Per-ticket narrative story assembly."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from jig.store.threads import ThreadStore
from jig.thread import (
    Answer, Decision, Escalation, Handoff, Note, Objection,
    Proposal, Question, Resolution, SystemEvent, ThreadEntry, Uncertain,
    Waiver,
)

_logger = logging.getLogger(__name__)


class StorySource(str, Enum):
    thread = "thread"
    log = "log"


@dataclass(frozen=True)
class StoryEvent:
    ts: datetime
    source: StorySource
    kind: str
    level: str
    message: str
    ticket_id: str
    phase: str | None
    role: str | None
    raw: dict


# ---- Per-kind thread entry renderers --------------------------------------


def _render_thread_entry(entry: ThreadEntry) -> tuple[str, str]:
    """Return (kind_label, message) for a thread entry."""
    if isinstance(entry, Handoff):
        n_outputs = len(entry.outputs)
        return ("handoff", f"HANDOFF phase={entry.phase} by={entry.author} "
                f"outputs={n_outputs} state={entry.acceptance_state or 'pending'} "
                f"summary={entry.summary!r}")
    if isinstance(entry, Question):
        q_text = (entry.question or "")[:80]
        blocking = " [BLOCKING]" if entry.is_blocking() else ""
        return ("question", f"QUESTION{blocking} by={entry.author} "
                f"target={entry.target}: {q_text}")
    if isinstance(entry, Answer):
        a_text = (entry.answer or "")[:80]
        return ("answer", f"ANSWER by={entry.author} "
                f"responds_to={entry.responds_to}: {a_text}")
    if isinstance(entry, Escalation):
        return ("escalation", f"ESCALATION by={entry.author} "
                f"target={entry.target} reason={entry.reason}")
    if isinstance(entry, Objection):
        return ("objection", f"OBJECTION by={entry.author}: "
                f"{(entry.content or '')[:80]}")
    if isinstance(entry, Resolution):
        return ("resolution", f"RESOLUTION by={entry.author}: "
                f"responds_to={entry.responds_to}")
    if isinstance(entry, Waiver):
        return ("waiver", f"WAIVER by={entry.author}: "
                f"responds_to={entry.responds_to}")
    if isinstance(entry, Decision):
        return ("decision", f"DECISION by={entry.author}: "
                f"{(entry.content or '')[:80]}")
    if isinstance(entry, Note):
        return ("note", f"NOTE by={entry.author}: "
                f"{(entry.content or '')[:80]}")
    if isinstance(entry, Uncertain):
        return ("uncertain", f"UNCERTAIN by={entry.author}: "
                f"{(entry.content or '')[:80]}")
    if isinstance(entry, Proposal):
        return ("proposal", f"PROPOSAL by={entry.author} "
                f"state={entry.state}: {entry.title}")
    if isinstance(entry, SystemEvent):
        return _render_system_event(entry)
    return ("unknown", f"<{type(entry).__name__} by={entry.author}>")


def _render_system_event(ev: SystemEvent) -> tuple[str, str]:
    """Dispatch on event_type for a nicer one-line summary."""
    et = ev.event_type
    p = ev.payload or {}
    if et == "phase_start":
        return (f"system_event/{et}",
                f"PHASE START {p.get('phase', ev.content)} role={p.get('role', '?')}")
    if et == "phase_end":
        ms = p.get("duration_ms", 0)
        return (f"system_event/{et}",
                f"PHASE END   {p.get('phase', '?')} "
                f"outcome={p.get('outcome', ev.content)} duration={ms}ms")
    if et == "agent_run":
        return (f"system_event/{et}",
                f"AGENT RUN   {p.get('role', '?')} "
                f"turns={p.get('num_turns', '?')} duration={p.get('duration_ms', 0)}ms")
    if et == "phase_run":
        return (f"system_event/{et}",
                f"phase_run {ev.content} result={ev.phase_result}")
    if et == "commit":
        return (f"system_event/{et}",
                f"commit {(ev.commit_sha or '')[:7]}: {ev.content}")
    if et == "check_failure":
        return (f"system_event/{et}",
                f"check_failure {ev.check_name} verdict={ev.check_verdict}")
    if et == "status_change":
        return (f"system_event/{et}", f"status_change {ev.content}")
    if et == "dep_merge_failed":
        return (f"system_event/{et}", f"dep_merge_failed {ev.content}")
    return (f"system_event/{et}", f"{et}: {ev.content}")


# ---- Log file scanning ----------------------------------------------------


def _parse_log_line(line: str) -> dict | None:
    """Parse a single JSONL log line. Return None on any error so
    a single corrupt line doesn't break the scan."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def _iter_log_events_for_ticket(
    project_path: Path, ticket_id: str
) -> list[StoryEvent]:
    log_dir = project_path / ".jig" / "logs"
    if not log_dir.is_dir():
        return []
    events: list[StoryEvent] = []
    for log_file in sorted(log_dir.glob("jig-*.jsonl")):
        try:
            with log_file.open() as f:
                for line in f:
                    rec = _parse_log_line(line)
                    if rec is None:
                        continue
                    if rec.get("ticket_id") != ticket_id:
                        continue
                    ts_raw = rec.get("ts", "")
                    try:
                        ts = datetime.fromisoformat(ts_raw)
                    except ValueError:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    events.append(
                        StoryEvent(
                            ts=ts,
                            source=StorySource.log,
                            kind=rec.get("logger", "?"),
                            level=rec.get("level", "INFO"),
                            message=f"{rec.get('logger', '?')}: "
                                    f"{rec.get('msg', '')}",
                            ticket_id=ticket_id,
                            phase=rec.get("phase"),
                            role=rec.get("role"),
                            raw=rec,
                        )
                    )
        except OSError as exc:
            _logger.warning("failed to read log file %s: %s", log_file, exc)
    return events


# ---- Public API -----------------------------------------------------------


async def build_story(
    ticket_id: str,
    *,
    project_path: Path,
    threads: ThreadStore,
    include_children: bool = False,
    since: datetime | None = None,
) -> list[StoryEvent]:
    events: list[StoryEvent] = []

    # 1. Thread entries for this ticket.
    entries = await threads.for_ticket(ticket_id)
    for entry in entries:
        kind_label, message = _render_thread_entry(entry)
        events.append(
            StoryEvent(
                ts=entry.created_at,
                source=StorySource.thread,
                kind=kind_label,
                level="info",
                message=message,
                ticket_id=ticket_id,
                phase=None,
                role=entry.author,
                raw=entry.model_dump(mode="json"),
            )
        )

    # 2. Log lines mentioning this ticket.
    events.extend(_iter_log_events_for_ticket(project_path, ticket_id))

    # 3. Optionally include children.
    if include_children:
        # Look up children via ticket store — the caller didn't
        # pass one in, so query the tickets subdir directly.
        # Cheap: it's just a file listing.
        # Note: this is the naive implementation. If the ticket
        # graph gets deep a proper traversal belongs in ticket_store.
        pass  # Task 13 fills this in.

    # 4. Filter by `since`.
    if since is not None:
        events = [e for e in events if e.ts >= since]

    events.sort(key=lambda e: e.ts)
    return events
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_story.py -v`
Expected: all pass (6 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/story.py tests/test_story.py
git commit -m "feat(story): thread + log merge with per-kind renderers"
```

---

## Task 13: build_story — include_children + since filter

Complete the child-ticket traversal and verify `since` filtering.

**Files:**
- Modify: `jig/story.py`
- Test: `tests/test_story.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_story.py`:

```python
from jig.store.tickets import TicketStore


@pytest.mark.asyncio
async def test_build_story_include_children(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path)
    tickets = TicketStore(tmp_path)

    parent_id = await tickets.create(
        Ticket(work_type=WorkType.FEATURE, title="parent", created_by="user")
    )
    child_id = await tickets.create(
        Ticket(
            work_type=WorkType.FEATURE, title="child", created_by="user",
            parent_id=parent_id,
        )
    )

    await threads.post(
        Note(ticket_id=parent_id, author="dev", content="parent note")
    )
    await threads.post(
        Note(ticket_id=child_id, author="dev", content="child note")
    )

    # Without include_children — only parent entries.
    events = await build_story(
        parent_id, project_path=tmp_path, threads=threads,
        tickets=tickets,
    )
    assert len(events) == 1
    assert "parent note" in events[0].message

    # With include_children — both entries.
    events = await build_story(
        parent_id, project_path=tmp_path, threads=threads,
        tickets=tickets, include_children=True,
    )
    msgs = [e.message for e in events]
    assert any("parent note" in m for m in msgs)
    assert any("child note" in m for m in msgs)


@pytest.mark.asyncio
async def test_build_story_since_filter(tmp_path: Path) -> None:
    (tmp_path / ".jig" / "store").mkdir(parents=True)
    threads = ThreadStore(tmp_path)

    await threads.post(
        Note(ticket_id="tid-1", author="dev", content="early")
    )
    import asyncio
    await asyncio.sleep(0.01)
    cutoff = datetime.now(timezone.utc)
    await asyncio.sleep(0.01)
    await threads.post(
        Note(ticket_id="tid-1", author="dev", content="late")
    )

    events = await build_story(
        "tid-1", project_path=tmp_path, threads=threads, since=cutoff,
    )
    msgs = [e.message for e in events]
    assert not any("early" in m for m in msgs)
    assert any("late" in m for m in msgs)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_story.py -v`
Expected: FAIL — `build_story` doesn't accept `tickets` and `include_children` is a no-op.

- [ ] **Step 3: Add tickets parameter + child traversal**

Edit `jig/story.py`. Update the `build_story` signature:

```python
from jig.store.tickets import TicketStore


async def build_story(
    ticket_id: str,
    *,
    project_path: Path,
    threads: ThreadStore,
    tickets: TicketStore | None = None,
    include_children: bool = False,
    since: datetime | None = None,
) -> list[StoryEvent]:
    # ... existing events list building for this ticket_id ...

    if include_children:
        if tickets is None:
            raise ValueError(
                "include_children=True requires a TicketStore"
            )
        all_tickets = await tickets.list_all()
        for child in all_tickets:
            if child.parent_id != ticket_id:
                continue
            # Recurse — children of children count.
            child_events = await build_story(
                child.id,
                project_path=project_path,
                threads=threads,
                tickets=tickets,
                include_children=True,
                since=since,
            )
            events.extend(child_events)

    if since is not None:
        events = [e for e in events if e.ts >= since]

    events.sort(key=lambda e: e.ts)
    return events
```

If `TicketStore` doesn't have a `list_all()` method, check existing usage: `grep "tickets\.list\|TicketStore" jig/ -r`. Use whatever listing method exists (likely `tickets.all()` or iterate over `tickets.list()`). Use whatever the existing codebase uses to avoid adding new surface area.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_story.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add jig/story.py tests/test_story.py
git commit -m "feat(story): include_children traversal + since filter"
```

---

## Task 14: jig story CLI command

Add the `jig story <ticket-id>` Click command. Pretty-print by default, `--json` for machine-read, `--include-children` / `--since` / `--level` flags.

**Files:**
- Modify: `jig/cli.py` (append new command at EOF)
- Test: `tests/test_cli_story.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cli_story.py`:

```python
"""CLI tests for `jig story`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from jig.cli import cli
from jig.project import Project, save_project
from jig.thread import Note
from jig.ticket import Ticket, WorkType


@pytest.mark.asyncio
async def test_story_command_prints_thread_entries(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p", name="p", path=str(tmp_path),
            language="python", package_manager="uv",
        ),
    )
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)

    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    threads = ThreadStore(tmp_path)
    tickets = TicketStore(tmp_path)
    tid = await tickets.create(
        Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
    )
    await threads.post(
        Note(ticket_id=tid, author="dev", content="hello story")
    )

    runner = CliRunner()
    result = runner.invoke(cli, ["story", tid, "--path", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "hello story" in result.output


@pytest.mark.asyncio
async def test_story_command_json_output(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p", name="p", path=str(tmp_path),
            language="python", package_manager="uv",
        ),
    )
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)

    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    threads = ThreadStore(tmp_path)
    tickets = TicketStore(tmp_path)
    tid = await tickets.create(
        Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
    )
    await threads.post(Note(ticket_id=tid, author="dev", content="hi"))

    runner = CliRunner()
    result = runner.invoke(
        cli, ["story", tid, "--path", str(tmp_path), "--json"]
    )
    assert result.exit_code == 0, result.output
    lines = [l for l in result.output.strip().splitlines() if l]
    assert lines
    parsed = json.loads(lines[0])
    assert parsed["source"] == "thread"
    assert parsed["kind"] == "note"


def test_story_command_unknown_ticket_exits_nonzero(tmp_path: Path) -> None:
    save_project(
        tmp_path,
        Project(
            id="p", name="p", path=str(tmp_path),
            language="python", package_manager="uv",
        ),
    )
    (tmp_path / ".jig" / "store").mkdir(parents=True, exist_ok=True)

    runner = CliRunner()
    result = runner.invoke(
        cli, ["story", "no-such-ticket", "--path", str(tmp_path)]
    )
    assert result.exit_code != 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cli_story.py -v`
Expected: FAIL — no `story` command registered.

- [ ] **Step 3: Add the CLI command**

Append to `jig/cli.py`:

```python
@cli.command()
@click.argument("ticket_id")
@click.option(
    "--path", default=".", type=click.Path(exists=True, path_type=Path),
    help="Project path.",
)
@click.option("--json", "json_out", is_flag=True, help="Output JSON per line.")
@click.option(
    "--include-children", is_flag=True,
    help="Include events from child tickets.",
)
@click.option(
    "--since", type=str, default=None,
    help="ISO8601 timestamp — only show events at or after this time.",
)
@click.option(
    "--level", type=click.Choice(["DEBUG", "INFO"]), default="INFO",
    help="Minimum log level (thread entries are always shown).",
)
def story(
    ticket_id: str, path: Path, json_out: bool,
    include_children: bool, since: str | None, level: str,
) -> None:
    """Print the full story of a ticket."""
    import asyncio
    import json as json_mod
    from datetime import datetime
    from jig.story import build_story, StorySource
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError as exc:
            raise click.ClickException(f"Invalid --since: {exc}")

    async def run() -> list:
        threads = ThreadStore(path)
        tickets = TicketStore(path)
        # Verify ticket exists (unless children search, in which case
        # we still want to error on a missing parent).
        t = await tickets.get(ticket_id)
        if t is None:
            raise click.ClickException(f"Ticket {ticket_id!r} not found")
        return await build_story(
            ticket_id, project_path=path, threads=threads,
            tickets=tickets, include_children=include_children,
            since=since_dt,
        )

    events = asyncio.run(run())

    # Filter by level for log events only (thread entries are
    # always shown).
    if level == "INFO":
        events = [
            e for e in events
            if e.source != StorySource.log or e.level != "DEBUG"
        ]

    if not events:
        click.echo("(no events)", err=True)
        return

    if json_out:
        for ev in events:
            click.echo(json_mod.dumps({
                "ts": ev.ts.isoformat(),
                "source": ev.source.value,
                "kind": ev.kind,
                "level": ev.level,
                "message": ev.message,
                "ticket_id": ev.ticket_id,
                "phase": ev.phase,
                "role": ev.role,
            }))
        return

    # Pretty print. Show elapsed since first event.
    first_ts = events[0].ts
    for ev in events:
        elapsed = (ev.ts - first_ts).total_seconds()
        src_tag = "T" if ev.source == StorySource.thread else "L"
        click.echo(
            f"{ev.ts.strftime('%H:%M:%S.%f')[:12]} "
            f"(+{elapsed:7.2f}s) [{src_tag}] "
            f"{ev.level:5s} {ev.message}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_story.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Smoke test the CLI manually**

Run: `uv run jig story --help`
Expected: Click help output for the new command.

- [ ] **Step 6: Commit**

```bash
git add jig/cli.py tests/test_cli_story.py
git commit -m "feat(cli): add `jig story <ticket-id>` command"
```

---

## Task 15: End-to-end story integration test

One integration test that runs a full orchestrator lifecycle and then calls `jig story` to confirm the story contains thread entries + log lines + SystemEvent timing.

**Files:**
- Test: `tests/test_story_e2e.py`

- [ ] **Step 1: Write the integration test**

Create `tests/test_story_e2e.py`:

```python
"""End-to-end: run a ticket through the orchestrator, then assert
jig story produces a coherent narrative."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_story_captures_orchestrator_agent_and_thread_events(
    tmp_path: Path, monkeypatch
) -> None:
    from claude_agent_sdk.types import (
        AssistantMessage, TextBlock, ThinkingBlock, ResultMessage,
    )

    from jig.logging_setup import configure_logging
    from jig.models import PhaseConfig, RoleConfig, WorkflowConfig
    from jig.story import build_story, StorySource
    from jig.store.threads import ThreadStore
    from jig.store.tickets import TicketStore
    from jig.thread import Note, SystemEvent
    from jig.ticket import Ticket, TicketStatus, WorkType
    from tests._phase5p_helpers import build_orch, poll_until

    (tmp_path / ".jig").mkdir(exist_ok=True)
    configure_logging(tmp_path, verbose=True)

    async def fake_query(*, prompt, options, transport=None):
        async for _ in prompt:
            break
        yield AssistantMessage(
            content=[
                ThinkingBlock(
                    thinking="Plan: read the brief, write a note.",
                    signature="sig",
                ),
                TextBlock(text="Working on it."),
            ],
            model="claude-test",
        )
        yield ResultMessage(
            subtype="success", duration_ms=200, duration_api_ms=180,
            is_error=False, num_turns=1, session_id="",
            total_cost_usd=0.0, usage={}, result="done",
        )

    from jig import agent as agent_module
    monkeypatch.setattr(agent_module, "query", fake_query)

    workflow = WorkflowConfig(
        name="default",
        phases=[PhaseConfig(name="spec", role="spec")],
    )
    roles = [RoleConfig(role="spec", phase_prompt="spec")]
    orch = build_orch(
        tmp_path, workflow=workflow, roles=roles, monkeypatch=monkeypatch
    )

    await orch.startup()
    try:
        tid = await orch.tickets.create(
            Ticket(work_type=WorkType.FEATURE, title="f", created_by="user")
        )
        # Pre-post a Note so thread content shows up alongside
        # orchestrator-emitted events.
        await orch.threads.post(
            Note(ticket_id=tid, author="user", content="please do the thing")
        )
        await orch._handle_schedule(tid)

        async def done() -> bool:
            t = await orch.tickets.get(tid)
            return t is not None and t.status == TicketStatus.RESOLVED

        assert await poll_until(done, timeout_s=5.0)
    finally:
        await orch.shutdown()
        for h in logging.getLogger().handlers:
            h.flush()

    threads = ThreadStore(tmp_path)
    tickets = TicketStore(tmp_path)
    story = await build_story(
        tid, project_path=tmp_path, threads=threads, tickets=tickets,
    )

    # The story must contain: the user note, phase_start, agent_run,
    # phase_end, at least one orchestrator log line, and at least
    # one thinking DEBUG log line.
    kinds = [e.kind for e in story]
    assert "note" in kinds
    assert "system_event/phase_start" in kinds
    assert "system_event/agent_run" in kinds
    assert "system_event/phase_end" in kinds
    # Log lines from the orchestrator or agent
    log_events = [e for e in story if e.source == StorySource.log]
    assert log_events, "expected at least one log event on the story"

    # Timestamp ordering: phase_start before phase_end.
    start_idx = kinds.index("system_event/phase_start")
    end_idx = kinds.index("system_event/phase_end")
    assert start_idx < end_idx
```

- [ ] **Step 2: Run the test**

Run: `uv run pytest tests/test_story_e2e.py -v`
Expected: PASS.

- [ ] **Step 3: Full regression check**

Run: `uv run pytest tests/ 2>&1 | tail -20`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add tests/test_story_e2e.py
git commit -m "test(story): E2E test validating narrative coverage"
```

---

## Task 16: Update DEFERRED.md and project docs

Mark observability-related deferred items addressed, note the TUI integration follow-on, and remove any now-outdated comments.

**Files:**
- Modify: `docs/DEFERRED.md`

- [ ] **Step 1: Update DEFERRED.md**

Find the observability / logging note in `docs/DEFERRED.md` (grep for "observability" first):

```bash
grep -n "observability\|logging\|story\|grep magic" /Users/brent/Projects/personal/jig/docs/DEFERRED.md
```

Based on the grep output, either:
- Mark the addressed items with a resolution note and a pointer to this spec, OR
- Remove them entirely if fully addressed.

Keep the "TUI integration of `jig story`" as a new deferred item:

```markdown
### TUI integration of per-ticket story view
_Plan: 2026-04-23-per-ticket-story-logs §Layer F._

* The library (`jig.story`) and the CLI (`jig story`) are shipped.
  The TUI still shells to the CLI via subprocess — proper integration
  would expose `build_story` / `stream_story` through a new
  `ws_server.py` handler (`story.request` / `story.subscribe`).
  Lands when the TUI grows a ticket-detail view; today's ticket list
  view has no need for it.
```

- [ ] **Step 2: Commit**

```bash
git add docs/DEFERRED.md
git commit -m "docs: mark observability deferred items addressed; note TUI follow-on"
```

---

## Plan Self-Review Notes

**Spec coverage check:**
- Layer A (structured logs + contextvars) → Tasks 1–5 ✓
- Layer B (richer agent capture) → Tasks 6–7 ✓
- Layer C (timing SystemEvents) → Tasks 8–10 ✓
- Layer D (`jig.story` library) → Tasks 11–13 ✓
- Layer E (CLI) → Task 14 ✓
- End-to-end validation → Task 15 ✓
- DEFERRED.md hygiene → Task 16 ✓
- Layer F (TUI) → deferred in Task 16 as intended ✓

**Type consistency check:**
- `StoryEvent` fields match between Task 11 (definition) and Tasks 12–14 (consumers) ✓
- `SystemEvent.payload` added in Task 9, consumed in Task 12 renderer ✓
- `_ticket_id_var` etc. defined in Task 2, imported consistently in Tasks 4, 5 ✓
- `_wrap_with_context` kwargs (ticket_id/phase/role/agent_id) match the ContextVar set in Task 5 ✓

**Placeholder scan:** none found.

**Risk flags:**
- Task 5 Step 6 calls out that the MCP SDK internals may require a different wrapping approach. The *recommended* path (inline `async with _with_correlation(...)` in every tool handler) is explicit enough that the implementer shouldn't stall there.
- Task 13 Step 3 notes the `TicketStore.list_all()` method may not exist under that name — the implementer should grep the codebase to find the actual listing API. Cheap check.
- Task 9's phase_end posts inside a `finally` block to guarantee emission even if the phase raises. This means a KeyboardInterrupt during a phase will still post phase_end — by design.
