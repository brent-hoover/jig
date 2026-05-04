---
title: Visual Design — Problem Statement
type: problem
status: draft
owner: brent
created: 2026-05-01
---

# Visual Design — Problem Statement

## Context

The current jig design corpus is text-first: briefs, contracts, build plans, AC, comments, learnings — all prose /
structured-text artifacts. That works cleanly for projects whose deliverable is text-shaped: backend services, CLI
tools, daemons, libraries.

For anything with a UI — web apps, mobile apps, desktop apps, browser extensions, even sufficiently rich CLI output —
there's an entire layer of work the design corpus is silent on:

- **Visual artifacts**: wireframes, hi-fi mocks, component specs, design tokens (color, spacing, typography),
  iconography, illustrations.
- **Operator visual input may exist or may not.** Some operators arrive with a Figma file, a stack of screenshots, a
  hand-drawn sketch, a competitor product they're pointing at, or a brand style guide PDF. Many don't — they have a
  rough sense of what the product does but no opinion (yet) on what it should look like. Both are valid starting points;
  the system needs to handle both.
- **Visual judgment**: does this implementation match what we agreed on? Are the components consistent across screens?
  Is this accessible? Does it look right?
- **Visual references during work**: dev agents implementing UI screens need to see what they're building; today there's
  no story for getting visual context into a tenet-2-respecting agent.


## Problem

For UI projects, the existing PO/SA/PM/dev workflow has gaps:

- **PO discovery walks journeys** (text), but journeys go through screens. The brief captures behaviors but not layouts,
  visual hierarchy, or interaction patterns.
- **SA contracts cover integration boundaries** but say nothing about UI primitives. Two UI tickets implementing
  different screens don't have a shared contract on "what does Button look like" — they each invent.
- **Dev agents** working UI tickets get behavior AC and integration AC injected, but no visual reference. They have to
  guess what the UI should look like.
- **Reviewer federation** checks contract compliance and pattern conformance, but no reviewer asks "does this match the
  mock?" or "is this consistent with how Button is used elsewhere?"

The result: agents implement UIs that work functionally but look wrong, are inconsistent, drift from any mocks the
operator has, and create maintenance debt as visual divergence accumulates.

## Simplest possible solution

**A VD agent walks the operator through a wireframe-first discovery loop, the same shape as PO discovery walks
journeys.** The system always produces something workable; operator-supplied artifacts are an *input* to that discovery,
not a prerequisite for it.

Two paths feed into the same loop:

- **Operator has visual input.** Drops Figma exports, screenshots, sketches, brand guides into
  `.jig/design/references/`. VD reads them and uses them to bias its initial wireframe drafts and design system
  defaults. The operator's intent shows up in the early proposals; iteration converges fast.
- **Operator has nothing.** VD proposes wireframes from the journeys alone, applying minimal default tokens and a
  framework-less stack. Operator accepts, edits, or pushes back per-screen. The lack of a starting artifact is itself
  signal: this operator doesn't have strong opinions on visual layout, so the agent's job is to propose something
  defensible and let the operator react.

Either way the output is the same: a wireframe set covering every screen implied by L1 journeys, plus a design system
(operator-customized or default), plus a frontend architecture (operator-overridden or default). Dev agents on UI
tickets get the relevant slice injected via vision-capable models.

What this does NOT do (deferred to later v2 layers): catch fine-grained divergence between agents implementing different
screens, enforce a component library beyond the named token set, provide automated screenshot-based visual review at
full sensitivity, or substitute for an operator who DOES have strong opinions and wants to author the design system in
detail (Claude Design integration handles that path).

## Complications considered

- **Scale**: more screens → more references → context cost. A 30-screen app has too many references to inject per
  ticket. Forces: per-capability or per-screen reference scoping. The `visual_references` field needs to be attached at
  the right granularity (suite + per-capability override) so dev agents see only what's relevant.
- **Concurrency**: two dev agents implementing different screens that share UI primitives (Button, Input, Card) invent
  them differently. Forces: a shared component library / design tokens artifact — the visual equivalent of SA's shared
  contracts. Not v2; deferred.
- **Failure modes**: agent implements something that doesn't match the mock. Mode A: catastrophic divergence (entirely
  wrong layout). Mode B: subtle drift (spacing, color, typography slightly off). Operator can catch Mode A by eye; Mode
  B accumulates silently. Forces: visual review as a reviewer-federation type, possibly vision-based screenshot diff.
  Deferred behind v2 simplest.
- **Cross-cutting policies**: accessibility (WCAG AA / AAA), responsive design (breakpoints), brand consistency
  (palette, typography). These apply system-wide and the SA's cross-cutting policy mechanism could absorb them — but
  they need visual-aware enforcement, not just text rules.

Other complications:

- **Operator workflow**: the TUI is text-only. Showing the operator a visual mock during PO discovery, or a screenshot
  of an implementation during review, requires either an external viewer (operator opens the file themselves) or a
  browser-based companion (similar to the visual companion in some claude skills). Real but punt-able for v2.
- **Vision context cost**: vision-capable models cost more per token; images take a lot of context. Tenet 2 applies —
  only inject visual references when the ticket clearly needs them.
- **Source-of-truth divergence**: operator's Figma is the canonical design; jig consumes exports. If operator updates
  Figma but doesn't re-export, jig works from stale references. Forces: explicit re-import step, versioned references,
  possibly Figma API integration (deferred).

## Constraints

- **Must use vision-capable models** for any agent that consumes visual references. Probably means the appropriate
  Claude vision model selected per agent, with cost as a real consideration.
- **Tenet 2 (exact context)**: don't dump the entire design system into every agent. Per-ticket reference scoping is
  required.
- **Tenet 5 (both sides earn best thinking)**: operator-side visual workflow needs as much thought as the agent-side.
  Showing visuals in the TUI / a side-panel matters; treating the operator as a passive uploader doesn't.
- **Scale-down**: backend-only projects (CLIs, daemons, libraries) bypass this layer entirely. The simplest solution
  above is no-op when no `visual_references` are declared.
- **Compose with existing workflow**: visual design fits alongside PO/SA/PM, not as a replacement. Probably parallel to
  SA in the sequence (both consume PO discovery, both produce inputs to the build plan).
- **AI-driven where possible**: operator shouldn't have to hand-author tedious component specs. But operator IS the
  source of "what should this look like" — agents can't generate that from nothing.

## Requirements

- A **`visual_references` field** on suite briefs (and optionally per capability) pointing at operator-provided files in
  `.jig/design/`.
- A **per-ticket reference resolver**: when a UI ticket is dispatched, the orchestrator looks up which visual references
  apply (suite-level + per-capability override) and injects them into the dev agent's context.
- A **vision-aware dev agent** path (use a vision-capable model when the ticket has visual_references).
- (Eventually) A **visual reviewer agent type** in the federation: at minimum, can a vision model compare implementation
  screenshot to reference and flag obvious divergence?
- (Eventually) A **shared component library / design tokens artifact** that constrains how UI primitives get built
  across modules — visual equivalent of SA's shared contracts.
- (Eventually) A **visual companion** for the operator: side-panel / browser view showing references during PO
  discovery, implementation screenshots during review.
- (Eventually) **Visual cross-cutting policies**: accessibility, responsive, brand. SA's cross-cutting mechanism
  extended to visual-aware predicates.

## Non-goals

- **Generating high-fidelity mocks from text descriptions.** VD generates *wireframes* (grey-screen SVG, structural) as
  part of the discovery loop, but it doesn't generate hi-fi visual mocks. Operators who want those bring them in (Figma,
  etc.) or use Claude Design integration; without either, the project ships against the wireframe + default design
  system.
- **Pixel-perfect implementation enforcement.** Vision-based diff is fuzzy; we accept "close enough" rather than chase
  pixel-level matching.
- **Replacing design tools.** Figma, Sketch, Penpot, Excalidraw all stay in operator's hands. Jig reads exports, not
  source files.
- **Animation / transition / motion design** as a first-class discipline. May come back; not v2.
- **Brand identity work** (logos, illustration libraries). Operator hands those in; jig doesn't generate.
- **Design system documentation as a downstream product.** What jig produces is enough to implement consistently; if
  operator wants a marketing-quality design system site, that's a different deliverable.

## Success criteria

- **For a UI project where the operator has visual artifacts:** VD reads them, biases its wireframe drafts toward what
  the operator already has in mind, converges fast in the per-screen confirmation loop. Dev agents implement screens
  that match the agreed wireframes at the level of obvious divergence (layout, hierarchy, primary colors).
- **For a UI project where the operator has nothing:** VD proposes wireframes from journeys alone, applies default
  tokens and a framework-less stack, and walks the operator through per-screen confirmation. Operator can react ("yeah,
  that's right" / "no, the form should be on the left") without ever having to author visual artifacts. The output ships
  and looks defensible even if the operator never has a strong opinion.
- **For a backend-only project:** zero ceremony — VD discovery exits in one step ("no UI surfaces"); no `.jig/design/`
  content, no vision models invoked.
- Operator can update a wireframe (or supply new reference material) at any point and trigger re-implementation of
  affected screens.
- Visual divergence between two screens that share UI primitives (Button, Form) is detectable — at minimum the reviewer
  surfaces "these two screens use Button differently." (Likely deferred behind v2.)

## Open questions

- [ ] **New role or PO extension?** A "Visual Designer" (VD) agent parallel to PO/SA/PM, or visual design as part of an
  expanded PO? Argument for new role: visual design is a different discipline with different artifacts and a different
  reviewer pattern. Argument for PO extension: small projects don't need a separate role and PO already owns "what does
  the product do."
- [ ] **Where in the workflow sequence?** Parallel to SA (both consume PO discovery, both produce contracts)? After SA
  but before PM? Inside PO L3 suite briefs as a sub-section? Sequencing affects how PM build plans reference visual
  artifacts.
- [ ] **What's the visual contract format?** Free-form file references in v2 is fine. For the eventual component
  library: YAML schema for design tokens? Storybook-style component spec? Something else? Probably reference existing
  standards (e.g., Style Dictionary for tokens) where they exist — same principle as SA contracts pointing at OpenAPI /
  Protobuf.
- [ ] **Visual review mechanism.** Vision-model screenshot diff is the obvious first thing. How sensitive? How does it
  integrate with the PR review loop? Is it a reviewer agent type, or does it run before review fires?
- [ ] **TUI vs browser companion.** The TUI is text-only; visual artifacts can't render. Either operator opens files
  externally, OR a browser companion (similar pattern to some claude skills) shows visuals in a side pane. Which is
  acceptable v2?
- [ ] **Source-of-truth and re-import.** If operator updates an external design tool but doesn't re-export, jig works
  from stale references. Detection mechanism? Periodic API pull (heavy)? Operator-confirmed re-import ritual? Probably
  the latter for v2; integration-via-API is per-tool and deferred.
- [ ] **Operator-supplied SVG override.** Design.md commits to agent-generated SVG wireframes with operator
  confirmation. Open: do we let operator drop in their own SVG when they have a strong opinion, and how does that
  compose with agent-generated screens elsewhere in the same project?
- [ ] **Visual review sensitivity.** Vision-model screenshot diff against the wireframe — what's the threshold for
  "matches"? Strict (any divergence flagged) vs loose (only obvious layout breaks flagged) vs configurable per project?
  Probably configurable with sensible defaults.
- [ ] **Claude Design integration mechanics.** Design.md commits to leveraging Anthropic-provided design tooling where
  available. Open: API shape, export formats, what jig consumes vs what stays in the external tool.

**Resolved during 2026-05-01 brainstorming** (now in `design.md`):

- New role (VD) parallel to PO/SA/PM, not a PO extension.
- Workflow sequence: VD parallel to SA; both consume PO discovery; both feed PM.
- Two-stage VD work: wireframe (grey-screen, SVG, structural) first; design system (tokens + components) second.
- Browser-based viewing — generated HTML index over the SVG wireframes; operator opens locally. Claude already knows how
  to open browser pages.
- Visual review as a separate `visual_compliance` reviewer type in the PM federation; vision-based diff between
  implementation screenshot and the wireframe.
- Bones/MVP/final layering applies: bones renders the wireframe with placeholder content; MVP uses design system; final
  adds responsive/accessibility/polish.

## Change log

- 2026-05-01: Initial capture (brent + claude). Names visual design as the missing discipline for UI projects. Frames
  simplest solution (operator-provided references + vision injection) and what it doesn't cover. Many open questions
  remain — particularly around role placement (new VD vs PO extension), workflow sequencing, and visual review
  mechanism. Design deferred until those are resolved.
- 2026-05-03: Reframed away from the operator-must-bring-artifacts framing. The system always produces something
  workable; operator artifacts are an *input* to VD discovery, not a prerequisite for it. Two paths feed the same loop —
  operator-has-input (VD biases drafts toward existing material; converges fast) and operator-has-nothing (VD proposes
  from journeys + defaults; operator reacts per screen). The lack of artifacts is itself signal that the operator
  doesn't have strong opinions on visual layout, so VD's job is to propose something defensible. Updated context
  section, simplest-possible-solution, non-goals (mock-generation distinction sharpened — VD generates wireframes, not
  hi-fi mocks), and success criteria (now covers both paths explicitly).
