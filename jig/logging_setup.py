"""Centralised logging configuration for the jig CLI daemon.

Owns handler construction, formatter choice, and the location of
the per-invocation log file. `jig.cli.start` calls
``configure_logging`` once at daemon startup.
"""

from __future__ import annotations

import contextvars
import json
import logging
from datetime import datetime, timezone
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


class JsonFormatter(logging.Formatter):
    """Renders each log record as one JSON object per line.

    Schema:
      {ts, level, logger, msg, ticket_id, phase, role, agent_id,
       exc?, extra?}
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
            "ts": datetime.fromtimestamp(
                record.created, tz=timezone.utc
            ).isoformat(timespec="milliseconds"),
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


def configure_logging(project_path: Path, *, verbose: bool = False) -> Path:
    """Configure root logger + console + file handlers.

    Returns the path to the log file so the caller can echo it.
    """
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
    # Clear any handlers from a prior configure call (tests run in
    # the same process).
    root.handlers.clear()
    root.addHandler(console)

    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("mcp").setLevel(logging.WARNING)

    log_dir = project_path / ".jig" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"jig-{datetime.now():%Y%m%d-%H%M%S}.jsonl"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(context_filter)
    root.addHandler(file_handler)
    return log_file
