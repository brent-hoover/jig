"""Helpers invoked by ``scripted`` check catalog entries.

Each module here is a self-contained subprocess: invoked from a
``command`` field in ``jig/defaults/checks.yaml`` (or its
project-local override) via ``python -m jig.check_helpers.<module>``.
Helpers exit non-zero to signal check failure.
"""
