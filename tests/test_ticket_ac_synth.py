"""Unit tests for the AC-rendering / synthesis helpers.

Three production paths construct work-type tickets and must ensure
the description satisfies the Ticket model's AC-required invariant:

- ``Coordinator._build_ticket_from_epic`` →
  ``jig.coordinator._render_description_with_ac`` (renders explicit
  ``Epic.acceptance_criteria`` bullets — no synthesis fallback).
- ``handle_arch_propose_spike`` →
  ``jig.sa_incremental_mcp._spike_description_with_ac`` (still
  transitional — synthesizes from the risk's free-form text since the
  SA workflow doesn't yet author explicit ACs for proposed spikes).
- ``handle_checkpoint_promote_deferred`` →
  ``jig.checkpoint_mcp._ensure_ac_section`` (still transitional —
  synthesizes from the deferred item's text when the operator doesn't
  supply an AC-bearing description on promotion).

Each helper must produce output that satisfies
``jig.ticket.has_acceptance_criteria_section`` even with edge-case
inputs (empty strings, prose without ACs, prose with an existing
AC). Integration tests catch most regressions at materialization
time, but the unit-level coverage is cheap and pins down the
contract.
"""

from __future__ import annotations

import pytest

from jig.checkpoint_mcp import _ensure_ac_section
from jig.coordinator import _render_description_with_ac
from jig.sa_incremental_mcp import _spike_description_with_ac
from jig.ticket import has_acceptance_criteria_section


class TestRenderDescriptionWithAc:
    """``jig.coordinator._render_description_with_ac``.

    Pure renderer — takes the epic's intent prose and a non-empty
    list of AC bullets (the Epic schema requires ``min_length=1``)
    and produces a description with the bullets in the AC section.
    No fallback logic needed; if a caller passes garbage the Epic
    schema would have already rejected it upstream.
    """

    def test_renders_single_bullet(self) -> None:
        out = _render_description_with_ac(
            problem="Catalog ingest pipeline does X.",
            acceptance_criteria=["Rows appear in the products collection."],
        )
        assert has_acceptance_criteria_section(out)
        assert "Catalog ingest pipeline does X." in out
        assert "Rows appear in the products collection." in out

    def test_renders_multiple_bullets_each_on_own_line(self) -> None:
        bullets = [
            "First bullet.",
            "Second bullet with more detail.",
            "Third bullet.",
        ]
        out = _render_description_with_ac(
            problem="Some problem.",
            acceptance_criteria=bullets,
        )
        assert has_acceptance_criteria_section(out)
        # Every bullet should appear in the output as its own line.
        for bullet in bullets:
            assert f"- {bullet}" in out
        ac_lines = [ln for ln in out.splitlines() if ln.startswith("- ")]
        assert len(ac_lines) == 3

    def test_empty_problem_still_renders_valid_description(self) -> None:
        out = _render_description_with_ac(
            problem="",
            acceptance_criteria=["The bullet."],
        )
        assert has_acceptance_criteria_section(out)
        # No intent prose preamble when problem is empty.
        assert out.startswith("## Acceptance criteria")

    def test_intent_prose_precedes_ac_section(self) -> None:
        out = _render_description_with_ac(
            problem="Why we are doing this.",
            acceptance_criteria=["bullet"],
        )
        intent_idx = out.index("Why we are doing this.")
        ac_idx = out.index("## Acceptance criteria")
        assert intent_idx < ac_idx


class TestSpikeDescriptionWithAc:
    """``jig.sa_incremental_mcp._spike_description_with_ac`` (transitional)."""

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
    """``jig.checkpoint_mcp._ensure_ac_section`` (transitional)."""

    def test_empty_description_synthesizes_full_ac(self) -> None:
        out = _ensure_ac_section("", fallback_bullet="Promote deferred item")
        assert has_acceptance_criteria_section(out)
        assert "Promote deferred item" in out

    def test_description_already_has_ac_passes_through(self) -> None:
        existing = (
            "Some prose.\n\n## Acceptance criteria\n- Specific behavior is verified.\n"
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
    """Cross-helper property: every output round-trips through
    ``Ticket(...)`` construction without raising ``ValidationError``.

    Catches the class of bug where a helper synthesizes prose that
    looks AC-shaped to the regex but actually fails the validator's
    bullet check (``\\S`` required after the marker)."""

    @pytest.mark.parametrize(
        "problem,bullets",
        [
            ("normal problem", ["bullet"]),
            ("", ["bullet"]),
            ("only problem", ["one", "two", "three"]),
            ("with multiple bullets", ["first", "second"]),
        ],
    )
    def test_coordinator_render_round_trips(
        self, problem: str, bullets: list[str]
    ) -> None:
        from jig.ticket import Ticket, WorkType

        description = _render_description_with_ac(
            problem=problem, acceptance_criteria=bullets
        )
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

        description = _spike_description_with_ac(summary=summary, risk_text=risk_text)
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
