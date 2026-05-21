"""Unit tests for the three transitional AC-synthesis helpers.

The ``Ticket`` model requires a discoverable Acceptance Criteria
section in the description for work-type tickets (FEATURE / BUGFIX /
REFACTOR / SPIKE / PERF / MIGRATION). Three production paths
currently construct work-type tickets from upstream data sources that
don't carry explicit ACs:

- ``Coordinator._build_ticket_from_epic`` →
  ``jig.coordinator._synthesize_description_with_ac``
- ``handle_arch_propose_spike`` →
  ``jig.sa_incremental_mcp._spike_description_with_ac``
- ``handle_checkpoint_promote_deferred`` →
  ``jig.checkpoint_mcp._ensure_ac_section``

Each helper must produce output that satisfies
``jig.ticket.has_acceptance_criteria_section`` even when called with
edge-case inputs (empty strings, prose without ACs, prose with an
existing AC). The integration tests catch most regressions at
materialization time, but the unit-level coverage is cheap and pins
down the contract.

These tests will move with their respective production helpers when
the follow-up PR replaces the synthesis with explicit AC fields on
the upstream schemas.
"""

from __future__ import annotations

import pytest

from jig.checkpoint_mcp import _ensure_ac_section
from jig.coordinator import _synthesize_description_with_ac
from jig.sa_incremental_mcp import _spike_description_with_ac
from jig.ticket import has_acceptance_criteria_section


class TestSynthesizeDescriptionWithAc:
    """``jig.coordinator._synthesize_description_with_ac``."""

    def test_typical_problem_renders_ac_bullet(self) -> None:
        out = _synthesize_description_with_ac(
            problem="Catalog ingest pipeline does X.",
            epic_title="Catalog Ingest",
        )
        assert has_acceptance_criteria_section(out)
        assert "Catalog ingest pipeline does X." in out

    def test_empty_problem_falls_back_to_epic_title(self) -> None:
        out = _synthesize_description_with_ac(
            problem="",
            epic_title="Catalog Ingest",
        )
        assert has_acceptance_criteria_section(out)
        assert "Catalog Ingest" in out

    def test_both_empty_uses_safe_default_bullet(self) -> None:
        """Regression: previously produced ``"- \\n"`` which failed the
        bullet regex (requires ``\\S`` after the marker) and crashed
        ``Ticket(...)`` at materialization time."""
        out = _synthesize_description_with_ac(problem="", epic_title="")
        assert has_acceptance_criteria_section(out)
        # The fallback bullet text is non-empty.
        assert "Implemented as planned" in out

    def test_whitespace_only_inputs_use_safe_default(self) -> None:
        out = _synthesize_description_with_ac(
            problem="   \n   ", epic_title="\t\t"
        )
        assert has_acceptance_criteria_section(out)

    def test_multiline_problem_collapses_to_single_bullet(self) -> None:
        out = _synthesize_description_with_ac(
            problem="Line one.\nLine two.\nLine three.",
            epic_title="Multi",
        )
        assert has_acceptance_criteria_section(out)
        # The synthesized bullet is one line — multi-line problem prose
        # collapses to a single AC bullet.
        ac_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        assert len(ac_lines) == 1


class TestSpikeDescriptionWithAc:
    """``jig.sa_incremental_mcp._spike_description_with_ac``."""

    def test_typical_inputs_render_ac_bullet(self) -> None:
        out = _spike_description_with_ac(
            summary="Investigate vendor rate limits.",
            risk_text="Shopify enforces 4-RPS bursts that may break ingest.",
        )
        assert has_acceptance_criteria_section(out)
        assert "Investigate vendor rate limits." in out
        assert "Shopify enforces" in out

    def test_empty_summary_still_produces_valid_description(self) -> None:
        out = _spike_description_with_ac(
            summary="",
            risk_text="Network partition during peak.",
        )
        assert has_acceptance_criteria_section(out)

    def test_empty_risk_text_falls_back_to_safe_default(self) -> None:
        out = _spike_description_with_ac(
            summary="Some summary.",
            risk_text="",
        )
        assert has_acceptance_criteria_section(out)
        assert "the risk is resolved" in out

    def test_both_empty_still_produces_valid_description(self) -> None:
        out = _spike_description_with_ac(summary="", risk_text="")
        assert has_acceptance_criteria_section(out)


class TestEnsureAcSection:
    """``jig.checkpoint_mcp._ensure_ac_section``."""

    def test_empty_description_synthesizes_full_ac(self) -> None:
        out = _ensure_ac_section("", fallback_bullet="Promote deferred item")
        assert has_acceptance_criteria_section(out)
        assert "Promote deferred item" in out

    def test_description_already_has_ac_passes_through(self) -> None:
        existing = (
            "Some prose.\n\n"
            "## Acceptance criteria\n"
            "- Specific behavior is verified.\n"
        )
        out = _ensure_ac_section(existing, fallback_bullet="ignored")
        assert has_acceptance_criteria_section(out)
        # Original AC bullet preserved verbatim, fallback not appended.
        assert "Specific behavior is verified." in out
        assert "ignored" not in out

    def test_description_without_ac_gets_appended(self) -> None:
        out = _ensure_ac_section(
            "Plain prose, no AC heading.",
            fallback_bullet="Item resolved",
        )
        assert has_acceptance_criteria_section(out)
        assert "Plain prose, no AC heading." in out
        assert "Item resolved" in out

    def test_empty_fallback_bullet_uses_safe_default(self) -> None:
        out = _ensure_ac_section("", fallback_bullet="")
        assert has_acceptance_criteria_section(out)
        assert "Resolved as planned" in out

    def test_whitespace_only_fallback_bullet_uses_safe_default(self) -> None:
        out = _ensure_ac_section("", fallback_bullet="   \n\t  ")
        assert has_acceptance_criteria_section(out)


class TestOutputSatisfiesValidator:
    """Cross-helper property: every synth output round-trips through
    ``Ticket(...)`` construction without raising ``ValidationError``.

    Catches the class of bug where a helper synthesizes prose that
    looks AC-shaped to the regex but actually fails the validator's
    bullet check (``\\S`` required after the marker)."""

    @pytest.mark.parametrize(
        "problem,epic_title",
        [
            ("normal problem", "epic"),
            ("", ""),
            ("only problem", ""),
            ("", "only title"),
            ("   ", "   "),
        ],
    )
    def test_coordinator_synth_round_trips(
        self, problem: str, epic_title: str
    ) -> None:
        from jig.ticket import Ticket, WorkType

        description = _synthesize_description_with_ac(
            problem=problem, epic_title=epic_title
        )
        # Should not raise.
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="test",
            description=description,
        )

    @pytest.mark.parametrize(
        "summary,risk_text",
        [
            ("summary", "risk"),
            ("", ""),
            ("only summary", ""),
            ("", "only risk"),
        ],
    )
    def test_spike_synth_round_trips(self, summary: str, risk_text: str) -> None:
        from jig.ticket import Ticket, WorkType

        description = _spike_description_with_ac(
            summary=summary, risk_text=risk_text
        )
        Ticket(
            work_type=WorkType.SPIKE,
            title="t",
            created_by="test",
            description=description,
        )

    @pytest.mark.parametrize(
        "description,fallback",
        [
            ("", "fallback"),
            ("", ""),
            ("plain prose", "fallback"),
            ("## Acceptance criteria\n- existing\n", "ignored"),
        ],
    )
    def test_checkpoint_synth_round_trips(
        self, description: str, fallback: str
    ) -> None:
        from jig.ticket import Ticket, WorkType

        out = _ensure_ac_section(description, fallback_bullet=fallback)
        Ticket(
            work_type=WorkType.FEATURE,
            title="t",
            created_by="test",
            description=out,
        )
