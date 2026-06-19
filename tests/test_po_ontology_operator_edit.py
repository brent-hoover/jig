"""Project ontology operator-edit affordances (Track B Final).

Per ``docs/v2.0/multi-level-spec/design.md`` §"Project ontology":

- ``ontology_edit_term`` replaces an existing term's definition +
  examples; raises on missing terms (operators expect "edit" to fail
  loud on a typo).
- ``ontology_remove_term`` deletes a term; with ``replacement_term``
  redirects references in v2 artifacts in place; without it surfaces
  the orphans for operator follow-up.
- ``ontology_find_references`` scans suite briefs / contracts /
  comments / suites.yaml for the term with word-boundary semantics.
- Each tool emits an analytics event when an emitter is wired.

CLI surfaces (jig ontology list/show/edit/remove/find-references) are
covered by the test_cli_ontology.py file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import OntologyTermEdited, OntologyTermRemoved
from jig.analytics.store import AnalyticsStore
from jig.po_ontology_mcp import (
    EditResult,
    OntologyReference,
    RemoveResult,
    handle_ontology_add_term,
    handle_ontology_edit_term,
    handle_ontology_find_references,
    handle_ontology_remove_term,
)
from jig.spec_loader import load_ontology


@pytest.fixture
async def emitter(tmp_path: Path) -> EventEmitter:
    store = AnalyticsStore(tmp_path / "analytics.jsonl")
    await store.load()
    return EventEmitter(store, simulator_mode=True)


# ---- handle_ontology_edit_term -------------------------------------------


@pytest.mark.asyncio
async def test_edit_term_replaces_in_place(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="initial definition",
    )
    result = await handle_ontology_edit_term(
        project_path=tmp_path,
        term="blocker",
        definition="refined definition",
        examples=["CI is down", "waiting on Sarah"],
    )
    assert isinstance(result, EditResult)
    assert result.replaced is True
    ont = load_ontology(tmp_path)
    e = ont.by_term("blocker")
    assert e is not None
    assert e.definition == "refined definition"
    assert e.examples == ["CI is down", "waiting on Sarah"]


@pytest.mark.asyncio
async def test_edit_term_raises_when_missing(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="x",
    )
    with pytest.raises(KeyError, match="ghost"):
        await handle_ontology_edit_term(
            project_path=tmp_path,
            term="ghost",
            definition="x",
        )


@pytest.mark.asyncio
async def test_edit_term_raises_when_no_ontology_file(tmp_path: Path):
    with pytest.raises(KeyError):
        await handle_ontology_edit_term(
            project_path=tmp_path,
            term="blocker",
            definition="x",
        )


@pytest.mark.asyncio
async def test_edit_term_emits_analytics_event(tmp_path: Path, emitter: EventEmitter):
    await handle_ontology_add_term(
        project_path=tmp_path,
        term="blocker",
        definition="x",
    )
    await handle_ontology_edit_term(
        project_path=tmp_path,
        term="blocker",
        definition="refined",
        examples=["one", "two"],
        emitter=emitter,
    )
    await emitter.drain()
    events = await emitter._store.all()
    edited = [e for e in events if isinstance(e, OntologyTermEdited)]
    assert len(edited) == 1
    assert edited[0].term == "blocker"
    assert edited[0].examples_count == 2


@pytest.mark.asyncio
async def test_edit_term_rejects_blank_definition(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    with pytest.raises(ValueError, match="definition"):
        await handle_ontology_edit_term(
            project_path=tmp_path, term="blocker", definition="   "
        )


# ---- handle_ontology_find_references -------------------------------------


def _seed_artifact(project_path: Path, rel: str, text: str) -> None:
    p = project_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.mark.asyncio
async def test_find_references_returns_empty_when_no_artifacts(tmp_path: Path):
    refs = await handle_ontology_find_references(project_path=tmp_path, term="blocker")
    assert refs == []


@pytest.mark.asyncio
async def test_find_references_matches_artifacts(tmp_path: Path):
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites/catalog/brief.md",
        "# Catalog brief\n\nThe blocker for this suite is OAuth.\n",
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/modules/m1/contracts.yaml",
        "module: m1\nnotes: blocker scenarios are tested.\n",
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites.yaml",
        "suites: []\n# blocker tracking is suite-scoped\n",
    )
    refs = await handle_ontology_find_references(project_path=tmp_path, term="blocker")
    paths = {r.path for r in refs}
    assert ".jig/spec/suites/catalog/brief.md" in paths
    assert ".jig/spec/modules/m1/contracts.yaml" in paths


@pytest.mark.asyncio
async def test_find_references_word_boundary(tmp_path: Path):
    """Matches ``blocker`` but not ``roadblockers`` (substring)."""
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites/catalog/brief.md",
        "Line one mentions a blocker.\nLine two mentions roadblockers (a substring).\n",
    )
    refs = await handle_ontology_find_references(project_path=tmp_path, term="blocker")
    assert len(refs) == 1
    assert refs[0].line == 1


@pytest.mark.asyncio
async def test_find_references_skips_ontology_heading(tmp_path: Path):
    """The ontology's own ``### blocker`` heading is the definition, not
    a reference. Operators editing the ontology file shouldn't see it
    pollute the reference list."""
    _seed_artifact(
        tmp_path,
        ".jig/spec/ontology.md",
        "# Domain\n\n### blocker\nSomething preventing progress.\n",
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites/catalog/brief.md",
        "We hit a blocker on Tuesday.\n",
    )
    refs = await handle_ontology_find_references(project_path=tmp_path, term="blocker")
    paths = {r.path for r in refs}
    assert ".jig/spec/ontology.md" not in paths
    assert ".jig/spec/suites/catalog/brief.md" in paths


@pytest.mark.asyncio
async def test_find_references_rejects_blank_term(tmp_path: Path):
    with pytest.raises(ValueError):
        await handle_ontology_find_references(project_path=tmp_path, term="   ")


# ---- handle_ontology_remove_term -----------------------------------------


@pytest.mark.asyncio
async def test_remove_term_drops_entry(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    await handle_ontology_add_term(
        project_path=tmp_path, term="standup", definition="y"
    )
    result = await handle_ontology_remove_term(project_path=tmp_path, term="blocker")
    assert isinstance(result, RemoveResult)
    assert result.term == "blocker"
    assert result.replacement_term is None
    ont = load_ontology(tmp_path)
    assert [t.term for t in ont.terms] == ["standup"]


@pytest.mark.asyncio
async def test_remove_term_with_replacement_rewrites_artifacts(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    await handle_ontology_add_term(
        project_path=tmp_path, term="impediment", definition="y"
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites/catalog/brief.md",
        "Title: blocker handling\n\nThe blocker we hit on Tuesday.\n",
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/modules/m1/contracts.yaml",
        "notes: blocker scenarios are tested.\n",
    )
    result = await handle_ontology_remove_term(
        project_path=tmp_path,
        term="blocker",
        replacement_term="impediment",
    )
    assert result.replacement_term == "impediment"
    assert ".jig/spec/suites/catalog/brief.md" in result.rewritten
    assert ".jig/spec/modules/m1/contracts.yaml" in result.rewritten

    brief = (tmp_path / ".jig/spec/suites/catalog/brief.md").read_text()
    assert "impediment handling" in brief
    assert "The impediment we hit on Tuesday." in brief
    contracts = (tmp_path / ".jig/spec/modules/m1/contracts.yaml").read_text()
    assert "impediment" in contracts
    assert "blocker" not in contracts


@pytest.mark.asyncio
async def test_remove_term_orphans_refs_when_no_replacement(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites/catalog/brief.md",
        "The blocker we hit on Tuesday.\n",
    )
    result = await handle_ontology_remove_term(project_path=tmp_path, term="blocker")
    assert result.replacement_term is None
    assert len(result.orphaned) == 1
    assert isinstance(result.orphaned[0], OntologyReference)
    # The artifact body should be unchanged.
    body = (tmp_path / ".jig/spec/suites/catalog/brief.md").read_text()
    assert "blocker" in body


@pytest.mark.asyncio
async def test_remove_term_raises_when_missing(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    with pytest.raises(KeyError, match="ghost"):
        await handle_ontology_remove_term(project_path=tmp_path, term="ghost")


@pytest.mark.asyncio
async def test_remove_term_rejects_replacement_not_in_ontology(tmp_path: Path):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    with pytest.raises(KeyError, match="replacement"):
        await handle_ontology_remove_term(
            project_path=tmp_path,
            term="blocker",
            replacement_term="ghost",
        )


@pytest.mark.asyncio
async def test_remove_term_emits_analytics_event(tmp_path: Path, emitter: EventEmitter):
    await handle_ontology_add_term(
        project_path=tmp_path, term="blocker", definition="x"
    )
    _seed_artifact(
        tmp_path,
        ".jig/spec/suites/catalog/brief.md",
        "blocker reference\n",
    )
    await handle_ontology_remove_term(
        project_path=tmp_path,
        term="blocker",
        emitter=emitter,
    )
    await emitter.drain()
    events = await emitter._store.all()
    removed = [e for e in events if isinstance(e, OntologyTermRemoved)]
    assert len(removed) == 1
    assert removed[0].term == "blocker"
    assert removed[0].replacement_term is None
    assert removed[0].orphaned_reference_count == 1
