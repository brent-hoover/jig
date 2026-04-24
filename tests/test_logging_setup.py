"""Tests for ``jig.logging_setup`` — centralised logging config."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

from jig.logging_setup import (
    LogContextFilter,
    _agent_id_var,
    _phase_var,
    _role_var,
    _ticket_id_var,
    configure_logging,
)


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
    assert rec["ts"].endswith("+00:00")
