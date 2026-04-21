"""Capability enforcement hook scripts (Phase 5 Task G).

This package ships three executable Python scripts — ``check-bash``,
``check-write``, ``check-path`` — that Claude Code invokes as PreToolUse
hooks when a spawned agent calls Bash, Write-family, or Read-family
tools respectively. ``_hooklib.py`` is a shared helper module the
scripts import.

The package has no runtime behaviour as a Python module; importing it
returns an empty namespace. The ``__init__.py`` exists so setuptools
packages the directory (``packages.find`` only collects directories
that contain an ``__init__.py``) and so :func:`hook_bin_dir` below can
resolve the on-disk location of the scripts regardless of whether jig
is installed as a wheel, editable, or run from a source checkout.
"""

from __future__ import annotations

from pathlib import Path


def hook_bin_dir() -> Path:
    """Return the filesystem directory containing the hook scripts.

    The directory this file lives in is always the bin dir — whether
    the package is installed into site-packages, a virtualenv, or run
    from a source checkout. ``sandbox.py`` bind-mounts the returned
    path to ``/jig/bin/`` inside the agent sandbox."""

    return Path(__file__).resolve().parent
