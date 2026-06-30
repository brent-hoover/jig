"""Reviewer federation — the LLM-judgment side of Enforcement.

Bones exposes the full dispatch/selection surface at its new home (re-exporting
``jig.reviewers.dispatch`` in lockstep via its ``__all__``). MVP migrates the
federation here and runs it behind the ``Review`` contract; Final brings in the
full reviewer set.
"""

from __future__ import annotations

from jig.engines.enforcement.reviewers import dispatch as _dispatch
from jig.engines.enforcement.reviewers.dispatch import *  # noqa: F401,F403

__all__ = list(_dispatch.__all__)
