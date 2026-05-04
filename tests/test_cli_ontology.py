"""CLI surfaces for ontology operator-edit affordances (Track B Final).

The MCP-tool tests live in test_po_ontology_operator_edit.py; this file
covers the ``jig ontology …`` group in jig/cli.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from click.testing import CliRunner

from jig.cli import cli
from jig.po_ontology_mcp import handle_ontology_add_term


def _runner() -> CliRunner:
    return CliRunner()


def _seed_term(project_path: Path, term: str, definition: str) -> None:
    asyncio.run(
        handle_ontology_add_term(
            project_path=project_path,
            term=term,
            definition=definition,
        )
    )


def test_ontology_list_empty(tmp_path: Path):
    r = _runner().invoke(cli, ["ontology", "list", "--path", str(tmp_path)])
    assert r.exit_code == 0
    assert "(no terms)" in r.output


def test_ontology_list_lists_added_terms(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "x")
    _seed_term(tmp_path, "standup", "y")
    r = _runner().invoke(cli, ["ontology", "list", "--path", str(tmp_path)])
    assert r.exit_code == 0
    assert "- blocker" in r.output
    assert "- standup" in r.output


def test_ontology_show_prints_definition(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "Something preventing progress.")
    r = _runner().invoke(
        cli, ["ontology", "show", "blocker", "--path", str(tmp_path)]
    )
    assert r.exit_code == 0
    assert "### blocker" in r.output
    assert "Something preventing progress." in r.output


def test_ontology_show_missing_term_errors(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "x")
    r = _runner().invoke(
        cli, ["ontology", "show", "ghost", "--path", str(tmp_path)]
    )
    assert r.exit_code != 0
    assert "ghost" in r.output


def test_ontology_edit_replaces(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "old")
    r = _runner().invoke(
        cli,
        [
            "ontology",
            "edit",
            "blocker",
            "--definition",
            "refined",
            "--example",
            "CI is down",
            "--path",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "updated term 'blocker'" in r.output


def test_ontology_edit_missing_term_errors(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "x")
    r = _runner().invoke(
        cli,
        [
            "ontology",
            "edit",
            "ghost",
            "--definition",
            "x",
            "--path",
            str(tmp_path),
        ],
    )
    assert r.exit_code != 0
    assert "ghost" in r.output


def test_ontology_remove_drops(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "x")
    r = _runner().invoke(
        cli, ["ontology", "remove", "blocker", "--path", str(tmp_path)]
    )
    assert r.exit_code == 0
    assert "removed term 'blocker'" in r.output


def test_ontology_remove_with_replacement_rewrites(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "x")
    _seed_term(tmp_path, "impediment", "y")
    artifact = tmp_path / ".jig/spec/suites/catalog/brief.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("The blocker we hit.\n")
    r = _runner().invoke(
        cli,
        [
            "ontology",
            "remove",
            "blocker",
            "--replace-with",
            "impediment",
            "--path",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, r.output
    assert "redirected references to 'impediment'" in r.output
    assert "rewrote .jig/spec/suites/catalog/brief.md" in r.output
    assert "impediment" in artifact.read_text()


def test_ontology_find_references_lists(tmp_path: Path):
    _seed_term(tmp_path, "blocker", "x")
    artifact = tmp_path / ".jig/spec/suites/catalog/brief.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("We hit a blocker on Tuesday.\n")
    r = _runner().invoke(
        cli,
        [
            "ontology",
            "find-references",
            "blocker",
            "--path",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0
    assert ".jig/spec/suites/catalog/brief.md:1" in r.output


def test_ontology_find_references_empty(tmp_path: Path):
    r = _runner().invoke(
        cli,
        [
            "ontology",
            "find-references",
            "ghost",
            "--path",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0
    assert "no references" in r.output
