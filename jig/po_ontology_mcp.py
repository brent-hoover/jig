"""Project ontology MCP tool handlers (Track B6 MVP).

Per ``docs/v2.0/multi-level-spec/design.md`` §"Project ontology — capturing
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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

from jig.analytics.emitter import EventEmitter
from jig.analytics.events import OntologyTermEdited, OntologyTermRemoved
from jig.atomic import atomic_write_text
from jig.schemas.po import Ontology, OntologyTerm, PendingOntologyTerm

__all__ = [
    "handle_ontology_stash_term",
    "handle_ontology_add_term",
    "handle_ontology_edit_term",
    "handle_ontology_remove_term",
    "handle_ontology_find_references",
    "handle_ontology_get_terms",
    "handle_ontology_lookup",
    "render_ontology_md",
    "parse_ontology_md",
    "load_pending_terms",
    "EditResult",
    "RemoveResult",
    "OntologyReference",
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


# ---- operator-edit affordances (Track B Final) ---------------------------


class OntologyReference(BaseModel):
    """One reference to an ontology term inside a v2 artifact.

    ``path`` is project-relative so it round-trips into operator-facing
    output. ``line`` is 1-indexed (matches the way grep / editors talk
    about line numbers). ``snippet`` is the matched line trimmed for
    display — the operator wants to see context, not just a line number.
    """

    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., description="Project-relative artifact path.")
    line: int = Field(..., ge=1)
    snippet: str = Field(..., min_length=1)


@dataclass
class EditResult:
    """Outcome of ``handle_ontology_edit_term``.

    ``term`` is the (operator-cased) term that was updated. ``replaced``
    is True when an existing entry was replaced; False when the call
    behaved like an add (no prior entry). The handler raises rather than
    silently inserting when the term doesn't exist — operators expect
    "edit" to fail loud on a missing term.
    """

    term: str
    replaced: bool = True


@dataclass
class RemoveResult:
    """Outcome of ``handle_ontology_remove_term``.

    ``term`` is the removed term as it appeared on disk.
    ``replacement_term`` is the redirect target if the operator chose
    one. ``rewritten`` is the list of project-relative artifact paths
    where the reference text was rewritten in place; ``orphaned`` is the
    list of refs that had no replacement and now point at a vanished
    term (operator can address those by hand).
    """

    term: str
    replacement_term: str | None = None
    rewritten: list[str] = field(default_factory=list)
    orphaned: list[OntologyReference] = field(default_factory=list)


# Artifact roots scanned for references. Track B Final scope: brief
# files (suite briefs), contracts (per-module contract YAML), and suite
# specs. The scan is opportunistic — missing roots return zero matches
# rather than raising, so a project that hasn't written suites yet
# doesn't break the call.
_REFERENCE_ROOTS = (
    Path(".jig") / "spec" / "suites",
    Path(".jig") / "spec" / "modules",
    Path(".jig") / "spec",  # suites.yaml lives here
    Path(".jig") / "store",  # comments.jsonl lives here
)


def _is_word_boundary(text: str, start: int, end: int) -> bool:
    """Return True when text[start:end] is bounded by non-word chars.

    Term references in artifacts may include spaces (multi-word terms
    like "fast pay"); a simple ``\bterm\b`` regex doesn't work for those.
    We do the boundary check ourselves: a match is a "real" reference
    when the preceding + following characters are not word characters.
    """
    before_ok = start == 0 or not text[start - 1].isalnum() and text[start - 1] != "_"
    after_ok = end == len(text) or not text[end].isalnum() and text[end] != "_"
    return before_ok and after_ok


def _scan_text_for_term(
    *, term: str, text: str, path_rel: str
) -> list[OntologyReference]:
    """Return one OntologyReference per line containing ``term``.

    Case-insensitive match with word-boundary semantics (spaces in
    multi-word terms count as boundaries on each side). Skips lines
    inside ontology.md headings (``### term``) so the ontology file's
    own definition doesn't show up as a "reference".
    """
    out: list[OntologyReference] = []
    needle = term.lower()
    needle_len = len(needle)
    if not needle:
        return out
    for idx, line in enumerate(text.splitlines(), start=1):
        # Skip the ontology heading row — that's the definition, not a
        # reference. Operator-edits-the-ontology-file workflows would
        # otherwise spam the reference list.
        stripped = line.strip()
        if stripped.startswith("###") and stripped.lower().endswith(needle):
            continue
        lower = line.lower()
        pos = 0
        while True:
            found = lower.find(needle, pos)
            if found < 0:
                break
            if _is_word_boundary(line, found, found + needle_len):
                out.append(
                    OntologyReference(
                        path=path_rel,
                        line=idx,
                        snippet=line.strip()[:200] or line.rstrip()[:200],
                    )
                )
                break  # one ref per line — multi-mentions on one line
            pos = found + 1
    return out


def _iter_artifact_files(project_path: Path):
    """Yield (file_path, project_rel_path) for each scannable artifact.

    Iterates ``_REFERENCE_ROOTS`` and yields markdown / yaml / jsonl
    files within them. Hidden/temporary files are skipped via the
    extension allowlist. Path-deduped — overlapping roots (e.g.
    ``.jig/spec`` containing ``.jig/spec/suites``) yield each file at
    most once.
    """
    allowed_exts = {".md", ".yaml", ".yml", ".jsonl"}
    seen: set[str] = set()
    for rel in _REFERENCE_ROOTS:
        root = project_path / rel
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix in allowed_exts:
                key = str(root.relative_to(project_path))
                if key not in seen:
                    seen.add(key)
                    yield root, key
            continue
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            if f.suffix not in allowed_exts:
                continue
            # Skip the ontology file + the discovery state file — those
            # are the ontology's own home, not artifact references.
            rel_path = f.relative_to(project_path)
            if rel_path.parts[-1] in ("ontology.md", "discovery.state.yaml"):
                continue
            key = str(rel_path)
            if key in seen:
                continue
            seen.add(key)
            yield f, key


async def handle_ontology_find_references(
    *,
    project_path: Path,
    term: str,
) -> list[OntologyReference]:
    """Scan v2 artifacts for references to ``term``.

    Returns a sorted list of ``OntologyReference`` rows. The scan covers
    suite briefs, module contracts, the suites index, and the comments
    store. Empty list when nothing matched (or no artifacts exist yet).
    """
    if not term.strip():
        raise ValueError("ontology find_references requires a non-empty term")
    refs: list[OntologyReference] = []
    for f, rel in _iter_artifact_files(project_path):
        try:
            text = f.read_text()
        except OSError:
            continue
        refs.extend(_scan_text_for_term(term=term, text=text, path_rel=rel))
    refs.sort(key=lambda r: (r.path, r.line))
    return refs


async def handle_ontology_edit_term(
    *,
    project_path: Path,
    term: str,
    definition: str,
    examples: list[str] | None = None,
    project_name: str = "Project",
    emitter: EventEmitter | None = None,
) -> EditResult:
    """Replace an existing term's definition + examples.

    Distinct from ``ontology_add_term``: this raises ``KeyError`` when
    the term doesn't exist. Operators reach for "edit" expecting the
    target to be present — silently creating a new entry would mask
    typos like ``ontology edit blockah`` (when they meant blocker).

    Emits ``OntologyTermEdited`` analytics event when an emitter is
    provided. Production callers (the L1 PO MCP server) wire one in;
    test code can omit it.
    """
    if not term.strip():
        raise ValueError("ontology term must not be empty")
    if not definition.strip():
        raise ValueError("ontology definition must not be empty")

    p = _ontology_path(project_path)
    if not p.is_file():
        raise KeyError(
            f"ontology.md not found at {p}; cannot edit a term in a missing file"
        )
    ontology = parse_ontology_md(p.read_text())
    needle = term.strip().lower()
    target = next(
        (t for t in ontology.terms if t.term.lower() == needle), None
    )
    if target is None:
        raise KeyError(f"ontology has no term {term!r}; nothing to edit")

    cleaned_examples = [ex.strip() for ex in (examples or []) if ex.strip()]
    new_terms: list[OntologyTerm] = []
    for t in ontology.terms:
        if t.term.lower() == needle:
            new_terms.append(
                OntologyTerm(
                    term=t.term,  # preserve original casing
                    definition=definition.rstrip(),
                    examples=cleaned_examples,
                )
            )
        else:
            new_terms.append(t)

    md = render_ontology_md(
        Ontology(terms=new_terms), project_name=project_name
    )
    atomic_write_text(p, md)

    if emitter is not None:
        emitter.emit_nowait(
            OntologyTermEdited(
                term=target.term,
                examples_count=len(cleaned_examples),
            )
        )
    return EditResult(term=target.term, replaced=True)


async def handle_ontology_remove_term(
    *,
    project_path: Path,
    term: str,
    replacement_term: str | None = None,
    project_name: str = "Project",
    emitter: EventEmitter | None = None,
) -> RemoveResult:
    """Remove a term; optionally redirect downstream references.

    Two modes:

    - ``replacement_term`` is **None** (default) — the term is removed
      and any references in suite briefs / contracts / comments stay
      put but become *orphaned* (no matching ontology entry). The
      operator can address them by hand.
    - ``replacement_term`` is **set** — references in v2 artifacts get
      rewritten in place to point at the replacement. The replacement
      must already exist in the ontology, otherwise we'd be redirecting
      to another orphan.

    Emits ``OntologyTermRemoved`` analytics event when an emitter is
    provided.
    """
    if not term.strip():
        raise ValueError("ontology term must not be empty")

    p = _ontology_path(project_path)
    if not p.is_file():
        raise KeyError(
            f"ontology.md not found at {p}; cannot remove a term in a missing file"
        )
    ontology = parse_ontology_md(p.read_text())
    needle = term.strip().lower()
    target = next(
        (t for t in ontology.terms if t.term.lower() == needle), None
    )
    if target is None:
        raise KeyError(f"ontology has no term {term!r}; nothing to remove")

    replacement_canonical: str | None = None
    if replacement_term is not None:
        replacement_canonical = replacement_term.strip()
        if not replacement_canonical:
            raise ValueError("replacement_term, when provided, must be non-empty")
        repl = next(
            (
                t
                for t in ontology.terms
                if t.term.lower() == replacement_canonical.lower()
            ),
            None,
        )
        if repl is None:
            raise KeyError(
                f"replacement_term {replacement_canonical!r} is not in the ontology"
            )
        replacement_canonical = repl.term  # preserve operator's casing

    new_terms = [t for t in ontology.terms if t.term.lower() != needle]
    md = render_ontology_md(
        Ontology(terms=new_terms), project_name=project_name
    )
    atomic_write_text(p, md)

    rewritten: list[str] = []
    orphaned: list[OntologyReference] = []
    refs = await handle_ontology_find_references(
        project_path=project_path, term=target.term
    )
    if replacement_canonical is not None:
        # Auto-rewrite references in artifact files. We do this with a
        # word-boundary regex so we don't accidentally rewrite substrings
        # of unrelated identifiers (e.g. "blocker" inside "roadblockers").
        seen_paths: set[str] = set()
        for ref in refs:
            if ref.path in seen_paths:
                continue
            seen_paths.add(ref.path)
            file_path = project_path / ref.path
            try:
                text = file_path.read_text()
            except OSError:
                continue
            rewritten_text = _rewrite_term(
                text=text,
                old=target.term,
                new=replacement_canonical,
            )
            if rewritten_text != text:
                atomic_write_text(file_path, rewritten_text)
                rewritten.append(ref.path)
    else:
        orphaned = refs

    if emitter is not None:
        emitter.emit_nowait(
            OntologyTermRemoved(
                term=target.term,
                replacement_term=replacement_canonical,
                orphaned_reference_count=len(orphaned),
            )
        )
    return RemoveResult(
        term=target.term,
        replacement_term=replacement_canonical,
        rewritten=rewritten,
        orphaned=orphaned,
    )


def _rewrite_term(*, text: str, old: str, new: str) -> str:
    """Replace ``old`` with ``new`` in ``text`` with word-boundary semantics.

    Case-preserving on the prefix only — multi-word terms keep the
    operator's chosen casing for ``new``. Doesn't rewrite occurrences
    inside ``###`` headings (those are ontology definitions, not refs;
    the heading is removed when the term is removed from the ontology
    body itself, not here).
    """
    # Build a pattern that matches ``old`` literal, with word boundaries
    # around it. We use a custom boundary (non-word-char or string
    # endpoint) to handle multi-word terms.
    pattern = re.compile(
        r"(?<![A-Za-z0-9_])" + re.escape(old) + r"(?![A-Za-z0-9_])",
        re.IGNORECASE,
    )
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        # Skip rewrite on lines that look like ontology headings.
        if line.lstrip().startswith("###"):
            out_lines.append(line)
            continue
        out_lines.append(pattern.sub(new, line))
    return "".join(out_lines)
