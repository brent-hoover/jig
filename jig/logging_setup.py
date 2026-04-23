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
