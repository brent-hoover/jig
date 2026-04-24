"""Spec-generator agent scaffolding.

The spec-generator is a one-shot, non-conversational agent that
translates the brief into the structured spec and validates it. This
module defines the Gap payload model; the spawn wrapper is added in a
later task.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Gap(BaseModel):
    """A brief-validation finding reported by the spec-generator."""

    kind: Literal["missing", "contradiction", "ambiguity", "under_specified"]
    location: str
    description: str
    suggested_question: str | None = None
    severity: Literal["blocking", "advisory"]
