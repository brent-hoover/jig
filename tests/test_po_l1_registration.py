"""Verify role-scoped registration of L1 PO MCP tools (Track B MVP)."""
from __future__ import annotations

from pathlib import Path

from jig.persistence import load_role


# The L1 PO's MCP toolset per design.md §"L1 PO tools (new MCP tools)".
# Kept in this test so the role config and the MCP server registration
# stay aligned via a single source of truth — adding a tool to the
# role config without registering it here will fail loudly.
EXPECTED_L1_TOOLS = {
    "Read",
    "ask_question",
    "discovery_set_intro",
    "discovery_set_phase",
    "discovery_set_next_question",
    "discovery_add_journey",
    "discovery_add_capability",
    "discovery_stash_pending_capability",
    "discovery_clear_pending",
    "discovery_set_playback",
    "discovery_load_state",
    "discovery_finalize",
}


def test_l1_po_role_config_loads(tmp_path: Path):
    """The shipped l1_po.yaml is well-formed and grants the L1 toolset."""
    cfg = load_role(tmp_path, "l1_po")
    assert cfg.role == "po-l1"
    assert cfg.strict_tools is True
    # Every MCP tool the design enumerates is granted; no extras.
    assert set(cfg.allowed_tools) == EXPECTED_L1_TOOLS


def test_l1_po_role_excludes_other_phase_tools(tmp_path: Path):
    """Strict scoping: L1 must not see L0 / L3 / SA / v1-brief tooling."""
    cfg = load_role(tmp_path, "l1_po")
    for forbidden in (
        "l0_finalize",       # L0 already done by upstream phase
        "l3_finalize",       # downstream phase
        "sa_finalize",       # SA's job
        "plan_finalize",     # PM's job
        "brief_set_section", # v1 monolithic-brief tooling
        "po_finish_brief",   # v1 PO finish
        "spec_publish",      # spec-generator
        "spec_report_gaps",
        "arch_set_field",    # v1 SA tooling
    ):
        assert forbidden not in cfg.allowed_tools


def test_l1_po_role_phase_prompt_is_substantive(tmp_path: Path):
    """The phase prompt must carry the load-bearing 5-phase guidance.

    Spot-checks key terms so a future edit that accidentally drops the
    phase walkthrough fails this test rather than silently shipping a
    PO that won't follow the discipline.
    """
    cfg = load_role(tmp_path, "l1_po")
    prompt = cfg.phase_prompt
    # The 5 phases by name.
    for needle in (
        "Phase 1",
        "Phase 2",
        "Phase 3",
        "Phase 4",
        "Phase 5",
        "primary persona",
        "primary job",
        "Walk the journey",
        "failure modes",
        "play back",
    ):
        assert needle in prompt, f"missing prompt fragment: {needle!r}"
    # The "one question per turn" mechanic — non-negotiable per design.
    assert "One question per turn" in prompt
