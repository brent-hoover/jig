"""Tests for v2 PO schemas — L0 Project + ProductNonGoal."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jig.schemas.po import Project, ProductNonGoal


def test_project_minimum_valid():
    p = Project(
        name="jig-search",
        pitch="hosted search SaaS for ecommerce",
        problem="merchants run blind without good search",
        audience="ecommerce ops staff at SMBs",
    )
    assert p.spec_version == 2
    assert p.non_goals == []
    assert p.generated_at is not None


def test_project_with_non_goals():
    p = Project(
        name="jig-search",
        pitch="x",
        problem="y",
        audience="z",
        non_goals=[
            ProductNonGoal(id="no-cms", text="We will not build a CMS",
                           rationale="out of scope for v2"),
            ProductNonGoal(id="no-recs", text="No recommendation engine"),
        ],
    )
    assert len(p.non_goals) == 2
    assert p.non_goals[0].id == "no-cms"
    assert p.non_goals[1].rationale is None


def test_project_rejects_extra_fields():
    with pytest.raises(ValidationError, match="extra"):
        Project(
            name="x",
            pitch="x",
            problem="x",
            audience="x",
            mystery_field="oops",
        )


def test_non_goal_rejects_empty_id():
    with pytest.raises(ValidationError):
        ProductNonGoal(id="", text="x")


def test_non_goal_rejects_empty_text():
    with pytest.raises(ValidationError):
        ProductNonGoal(id="ng-1", text="")
