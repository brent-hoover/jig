"""Project ontology MCP tool handlers + markdown round-trip (Track B6 MVP)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.po_ontology_mcp import (
    handle_ontology_add_term,
    handle_ontology_get_terms,
    handle_ontology_lookup,
    handle_ontology_stash_term,
    load_pending_terms,
    parse_ontology_md,
    render_ontology_md,
)
from jig.schemas.po import Ontology, OntologyTerm
from jig.spec_loader import (
    load_ontology,
    ontology_path,
)


# ---- markdown round-trip --------------------------------------------------


def test_render_minimal_ontology_with_no_terms():
    md = render_ontology_md(Ontology(), project_name="async-standup")
    assert md.startswith("# async-standup — Domain Vocabulary\n")
    # Header structure is always present so downstream readers can
    # ``parse_ontology_md`` without ``FileNotFoundError`` checks.
    assert "## Terms" in md


def test_render_one_term_with_examples():
    ont = Ontology(
        terms=[
            OntologyTerm(
                term="blocker",
                definition=("Something preventing a team member from making progress."),
                examples=[
                    "waiting on Sarah",
                    "CI is down",
                ],
            )
        ]
    )
    md = render_ontology_md(ont, project_name="async-standup")
    assert "### blocker" in md
    assert "Something preventing a team member" in md
    assert "**Examples:**" in md
    assert "- waiting on Sarah" in md
    assert "- CI is down" in md


def test_round_trip_one_term():
    ont = Ontology(
        terms=[
            OntologyTerm(
                term="blocker",
                definition="Something preventing a team member from making progress.",
                examples=["waiting on Sarah"],
            )
        ]
    )
    md = render_ontology_md(ont, project_name="x")
    parsed = parse_ontology_md(md)
    assert len(parsed.terms) == 1
    assert parsed.terms[0].term == "blocker"
    assert "preventing a team member" in parsed.terms[0].definition
    assert parsed.terms[0].examples == ["waiting on Sarah"]


def test_round_trip_multiple_terms_preserves_order():
    ont = Ontology(
        terms=[
            OntologyTerm(term="alpha", definition="first"),
            OntologyTerm(term="beta", definition="second"),
            OntologyTerm(term="gamma", definition="third"),
        ]
    )
    md = render_ontology_md(ont, project_name="x")
    parsed = parse_ontology_md(md)
    assert [t.term for t in parsed.terms] == ["alpha", "beta", "gamma"]


def test_parse_tolerates_definition_without_examples_section():
    md = (
        "# proj — Domain Vocabulary\n\n"
        "## Terms\n\n"
        "### blocker\n"
        "Something blocking progress.\n"
    )
    parsed = parse_ontology_md(md)
    assert parsed.terms[0].term == "blocker"
    assert parsed.terms[0].definition == "Something blocking progress."
    assert parsed.terms[0].examples == []


def test_parse_skips_heading_without_definition():
    """An in-flight operator edit (heading typed but no definition yet)
    shouldn't crash the parser — the rest of the file still parses."""
    md = (
        "# proj — Domain Vocabulary\n\n"
        "## Terms\n\n"
        "### empty-heading\n\n"  # No definition; should be skipped.
        "### blocker\n"
        "Something blocking progress.\n"
    )
    parsed = parse_ontology_md(md)
    assert [t.term for t in parsed.terms] == ["blocker"]


# ---- handle_ontology_stash_term -----------------------------------------


@pytest.mark.asyncio
async def test_stash_term_writes_pending_sidecar(tmp_path: Path):
    await handle_ontology_stash_term(
        project_path=tmp_path,
        term="blocker",
        context="j-merchant-tuesday",
    )
    pending = load_pending_terms(tmp_path)
    assert len(pending) == 1
    assert pending[0].term == "blocker"
    assert pending[0].context == "j-merchant-tuesday"


@pytest.mark.asyncio
async def test_stash_term_idempotent_updates_context(tmp_path: Path):
    """Re-stashing the same term replaces the recorded context."""
    await handle_ontology_stash_term(
        project_path=tmp_path,
        term="blocker",
        context="first context",
    )
    await handle_ontology_stash_term(
        project_path=tmp_path,
        term="BLOCKER",  # case-insensitive match
        context="second context",
    )
    pending = load_pending_terms(tmp_path)
    assert len(pending) == 1
    assert pending[0].context == "second context"


@pytest.mark.asyncio
async def test_stash_term_rejects_blank_term(tmp_path: Path):
    with pytest.raises(ValueError, match="term must not be empty"):
        await handle_ontology_stash_term(
            project_path=tmp_path,
            term="   ",
            context="x",
        )


@pytest.mark.asyncio
async def test_stash_term_rejects_blank_context(tmp_path: Path):
    with pytest.raises(ValueError, match="context must not be empty"):
        await handle_ontology_stash_term(
            project_path=tmp_path,
            term="blocker",
            context="   ",
        )


# ---- handle_ontology_add_term -------------------------------------------


@pytest.mark.asyncio
async def test_add_term_creates_ontology_md(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="Something preventing progress.",
        examples=["waiting on Sarah"],
    )
    md = ontology_path(tmp_path).read_text()
    assert "### blocker" in md
    assert "Something preventing progress." in md
    assert "- waiting on Sarah" in md


@pytest.mark.asyncio
async def test_add_term_replaces_existing_in_place(tmp_path: Path):
    """Re-adding a term updates the entry in place — first-mention
    reading order is preserved across edits."""
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="first definition",
    )
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="standup",
        definition="async daily ritual",
    )
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="BLOCKER",  # case-insensitive update
        definition="refined definition",
        examples=["waiting on Sarah"],
    )
    parsed = load_ontology(tmp_path)
    # Order preserved — blocker stays first because that's where it was
    # first mentioned.
    assert [t.term for t in parsed.terms] == ["blocker", "standup"]
    blocker = parsed.by_term("blocker")
    assert blocker is not None
    assert blocker.definition == "refined definition"
    assert blocker.examples == ["waiting on Sarah"]


@pytest.mark.asyncio
async def test_add_term_drops_blank_examples(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="x",
        examples=["valid", "  ", "", "also valid"],
    )
    parsed = load_ontology(tmp_path)
    assert parsed.terms[0].examples == ["valid", "also valid"]


@pytest.mark.asyncio
async def test_add_term_clears_matching_pending(tmp_path: Path):
    """Folding clear-from-pending: the LLM doesn't have to remember the
    explicit clear step after committing a confirmed term."""
    await handle_ontology_stash_term(
        project_path=tmp_path,
        term="blocker",
        context="j-merchant-tuesday",
    )
    await handle_ontology_stash_term(
        project_path=tmp_path,
        term="standup",
        context="j-merchant-tuesday",
    )
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="Something preventing progress.",
    )
    remaining = load_pending_terms(tmp_path)
    assert [t.term for t in remaining] == ["standup"]


@pytest.mark.asyncio
async def test_add_term_rejects_blank(tmp_path: Path):
    with pytest.raises(ValueError, match="term must not be empty"):
        await handle_ontology_add_term(
            project_path=tmp_path,
            term="",
            definition="x",
        )
    with pytest.raises(ValueError, match="definition must not be empty"):
        await handle_ontology_add_term(
            project_path=tmp_path,
            term="blocker",
            definition="   ",
        )


# ---- handle_ontology_get_terms / handle_ontology_lookup -----------------


@pytest.mark.asyncio
async def test_get_terms_returns_empty_when_absent(tmp_path: Path):
    out = await handle_ontology_get_terms(project_path=tmp_path)
    assert out == {"terms": []}


@pytest.mark.asyncio
async def test_get_terms_round_trips(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="Something preventing progress.",
        examples=["CI is down"],
    )
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="standup",
        definition="async daily ritual",
    )
    out = await handle_ontology_get_terms(project_path=tmp_path)
    assert [t["term"] for t in out["terms"]] == ["blocker", "standup"]
    assert out["terms"][0]["examples"] == ["CI is down"]


@pytest.mark.asyncio
async def test_lookup_returns_none_when_absent_file(tmp_path: Path):
    out = await handle_ontology_lookup(project_path=tmp_path, term="blocker")
    assert out is None


@pytest.mark.asyncio
async def test_lookup_returns_none_when_missing_term(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="standup",
        definition="x",
    )
    out = await handle_ontology_lookup(project_path=tmp_path, term="ghost")
    assert out is None


@pytest.mark.asyncio
async def test_lookup_case_insensitive(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="Blocker",
        definition="Something preventing progress.",
    )
    out = await handle_ontology_lookup(project_path=tmp_path, term="BLOCKER")
    assert out is not None
    assert out["term"] == "Blocker"
    assert out["definition"] == "Something preventing progress."


# ---- spec_loader integration --------------------------------------------


def test_load_ontology_raises_when_absent(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_ontology(tmp_path)


@pytest.mark.asyncio
async def test_load_ontology_round_trips_after_add(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="Something preventing progress.",
    )
    ont = load_ontology(tmp_path)
    assert ont.by_term("blocker") is not None


def test_ontology_path_helper(tmp_path: Path):
    p = ontology_path(tmp_path)
    assert p == tmp_path / ".jig" / "spec" / "ontology.md"
