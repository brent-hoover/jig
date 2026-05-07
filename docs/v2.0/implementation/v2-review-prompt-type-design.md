---
title: v2 Type-Design Review Prompt
type: review-prompt
status: ready
owner: brent
created: 2026-05-04
---

# v2 Type-Design Review Prompt

Paste verbatim as the first message in a fresh agent session. The reviewer
needs read access to the repo and the ability to run `git` / `rg` / `pytest`.

---

You're doing a focused type-design review of jig (a multi-agent orchestrator)
on /Users/brent/Projects/personal/jig.

## Scope

The review target is the Pydantic schema surface added during v2 — roughly
50 model classes across these modules:

- jig/schemas/po.py — PO outputs (Project, Suite, Persona, Journey, etc.)
- jig/schemas/arch.py — SA outputs (Architecture, Module, ContractsFile,
  BehavioralContract, DataContract, Risk, etc.)
- jig/schemas/plan.py — PM outputs (BuildPlan, Epic, EpicLayers, etc.)
- jig/schemas/dev_env.py — Dev manifest types
- jig/schemas/frontend.py — Frontend stack
- jig/schemas/design_system.py — Design tokens / components / brand
- jig/schemas/_validators.py — Shared validators (kebab-id, tz-aware, URI shape)
- jig/intent.py — Intent + ComplicationsConsidered
- jig/ticket.py — Ticket model (extended with v2 fields)
- jig/reviewers/comment.py — ReviewerComment + Evidence
- jig/sim/scenario.py + jig/sim/persona.py + jig/sim/assertions.py —
  Simulator types

## What you're rating

For each model, judge it on four axes (rate each 1-5):

1. **Encapsulation** — does the model bundle related state, or is it a
   loose bag of optional fields the caller has to assemble correctly?
2. **Invariant expression** — does the schema actually express the
   invariants the design doc claims? (e.g., the design says "Risk
   requires dependent_contracts when status >= spike_proposed" — does
   the schema enforce that, or does it rely on a separate validator
   the caller might forget?)
3. **Usefulness** — does this type help downstream callers, or is it
   ceremony that callers route around with raw dicts?
4. **Enforcement** — does Pydantic actually catch malformed inputs at
   construction, or are there bypass paths via raw dict construction
   or extra='allow'?

## Anti-patterns to flag

- **Bag-of-optionals** — model with 15+ optional fields, no required
  invariants, used as a typed dict with no semantic value
- **Cross-field invariants in docstrings** — the docstring says "X is
  required when Y" but no validator enforces it
- **Implicit defaults that mask intent** — `field: list = []` where the
  caller might mean "no items" or might mean "I forgot to populate"
- **Mutable shared defaults** — `field_factory=lambda: shared_dict`
  bugs (Pydantic v2 catches most but worth checking)
- **Stringly-typed fields where enums would help** — `kind: str` with
  a fixed value set
- **Missing `extra='forbid'`** — schemas that silently accept unknown
  keys are rot-magnets
- **Overlap between models** — two models that are 80% the same shape;
  one should subclass the other or they should share a base
- **Models that should be ADTs** — discriminated unions modeled as a
  parent class with optional sub-typed fields

## High-suspicion targets

The largest schemas in the corpus:
- `Architecture` + `Module` + `ContractsFile` + `BehavioralContract` —
  the SA surface is the most complex; rate carefully
- `BuildPlan` + `Epic` + `EpicLayers` + `LayerStatus` — PM hierarchy,
  cross-field invariants live here
- `Ticket` + v2 extension fields — Block 4 added validators but the
  field set is now large
- `ReviewerComment` — accumulated 7+ optional fields across reviewer
  kinds; might want discriminated union by `type`
- `Scenario` + `Persona` + `Assertion` — simulator surface

## How to run things

- `uv run pytest tests/test_schemas_*.py tests/test_*_v2*.py
  tests/test_schema_validators.py` — schema-specific tests
- `uv run python -c "from jig.schemas.arch import *; print(Architecture.model_json_schema())"`
  to dump JSON Schema
- Search docs/v2.0/ for design intent on each schema; cross-reference
  against the model

## Output format

Markdown. For each significant model (~15-20 to cover), produce a
ratings block:

### `Architecture` (`jig/schemas/arch.py:N`)
Encapsulation: 4/5 — short justification
Invariant expression: 3/5 — short justification
Usefulness: 4/5 — short justification
Enforcement: 3/5 — short justification
**Notes:** What's broken, what's good, suggested change.

After the per-model section, write:

### Cross-cutting observations
Patterns that span multiple schemas — base classes that should exist,
duplicated validators, etc.

### Highest-leverage improvements
The 3-5 model changes that would most improve the type surface.

## What NOT to focus on

- Style nitpicks
- Field-naming bikeshedding
- Test coverage of each model individually
- Optimization of model_dump performance

## Discipline

Don't write a v2 rewrite. The review is rating what landed and naming
the highest-leverage tightenings, not redesigning.

End with one sentence: "schema surface is solid / schema surface needs
N tightenings before shipping" + the two-or-three highest-leverage
changes.
