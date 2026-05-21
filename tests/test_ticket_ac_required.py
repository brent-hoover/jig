"""Tests for the Ticket model's Acceptance-Criteria-required validator.

Every work-type ticket (FEATURE, BUGFIX, REFACTOR, SPIKE, PERF, MIGRATION)
must have an Acceptance Criteria section in its description. System
tickets (BRIEF, ARCHITECTURE, PLANNING, CANONICALIZE, DOCS) are
exempt — they orchestrate work but do not represent units of work
that need AC enforcement.

Validation lives at the pydantic model layer so every Ticket(...)
construction is gated. Downstream consumers (reviewer-test-adequacy,
the workflow loop, etc.) can rely on the invariant without defensive
fallbacks.
"""

from __future__ import annotations

import pytest

from jig.ticket import Ticket, WorkType


_VALID_AC = "## Acceptance criteria\n- Some behaviour the ticket guarantees.\n"


class TestAcceptedHeadingFormats:
    @pytest.mark.parametrize(
        "heading",
        [
            "## Acceptance criteria",
            "### Acceptance criteria",
            "**Acceptance criteria:**",
            "**Acceptance criteria**",
            "## ACs",
            "### ACs",
            "**ACs:**",
            "## Done when",
            "### Done-when",
            "**Done when:**",
        ],
    )
    def test_recognized_heading_formats_pass(self, heading: str) -> None:
        description = f"{heading}\n- The thing is done when X happens.\n"
        ticket = Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )
        assert ticket.description == description

    @pytest.mark.parametrize(
        "heading",
        [
            "## ACCEPTANCE CRITERIA",
            "## acceptance criteria",
            "## Acceptance Criteria",
            "**Acceptance Criteria:**",
        ],
    )
    def test_case_insensitive(self, heading: str) -> None:
        description = f"{heading}\n- The behavior.\n"
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )

    def test_trailing_punctuation_tolerated(self) -> None:
        description = "## Acceptance criteria:\n- thing.\n"
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )


class TestWorkTypesRequireAC:
    @pytest.mark.parametrize(
        "work_type",
        [
            WorkType.FEATURE,
            WorkType.BUGFIX,
            WorkType.REFACTOR,
            WorkType.SPIKE,
            WorkType.PERF,
            WorkType.MIGRATION,
        ],
    )
    def test_work_type_without_ac_section_rejected(self, work_type: WorkType) -> None:
        with pytest.raises(ValueError, match="Acceptance"):
            Ticket(
                work_type=work_type,
                title="t",
                created_by="user",
                description="Just a description with no AC heading.",
            )

    @pytest.mark.parametrize(
        "work_type",
        [
            WorkType.FEATURE,
            WorkType.BUGFIX,
            WorkType.REFACTOR,
            WorkType.SPIKE,
            WorkType.PERF,
            WorkType.MIGRATION,
        ],
    )
    def test_work_type_with_empty_description_rejected(
        self, work_type: WorkType
    ) -> None:
        with pytest.raises(ValueError, match="Acceptance"):
            Ticket(
                work_type=work_type,
                title="t",
                created_by="user",
                description="",
            )


class TestSystemTypesExempt:
    @pytest.mark.parametrize(
        "work_type",
        [
            WorkType.BRIEF,
            WorkType.ARCHITECTURE,
            WorkType.PLANNING,
            WorkType.CANONICALIZE,
            WorkType.DOCS,
        ],
    )
    def test_system_work_type_without_ac_accepted(self, work_type: WorkType) -> None:
        # System tickets orchestrate other work and don't carry their
        # own AC. Empty description is fine.
        Ticket(work_type=work_type, title="t", created_by="cli", description="")

    def test_system_work_type_with_ac_still_accepted(self) -> None:
        # Having an AC section on a system ticket is unusual but
        # harmless; we only care that ACs aren't *required* for them.
        Ticket(
            work_type=WorkType.BRIEF,
            title="t",
            created_by="cli",
            description=_VALID_AC,
        )


class TestACSectionMustHaveBullets:
    def test_ac_section_without_bullets_rejected(self) -> None:
        description = "## Acceptance criteria\n\nNo bullets, just prose.\n"
        with pytest.raises(ValueError, match="Acceptance"):
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="user",
                description=description,
            )

    def test_ac_section_with_asterisk_bullets_accepted(self) -> None:
        description = "## Acceptance criteria\n* The behavior.\n"
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )

    def test_ac_section_with_numbered_bullets_accepted(self) -> None:
        description = "## Acceptance criteria\n1. The behavior.\n"
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )


class TestACSectionScoping:
    def test_bullets_in_other_section_dont_count(self) -> None:
        """Bullets under a non-AC heading don't satisfy the AC requirement."""
        description = (
            "## What to build\n"
            "- A thing.\n"
            "- Another thing.\n"
            "## Notes\n"
            "Nothing AC-shaped here.\n"
        )
        with pytest.raises(ValueError, match="Acceptance"):
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="user",
                description=description,
            )

    def test_ac_section_with_intermediate_blank_line_accepted(self) -> None:
        description = "## Acceptance criteria\n\n- The behaviour.\n"
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )

    def test_ac_followed_by_another_section_still_counts(self) -> None:
        description = "## Acceptance criteria\n- AC bullet.\n\n## Notes\nMore prose.\n"
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )

    def test_bold_emphasis_inside_ac_does_not_terminate_scan(self) -> None:
        """Regression: bold inline emphasis like ``**Important:** ...``
        followed by more text on the same line used to be mis-detected
        as a section-break "bold label heading", causing the scanner to
        flip out of AC mode before reaching the bullet. The narrowed
        ``_OTHER_HEADING_RE`` now requires the bold to consume the whole
        line (heading-style) so in-paragraph emphasis no longer kicks
        the scanner out."""
        description = (
            "## Acceptance criteria\n"
            "**Important:** the widget must load in under 200 ms.\n"
            "- Measured by the p95 load-time metric.\n"
        )
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="user",
            description=description,
        )

    def test_bold_heading_label_still_terminates_ac_section(self) -> None:
        """The narrowed regex must still match a proper bold-label
        heading on its own line — that's how the PO role's
        ``**Acceptance criteria:**`` syntax works, and a *subsequent*
        bold-label heading inside the description must still close out
        the current AC scope. Construct a doc where the AC heading
        comes first, has no bullets, then a second bold label heading
        appears — the validator should reject (no bullets in AC)."""
        with pytest.raises(ValueError, match="Acceptance"):
            Ticket(
                work_type=WorkType.FEATURE,
                title="t",
                created_by="user",
                description=(
                    "**Acceptance criteria:**\n"
                    "\n"
                    "**Notes:**\n"
                    "- This bullet is under Notes, not Acceptance.\n"
                ),
            )
