"""Project ontology MCP tool handlers (Track B6 MVP).

Per ``docs/multi-level-spec/design.md`` §"Project ontology — capturing
the operator's domain vocabulary": every project has a
``.jig/spec/ontology.md`` capturing the operator's domain words as they
emerge during PO discovery. The L1 PO is the primary author; downstream
agents (SA, VD, PM, dev, reviewer) read the same file for terminology
consistency.

Four MCP tools:

- ``ontology_stash_term(term, context)`` — buffer a term that came up
  mid-conversation; full definition is captured later. Stashed terms
  live in a sidecar (``.jig/spec/ontology.pending.yaml``) so they
  survive interruption + resume.
- ``ontology_add_term(term, definition, examples?)`` — append (or
  replace by lowercased term) a confirmed term to ``ontology.md``.
- ``ontology_get_terms()`` — return the full term list (downstream
  agents lift the project's vocabulary in one call).
- ``ontology_lookup(term)`` — single-term entry lookup, case-
  insensitive on ``term``. Returns ``None`` when absent so callers can
  fall back to general LLM-vocabulary without raising.

The on-disk format is plain markdown with ``## term`` headings + a
definition paragraph + optional ``**Examples:**`` bullet list, so the
operator can edit it directly. The renderer + parser round-trip via
this module's ``render_ontology_md`` / ``parse_ontology_md``.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from jig.atomic import atomic_write_text
from jig.schemas.po import Ontology, OntologyTerm, PendingOntologyTerm

__all__ = [
    "handle_ontology_stash_term",
    "handle_ontology_add_term",
    "handle_ontology_get_terms",
    "handle_ontology_lookup",
    "render_ontology_md",
    "parse_ontology_md",
    "load_pending_terms",
]


# ---- on-disk paths --------------------------------------------------------


def _ontology_path(project_path: Path) -> Path:
    """The committed ontology — public per ``spec_loader.ontology_path``.

    Re-implemented here (rather than imported from ``spec_loader``) to
    avoid a circular import: ``spec_loader.load_ontology`` lazily
    imports this module's parser, so ontology helpers resolve their own
    paths without re-entering the loader.
    """
    return project_path / ".jig" / "spec" / "ontology.md"


def _pending_path(project_path: Path) -> Path:
    """Sidecar for stashed-but-not-yet-committed terms.

    Per design.md §"How it gets populated": Phase-3/4 of the journey
    walk surfaces terms; Phase-5 confirms them. Keeping pending terms
    in a separate file (rather than in the markdown) means the operator
    only sees confirmed entries when they read ``ontology.md``.
    """
    return project_path / ".jig" / "spec" / "ontology.pending.yaml"


# ---- markdown render + parse ---------------------------------------------


_TERMS_HEADING = "## Terms"


def render_ontology_md(ontology: Ontology, *, project_name: str) -> str:
    """Render ``ontology`` to the canonical markdown form.

    Format (per design.md §"Project ontology" sample):

        # <project_name> — Domain Vocabulary

        The terms used by people who actually use this product. The
        operator's words, not jig's words. Every agent reads this for
        consistent terminology. Operator can edit directly.

        ## Terms

        ### <term>
        <definition>

        **Examples:**
        - example one
        - example two

    Empty ontologies still render the headings so a downstream agent
    can read the file shape without ``FileNotFoundError`` after the
    first ``ontology_add_term`` call.
    """
    lines: list[str] = []
    lines.append(f"# {project_name} — Domain Vocabulary")
    lines.append("")
    lines.append(
        "The terms used by people who actually use this product. "
        "The operator's words, not jig's words. Every agent reads "
        "this for consistent terminology. Operator can edit directly."
    )
    lines.append("")
    lines.append(_TERMS_HEADING)
    lines.append("")
    for term in ontology.terms:
        lines.append(f"### {term.term}")
        lines.append(term.definition.rstrip())
        if term.examples:
            lines.append("")
            lines.append("**Examples:**")
            for ex in term.examples:
                lines.append(f"- {ex}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_ontology_md(text: str) -> Ontology:
    """Parse a markdown ontology back into an ``Ontology``.

    Round-trips with ``render_ontology_md``. Tolerant of operator
    edits — extra prose between H2/H3 sections is ignored, blank
    lines are collapsed, and the ``**Examples:**`` block is optional.

    Operator edits we deliberately accept:
    - Reworded definition paragraphs (we take everything between the
      ``### term`` heading and the next blank-line-then-marker as the
      definition).
    - Reordered terms (we preserve source order in the parsed
      ``Ontology.terms``).
    - Added / removed example bullets.
    """
    terms: list[OntologyTerm] = []
    lines = text.splitlines()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("### "):
            term_name = line[4:].strip()
            i += 1
            # Definition: every non-empty line until ``**Examples:**`` or
            # the next ``### `` heading or EOF. Blank lines inside the
            # definition collapse to single newlines so the schema's
            # ``min_length=1`` check passes on real input.
            def_lines: list[str] = []
            examples: list[str] = []
            while i < len(lines):
                nxt = lines[i]
                if nxt.startswith("### ") or nxt.startswith("## "):
                    break
                if nxt.strip().startswith("**Examples:**"):
                    i += 1
                    while i < len(lines):
                        ex_line = lines[i]
                        if ex_line.startswith("### ") or ex_line.startswith("## "):
                            break
                        ex_stripped = ex_line.strip()
                        if ex_stripped.startswith("- "):
                            examples.append(ex_stripped[2:].strip())
                        i += 1
                    break
                def_lines.append(nxt)
                i += 1
            definition = "\n".join(def_lines).strip()
            if not definition:
                # Skip headings with no definition — likely an in-flight
                # operator edit. Re-saving will drop them; meanwhile the
                # rest of the file still parses.
                continue
            terms.append(
                OntologyTerm(
                    term=term_name,
                    definition=definition,
                    examples=examples,
                )
            )
            continue
        i += 1

    return Ontology(terms=terms)


# ---- pending-term sidecar -------------------------------------------------


def load_pending_terms(project_path: Path) -> list[PendingOntologyTerm]:
    """Read the pending sidecar; empty list if absent."""
    p = _pending_path(project_path)
    if not p.is_file():
        return []
    raw = yaml.safe_load(p.read_text()) or []
    if not isinstance(raw, list):
        raise ValueError(
            f"pending ontology sidecar {p} corrupt: expected a list"
        )
    return [PendingOntologyTerm.model_validate(item) for item in raw]


def _save_pending_terms(
    project_path: Path, terms: list[PendingOntologyTerm]
) -> None:
    payload = yaml.safe_dump(
        [t.model_dump(mode="json") for t in terms], sort_keys=False
    )
    atomic_write_text(_pending_path(project_path), payload)


# ---- handlers -------------------------------------------------------------


async def handle_ontology_stash_term(
    *,
    project_path: Path,
    term: str,
    context: str,
) -> None:
    """Buffer a domain term surfaced mid-conversation.

    Idempotent on lowercased ``term`` — re-stashing the same term with
    a different context updates the recorded context (latest wins) so
    the L1 PO can refine its anchor without dupe entries piling up.
    """
    if not term.strip():
        raise ValueError("ontology term must not be empty")
    if not context.strip():
        raise ValueError("ontology stash context must not be empty")

    pending = PendingOntologyTerm(term=term.strip(), context=context.strip())
    terms = load_pending_terms(project_path)
    needle = pending.term.lower()
    terms = [t for t in terms if t.term.lower() != needle]
    terms.append(pending)
    _save_pending_terms(project_path, terms)


async def handle_ontology_add_term(
    *,
    project_path: Path,
    term: str,
    definition: str,
    examples: list[str] | None = None,
    project_name: str = "Project",
) -> None:
    """Commit a confirmed term to ``ontology.md``.

    Replace-by-lowercased-term semantics: re-adding a term swaps the
    definition + examples for the prior entry. Order of first-mention
    is preserved (we replace in place rather than appending) so the
    operator's reading order on disk stays stable across edits.

    Also clears the term from the pending sidecar (best-effort) — the
    L1 PO at Phase 5 typically commits + clears in two steps; this
    folds them so an LLM that forgets the second step doesn't leave
    stale pending entries.
    """
    if not term.strip():
        raise ValueError("ontology term must not be empty")
    if not definition.strip():
        raise ValueError("ontology definition must not be empty")

    new_entry = OntologyTerm(
        term=term.strip(),
        definition=definition.rstrip(),
        examples=[ex.strip() for ex in (examples or []) if ex.strip()],
    )

    ontology_path_ = _ontology_path(project_path)
    if ontology_path_.is_file():
        existing = parse_ontology_md(ontology_path_.read_text())
    else:
        existing = Ontology()

    needle = new_entry.term.lower()
    replaced = False
    out_terms: list[OntologyTerm] = []
    for t in existing.terms:
        if t.term.lower() == needle:
            # Preserve the first-mention casing — operators may type
            # the term inconsistently across turns ("BLOCKER" vs
            # "blocker") and the on-disk reading flow shouldn't churn
            # because of that. Only definition + examples update.
            out_terms.append(
                OntologyTerm(
                    term=t.term,
                    definition=new_entry.definition,
                    examples=new_entry.examples,
                )
            )
            replaced = True
        else:
            out_terms.append(t)
    if not replaced:
        out_terms.append(new_entry)

    md = render_ontology_md(
        Ontology(terms=out_terms), project_name=project_name
    )
    atomic_write_text(ontology_path_, md)

    # Drop the matching pending entry if present — LLM-friendly fold of
    # commit + clear so a forgotten clear-step doesn't leak stale state.
    pending = load_pending_terms(project_path)
    pruned = [p for p in pending if p.term.lower() != needle]
    if len(pruned) != len(pending):
        _save_pending_terms(project_path, pruned)


async def handle_ontology_get_terms(*, project_path: Path) -> dict[str, Any]:
    """Return the committed ontology as a JSON-friendly dump.

    Absent ontology surfaces as ``{"terms": []}`` rather than
    ``FileNotFoundError`` — downstream readers (always-injected agent
    context, reviewers) call this opportunistically and treat absence
    as "no vocabulary captured yet" rather than an error.
    """
    p = _ontology_path(project_path)
    if not p.is_file():
        return {"terms": []}
    ontology = parse_ontology_md(p.read_text())
    return ontology.model_dump(mode="json")


async def handle_ontology_lookup(
    *, project_path: Path, term: str,
) -> dict[str, Any] | None:
    """Return the entry for one term, or ``None`` when absent.

    Case-insensitive on ``term``; the returned dict is the ``OntologyTerm``
    JSON dump. Used by downstream agents to resolve a specific operator
    word (e.g., reviewer flagging a synonym not in the ontology).
    """
    p = _ontology_path(project_path)
    if not p.is_file():
        return None
    ontology = parse_ontology_md(p.read_text())
    entry = ontology.by_term(term)
    if entry is None:
        return None
    return entry.model_dump(mode="json")
