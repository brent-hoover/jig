---
title: Visual Design — Design
type: design
status: draft
owner: brent
created: 2026-05-01
problem: ./problem.md
---

# Visual Design — Design

## Summary

Visual Design (**VD**) is a new role parallel to PO / SA / PM. **VD is the architect for the frontend, not just the
visual designer** — the frontend stack choice is inseparable from visual implementation patterns, so it belongs to VD
rather than SA. VD runs after PO discovery completes, in parallel with SA, and produces three artifact tiers:

1. **Frontend architecture** (`.jig/design/frontend.yaml`): stack choice, build tooling, component implementation
   pattern, accessibility target. Default is minimal / framework-less (HTMX + Alpine.js + custom CSS with CSS custom
   properties referencing tokens). Operator can override with framework choice + rationale.
2. **Design system** (tokens + component spec + brand): **always present** — Jig applies a minimal default at the moment
   VD discovery starts so wireframes have something to render against. Operator can replace via Anthropic-provided
   design tooling ("Claude Design") where available, or supply their own export from any tool. `default` is a permanent
   valid state, not a placeholder.
3. **Wireframes** (grey-screens): one SVG per screen, structural-only — boxes, labels, regions, no styling. Covers
   *every screen implied by L1 journeys* before any UI implementation begins.

The first two are *foundational* (set once at the start of VD discovery, modified rarely); the third is *iterative*
(walked screen-by-screen with the operator).

Browser-based viewing throughout: jig generates a static HTML index over the SVG wireframes; operator opens it locally
to review. (Claude already knows how to open browser pages, so the operator never has to leave the conversational flow
for long.)

Visual review enters the PM reviewer federation as a new `visual_compliance` reviewer type — vision-model diff between
implementation screenshots and the wireframe (plus design-system check at MVP/Final layers).

For backend-only projects: VD discovery is invoked but exits immediately ("no UI surfaces — VD has nothing to do"). Same
scale-down pattern as SA and PM.

## Sequencing relative to PO / SA / PM

```
L0 Pitch         (PO)
L1 Discovery     (PO)  — personas, journeys, capabilities
L2 Suites        (PO)  — capability groupings
L3 Suite briefs  (PO)  — behaviors, behavior AC per suite
   ─────────  operator declares PO "done for now"  ─────────
SA Architecture  (SA)  ─┐  parallel; both consume the same
VD Discovery     (VD)  ─┤  PO artifacts; neither blocks the
                        │  other
   ─────────  operator declares SA + VD "done for now"  ─────────
Spike tickets    (dev) — bounded exploration to mitigate risks
SA delta + VD delta    — fold spike learnings back in
   ─────────  Planner PM fires  ─────────
Build plan       (PM)  — UI tickets reference VD wireframes;
                         non-UI tickets reference SA contracts;
                         many tickets reference both
L4 Tickets       (dev) — bones-first across all epics; UI
                         tickets get wireframe + design system
                         injected; reviewed by federated
                         reviewers including visual_compliance
                         on UI tickets
```

VD and SA run in parallel because they have orthogonal concerns:

- SA decides *backend* technical shape — server frameworks, databases, message buses, integration patterns, deployment.
- VD decides *frontend* technical shape AND *visual* shape — frontend stack, build tooling, layouts, hierarchy,
  components, brand. **VD is the architect for the frontend, not just the visual designer.**

The frontend stack choice is inseparable from visual implementation patterns (HTMX vs React vs Alpine produce
fundamentally different codebases for the same wireframe), so it belongs to VD rather than SA. SA may still have
opinions where backend and frontend connect (e.g., does the backend render server-side HTML or only emit JSON?), but the
frontend technology is VD's call.

Neither blocks the other; both have to land before PM can produce a build plan.

## VD's three-tier structure

| Tier | Artifact | What's there | "Done" means |
|---|---|---|---|
| **VD-frontend-arch** | `.jig/design/frontend.yaml` | Stack choice (HTMX + Alpine + custom CSS by default), build tooling, code organization, component implementation pattern, accessibility target | Operator confirms (defaults applied if operator skips). One pass at start of VD discovery; rare changes thereafter. |
| **VD-system** | `.jig/design/system/tokens.yaml`, `.jig/design/system/components.yaml`, `.jig/design/system/brand.md` | Design tokens (color, spacing, typography), component specs (Button states, Input variants), brand voice + palette | **Always present** — defaults applied immediately so wireframes have something to render against; operator can replace via Claude Design or supply their own export. "Default" is a permanent valid state, not a placeholder. |
| **VD-wireframes** | `.jig/design/wireframes/<screen-id>.svg` (one per screen) + `.jig/design/wireframes/index.html` | Grey-screen SVG per screen — structure, regions, content placeholders, interactive elements labeled, state coverage notes | Every screen implied by L1 journeys has a wireframe; operator confirms "all screens present" gate via the browser index. |

The three tiers stack: frontend architecture and design system are *foundational* (set once, modified rarely);
wireframes are *iterative* (one per screen, walked with the operator). Wireframes always render against a real design
system from the start because the default is always there.

## Artifacts on disk

```
.jig/
  design/
    frontend.yaml                    Stack choice, build tooling, code organization (VD-frontend-arch tier)
    wireframes/
      index.html                     Browser-viewable index of all wireframes
      <screen-id>.svg                One SVG per screen
      <screen-id>.notes.md           Per-screen interaction notes, state coverage
      screens.yaml                   Roster: screen-id → journey ids → capability ids
    system/
      tokens.yaml                    Design tokens (color, spacing, typography, etc.) — always present
      components.yaml                Component spec — always present
      brand.md                       Brand voice + palette + usage notes
      source.yaml                    Where the design system came from (Claude Design / operator-supplied / default)
    references/                      Operator-dropped reference material (mood boards, competitor screenshots)
      *
```

All three artifact tiers are present from the start. The frontend.yaml and design system carry defaults until the
operator chooses to modify them; the wireframes get authored screen-by-screen during VD discovery. The references
directory is operator-curated mood/inspiration material that VD reads but doesn't author.

## VD discovery loop

Same shape as PO L1 and SA discovery; parameterized for screens. Three-stage with foundational decisions first, then the
iterative wireframe walk:

```
0. Apply defaults (one-time, at VD start):
   - Write `.jig/design/frontend.yaml` with default frontend stack (HTMX + Alpine + custom CSS).
   - Write `.jig/design/system/{tokens,components,brand}.yaml` with default design system.
   - These are immediately valid; wireframes can render against them.

1. Confirm frontend architecture (foundational, one pass):
   - Show operator the default stack: "I'm using HTMX + Alpine.js + custom CSS with CSS custom properties for tokens.
     Keep this default, or override?"
   - If operator overrides: walk the override (React? Svelte? plain HTML?). Capture rationale; record the override
     in frontend.yaml.
   - Operator confirms; gate passes.

2. Confirm design system source (foundational, one pass):
   - Show operator the default tokens / components: "Defaults applied. Want to author a custom design system in
     Claude Design? Supply your own export? Or keep defaults?"
   - If operator picks Claude Design: pause, hand off, consume export when ready.
   - If operator supplies their own: read and normalize.
   - If operator keeps defaults: confirm and continue. (Most common; no friction.)
   - Operator confirms; gate passes.

3. Build the screen roster:
   - Read L1 discovery (journeys, personas)
   - Read L3 suite briefs (capabilities + behaviors)
   - Propose initial screen list: every "the user does X on a screen" → a candidate screen
   - Operator confirms / merges / splits

4. Walk axis: screens (iterative)
   For each screen:
     NARRATIVE  ask "what does the user see on this screen? what regions? what's
                most prominent? what state can it be in (loading / empty / error /
                success)?"
     EXTRACT    propose an SVG wireframe (grey-screen — boxes, labels, no
                styling); generate the .notes.md alongside (interactions, states)
     CONFIRM    operator opens the wireframe in browser (jig prints local URL),
                approves OR describes changes in conversation
     ITERATE    revise SVG based on operator feedback (small loop)
     APPEND     save approved SVG + notes; update screens.yaml roster
     LOOP       next screen

5. After all screens are wireframed:
   - Coverage check: every L1 journey touches at least one wireframed screen?
     Every L3 capability that's user-visible has a screen?
   - Operator declares "VD done for now."
```

The browser index page is regenerated after each wireframe APPEND so the operator can keep a tab open and refresh to see
the running set.

## Frontend architecture (VD owns this)

VD picks the frontend stack — that's why VD is the architect for the frontend, not just the visual designer. SA's
opinions about backend frameworks are orthogonal; SA may dictate whether the backend renders server-side HTML or returns
JSON, but the frontend technology choice belongs to VD.

**Default stack — minimal / framework-less:**

```yaml
# .jig/design/frontend.yaml — defaults; operator can override
spec_version: 1
source: default                    # default | operator_override
selected_at: 2026-05-01T18:00:00Z

stack:
  rendering: server-side           # server-side | spa | hybrid
  templating: html-with-htmx       # how the server renders
  interactivity: alpinejs          # client-side reactivity primitive
  styling: custom-css-with-tokens  # references design system tokens directly via CSS custom properties
  build: none                      # no JS bundler by default; static asset pipeline only
  package_manager: none            # for projects that genuinely don't need npm

components:
  pattern: html-templates          # html-templates | web-components | framework-components
  organization: per-screen         # per-screen | per-feature | flat

accessibility:
  target: wcag-aa                  # wcag-a | wcag-aa | wcag-aaa
  enforced_at: final               # final | mvp | bones

rationale: |
  Default to minimal because (1) less abstraction is easier for agents to reason about — they see the actual DOM,
  not a virtual one; (2) HTMX + Alpine handle ~80% of UI needs without a build pipeline; (3) tenet 1 — bite-sized
  work is easier when there's less framework machinery to track; (4) the resulting code is portable, debuggable,
  and not locked to a transient framework version.
```

**Why minimal default:**

- **Agent reasoning load**: a vanilla HTML element with HTMX attributes is one thing the agent has to think about. A
  React component with hooks, state, useEffect, and Tailwind classes is six interleaved things. Agents do better on the
  smaller surface.
- **Tenet 1 (bite-sized)**: less framework means less context the dev agent needs about framework conventions, fewer
  architectural calls during a ticket, smaller per-ticket scope.
- **Tenet 4 (structured)**: HTML and CSS are themselves structured languages with established conventions. We don't need
  a framework's virtual DOM to add structure — the actual DOM already has it.
- **Operator override is real and respected**: existing teams with React expertise, projects with rich client-side state
  needs, or codebases that already use a framework — operator overrides during step 1 of VD discovery and the override
  is honored throughout.

**Operator override path:**

When the operator says "we use React" (or Svelte, Vue, SwiftUI, Jetpack Compose, etc.), VD captures the override:

```yaml
# .jig/design/frontend.yaml — operator override
spec_version: 1
source: operator_override
selected_at: 2026-05-01T18:00:00Z
override_rationale: "Existing team has 5 years React expertise; greenfield rewrite would lose that."

stack:
  rendering: spa
  framework: react
  framework_version: "19"
  state_management: tanstack-query + zustand
  styling: tailwindcss
  build: vite
  package_manager: bun

components:
  pattern: framework-components
  organization: per-feature
  library_path: src/components

accessibility:
  target: wcag-aa
  enforced_at: final
```

The override is recorded once; subsequent VD passes and dev tickets honor it. Re-overriding later is a real change that
requires re-confirming and may stale-flag existing wireframe-to-implementation mappings.

**What the dev agent gets:**

When a UI ticket is dispatched, the orchestrator includes `frontend.yaml` in the auto-injected context so the dev agent
knows which stack to implement against. This is part of the per-ticket reference resolution that already covers
wireframes and design system.

## SVG wireframe format

Wireframes are deliberately structural, not stylistic. The SVG primitives:

- **Regions**: outlined rectangles labeling layout zones (header, sidebar, main, footer, modal).
- **Content placeholders**: gray rectangles for images, lines of `█████` for text blocks of approximate length.
- **Interactive elements**: outlined shapes labeled with role + identifier (`[Button: submit-order]`, `[Input: email]`,
  `[Link: forgot-password]`).
- **Hierarchy markers**: visual weight via stroke thickness / size, not color.
- **State indicators**: separate SVG layers for loading / empty / error / success states; layer toggling in the index
  page.
- **Annotations**: text labels for behaviors that aren't visually obvious ("on click → navigate to /orders/:id").

What's NOT in a wireframe:

- Real colors (other than gray-scale for hierarchy)
- Final typography (placeholder text only)
- Brand identity (logos, illustrations)
- Pixel-perfect spacing
- Real content

The wireframe is the *what* of the visual layer; the design system is the *how*. Implementation combines both.

A minimal example for a "post a job" screen:

```svg
<!-- .jig/design/wireframes/post-a-job.svg -->
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 600" data-screen-id="post-a-job">
  <rect class="region" x="0" y="0" width="800" height="60" stroke="#888" fill="#eee"/>
  <text x="20" y="38" font-size="14">Header</text>

  <rect class="region" x="0" y="60" width="800" height="540" stroke="#888" fill="none"/>
  <text x="20" y="88" font-size="12">Main content</text>

  <text x="40" y="120" font-size="16">Post a job</text>

  <rect class="input" x="40" y="140" width="400" height="32" stroke="#444" fill="none"/>
  <text x="48" y="160" font-size="11">[Input: title]</text>

  <rect class="input" x="40" y="190" width="720" height="120" stroke="#444" fill="none"/>
  <text x="48" y="210" font-size="11">[TextArea: description]</text>

  <rect class="button" x="40" y="340" width="120" height="40" stroke="#222" fill="#ddd"/>
  <text x="62" y="365" font-size="13">[Button: post-job]</text>

  <text x="40" y="420" font-size="11" font-style="italic">
    On click: validates title + description; on success → navigates to /jobs/:id
  </text>
</svg>
```

Plus a sibling notes file:

```markdown
<!-- .jig/design/wireframes/post-a-job.notes.md -->
# post-a-job — interaction notes

## States
- default: empty form, post button enabled
- submitting: post button shows spinner, form fields disabled
- error: inline validation messages under each field
- success: redirects (no separate state on this screen)

## Behaviors
- Title required, max 100 chars
- Description required, max 5000 chars
- On submit: POST to /api/jobs (per SA contract `jobs-create-api`)

## Cross-references
- L1 journey: j-merchant-posts-job
- Capabilities: post-a-job, validate-job-content
```

## Browser-based viewing

The TUI is text-only; the wireframes need a visual surface. Solution: jig generates a static HTML index page that embeds
all SVGs, organized by suite + screen. Operator opens the file in their browser (or runs `jig --print "/design serve"`
which spawns a local HTTP server and opens it).

```html
<!-- .jig/design/wireframes/index.html — generated -->
<!doctype html>
<html><head><title>jig wireframes — &lt;project&gt;</title>
  <style>
    body { font-family: sans-serif; max-width: 1200px; margin: 0 auto; }
    .screen { border: 1px solid #ccc; margin: 24px 0; padding: 16px; }
    .screen svg { width: 100%; height: auto; max-height: 600px; }
    .state-toggle { /* state-layer toggling controls */ }
  </style>
</head><body>
  <h1>jig-search — wireframes (revision 3)</h1>
  <nav>
    <a href="#suite-onboarding">Onboarding (4 screens)</a>
    <a href="#suite-catalog">Catalog (6 screens)</a>
    ...
  </nav>
  <section id="suite-onboarding">
    <h2>Onboarding</h2>
    <article id="screen-signup" class="screen">
      <h3>Sign up</h3>
      <p>Journey: j-merchant-onboarding · Capability: self-serve-signup</p>
      <object data="signup.svg" type="image/svg+xml"></object>
      <details><summary>Interaction notes</summary>
        <!-- markdown-rendered notes -->
      </details>
    </article>
    ...
  </section>
</body></html>
```

The index is regenerated whenever a wireframe is added/updated. State-layer toggling and side-by-side comparison
(wireframe vs implementation screenshot at review time) come later.

## Design system integration

VD's second tier is the design system: tokens, components, brand. Two paths:

**Path A — leverage Anthropic-provided design tooling ("Claude Design") where available:**

- VD discovery prompts operator: "want to author the design system in Claude Design, or skip / supply your own?"
- If operator chooses Claude Design: jig provides the project's wireframe set + brand notes; operator authors a design
  system in the external tool; jig consumes the export back into `.jig/design/system/`.
- Records the source in `.jig/design/system/source.yaml` so subsequent passes know where authoritative changes live.

**Path B — operator-supplied:**

- Operator drops `tokens.yaml` (or any existing format that VD recognizes — Style Dictionary, Tailwind config, CSS
  custom properties, etc.) into `.jig/design/system/`.
- VD reads and normalizes into its internal representation.

**Path C — defaults (always-applied baseline):**

- VD applies a minimal default token set (neutral palette, system fonts, 8pt spacing scale) and standard component specs
  **at the moment VD discovery starts**, before any operator interaction. This guarantees wireframes always render
  against a real design system.
- Marks the design system as `source: default`.
- `default` is a **permanent valid state**, not a placeholder. A project can ship through Final on default tokens if the
  operator never customizes — design system customization is a refinement, not a gate.
- Operator can switch to Path A or Path B at any point; switching emits a `DesignSystemImported` analytics event and
  stale-flags any design-system-dependent visual review on already-shipped UI.

The design system schema (regardless of source):

```yaml
# .jig/design/system/tokens.yaml
spec_version: 1
source: claude_design                  # claude_design | operator_supplied | default
imported_from: <uri>                   # for claude_design and operator_supplied
imported_at: 2026-05-01T18:00:00Z

color:
  primary: "#0a66c2"
  primary_hover: "#084a8e"
  text: "#1a1a1a"
  text_muted: "#6a6a6a"
  background: "#ffffff"
  surface: "#f5f5f5"
  error: "#c0392b"
  success: "#27ae60"
  pii: { encrypt: true }              # cross-cutting policy hook

typography:
  font_family_sans: "Inter, system-ui, sans-serif"
  font_family_mono: "Berkeley Mono, ui-monospace, monospace"
  scale:
    xs: 12px
    sm: 14px
    base: 16px
    lg: 20px
    xl: 28px

spacing:
  scale: [0, 4, 8, 12, 16, 24, 32, 48, 64]
  unit: px

radius:
  sm: 4px
  md: 8px
  lg: 16px

# .jig/design/system/components.yaml
components:
  - id: button
    variants: [primary, secondary, ghost, destructive]
    sizes: [sm, md, lg]
    states: [default, hover, active, disabled, loading]
    a11y_role: button
  - id: input
    variants: [text, email, password, number]
    states: [default, focus, error, disabled]
    a11y_required_attrs: [aria-label or label-for]
```

## Per-ticket reference resolution

When PM generates the build plan, UI tickets gain visual context fields:

```yaml
# in build-plan.yaml or per-ticket spec
type: standard
suite_id: onboarding
capability_ids: [self-serve-signup]
visual_references:
  wireframes:
    - .jig/design/wireframes/signup.svg
    - .jig/design/wireframes/signup.notes.md
  design_system:
    - .jig/design/system/tokens.yaml
    - .jig/design/system/components.yaml
  brand: .jig/design/system/brand.md     # only when relevant
```

The orchestrator at agent-spawn time resolves these into actual context injection (the agent gets the SVG and notes in
its prompt; if vision is needed, uses a vision-capable model).

Tenet 2 still applies — only the wireframe(s) for the screen(s) the ticket touches; not the entire wireframe set. The
build plan's visual_references field is what scopes this.

## VD checklist (over-specify lever)

Same pattern as the SA checklist. For each screen, the VD agent MUST address:

- **Layout structure**: header / sidebar / main / footer regions present? Grid / flex direction?
- **Content hierarchy**: what's most prominent? What's secondary? What's tertiary?
- **Interactive elements**: every action labeled? Every input labeled?
- **States**: default, loading, empty, error, success — which apply, what does each look like?
- **Cross-screen consistency**: which other screens does this share components with? (Drives later component-library
  pressure.)
- **Behaviors**: what triggers what? Where does each action lead?
- **Accessibility considerations**: keyboard navigation order? ARIA roles? Color-contrast-only-affordance check?
- **Responsive considerations**: how does this layout collapse at narrow widths? (Can defer to MVP if not yet decided.)

Silence on any category is a bug — same discipline as SA. Forces VD agent to produce thorough wireframes rather than
sketch-and-move-on.

## What a normal VD pass produces

For a medium-sized UI project (3-5 suites, 15-25 capabilities, ~10-25 user-facing screens):

**Wireframe stage:**
- **10-25 SVG wireframes** — one per screen, plus state-layer variants
- **One `.notes.md` per wireframe** with interaction + state notes
- **`screens.yaml` roster** mapping screen-id → journey ids → capability ids → suite
- **`index.html`** auto-generated for browser viewing
- **Coverage check passing**: every L1 journey touches ≥ 1 wireframed screen; every user-facing capability has ≥ 1
  screen

**Design system stage (when not deferred to MVP):**
- **`tokens.yaml`** — color, typography, spacing, radius (50-150 tokens typical)
- **`components.yaml`** — 8-20 component specs covering project's common UI primitives
- **`brand.md`** — voice, palette guidance, usage notes
- **`source.yaml`** — provenance (Claude Design / operator-supplied / default)

**Sizing notes:**
- Backend-only project: `screens.yaml` is empty; wireframes/ is empty; design system is N/A. VD discovery exits in one
  step ("no user-facing screens").
- A project with 50+ screens almost certainly groups screens by suite — VD walks suite-by-suite rather than
  screen-by-screen for tractability.
- Dashboard / admin tools tend to have many screens with shared layout — component library pressure shows up earlier;
  design-system stage is more important than for content-heavy sites.

## Visual review (interfacing with PM)

A new reviewer type joins the federation: **`visual_compliance`**. Runs on UI tickets at the diff-review step.

Inputs:
- The wireframe(s) the ticket implements (from `visual_references.wireframes`)
- A screenshot of the implementation (rendered locally during the review pass — typically by booting the dev environment
  service that serves the UI, navigating to the screen, capturing)
- The design system (for tokens / component checks)

Output (structured comments per the PM design):
- Layout divergence: "the form is left-aligned but the wireframe shows centered"
- Component misuse: "this is a div+onclick but should be a Button per components.yaml"
- Token violation: "color #2a4f8c isn't in tokens.yaml — closest match is `primary`"
- State coverage gap: "wireframe specifies an empty state; implementation doesn't render one"
- Accessibility violation: "Button has no accessible name"

Severity tiers per PM design:
- **Critical**: layout entirely wrong, missing required state, accessibility violation that blocks WCAG AA
- **Important**: component misuse, token violation, missing minor state
- **Notable**: spacing slight off, color slightly off, hierarchy emphasis differs

Tier of the reviewer agent itself: senior (uses vision; needs to reason about layout). For projects with no design
system (defaults active), runs in a permissive mode that flags only Critical.

Detailed reviewer-side mechanics live in the PM workflow doc; this section names what the visual reviewer is and what it
consumes.

## Iteration model

Same push/pull pattern as SA:

**Push (downward) — operator changes upstream:**

1. New journey added at L1 → may imply new screens.
2. Cascades to L3 → suite brief updated.
3. Triggers VD delta pass → new wireframe(s) authored.
4. If new wireframe references components/tokens not yet in the design system, flags a gap.
5. Operator confirms.

**Pull (upward) — dev agent surfaces a gap:**

1. Dev agent on a UI ticket realizes the wireframe doesn't specify some interaction or state.
2. Posts a structured "wireframe gap" comment.
3. VD agent fires, amends the wireframe (or notes file), operator confirms.
4. Dev resumes.

**External update — operator changes design tool:**

- Operator updates tokens in Claude Design (or wherever).
- `/design reimport` slash command pulls the latest export, validates, presents a diff to the operator.
- Affected wireframes / tickets get marked as design-stale; PM re-plan trigger fires.

## Bones / MVP / Final layering

VD's output gets consumed differently at each build-plan layer:

| Layer | What gets implemented from VD | Visual review strictness |
|---|---|---|
| **Bones** | Wireframe structure only — happy path screen renders with placeholder content; default tokens (no design system applied yet); no state coverage; no error handling. | Permissive: layout matches wireframe at coarse granularity. |
| **MVP** | Wireframes + design system applied; critical states (default, error, success); core capability behaviors. | Standard: layout + token + component matches. State coverage for non-trivial states. |
| **Final** | All states; responsive breakpoints; accessibility (WCAG AA); polish; animation/transitions if specified. | Strict: full reviewer set runs; visual_compliance + accessibility-aware checks. |

This means VD doesn't have to be "complete" before bones starts — wireframes are enough to bones-layer the system. The
design system can land at the MVP-gate.

## Risks (of this design)

- **Wireframe authoring is heavy.** 25 screens × multiple iterations per screen × operator confirmation in browser could
  add days to project setup. Mitigation: agent-generated initial draft per screen (operator iterates, doesn't author
  from scratch); browser viewing is fast (HTML index); VD agent uses operator's existing references (mood-board files in
  `.jig/design/references/`) to bias the initial draft.
- **SVG generation quality.** Vision models are better at *reading* SVG than *writing* it; agent-generated wireframes
  may be ugly or structurally wrong. Mitigation: SVG output validated against schema; problematic SVGs surface to
  operator with a "draft is rough — describe the corrections" flow rather than silent.
- **Visual review false positives.** Vision-based diff is fuzzy; reviewer flags acceptable variation as divergence.
  Mitigation: configurable sensitivity; operator override always available; default permissive at bones, strict at
  final.
- **Design system bloat.** tokens.yaml grows to 500 entries; components.yaml has 80 components nobody uses. Mitigation:
  usage tracking (which tokens / components are actually referenced by implementation); periodic prune surfacing unused
  entries.
- **Browser companion friction.** Operator has to leave the TUI to view wireframes. Mitigation: jig prints the local URL
  with each VD update; Claude can open browser pages on the operator's behalf where it has tooling for that. Long-term:
  a TUI affordance that shows a thumbnail, with click-to-open-browser.
- **Wireframe-implementation drift.** Operator approves wireframe, dev implements, but operator's internal vision
  shifted in between. Mitigation: visual_compliance review surfaces divergence; if operator says "actually I changed my
  mind," that's a wireframe amendment that triggers VD delta.

## Out of scope

- Generating final implementation code from wireframes alone (we have separate dev tickets for that).
- Pixel-perfect implementation enforcement.
- Animation / motion / transition design as a discipline.
- Brand identity work (logo design, illustration).
- Marketing-quality design system documentation (Storybook-as-deliverable). Internal `tokens.yaml` + `components.yaml`
  is enough for implementation.
- Multi-brand / theme switching (single design system per project for v1).
- Interactive prototyping (wireframes are static SVG, not clickable prototypes).
- Replacing external design tools. Figma / Sketch / Penpot / Excalidraw stay in the operator's hands; jig consumes
  exports.

## Open questions

Continued from `problem.md`:

- **Source-of-truth and re-import** mechanics — operator-confirmed `/design reimport` ritual is the v1 answer; tighter
  integration is per-tool and deferred.
- **Operator-supplied SVG override** — when the operator wants to drop in their own SVG instead of accepting the agent's
  draft. Probably yes; needs a confirmation flow.
- **Visual review sensitivity** — strict / loose / configurable. Default loose at bones, strict at final; per-project
  override via `.jig/design/system/review_sensitivity.yaml`.
- **Claude Design integration mechanics** — API shape, export formats. Will land per Anthropic's product release; jig
  reserves the integration point in v1.

New ones surfaced during design:

- **Component library generation as a downstream output.** Should the dev agents generating UI code emit their
  components in a way that builds the project's actual component library, so the design-system YAML stays in sync with
  code? Probably yes but mechanism TBD.
- **Cross-suite screen sharing.** A screen that's used in multiple journeys / suites. Owned by which suite? Probably the
  L2 PO call but VD needs to handle it gracefully.
- **Mobile vs desktop.** Multiple wireframes per screen (one per breakpoint) or one wireframe with responsive notes?
  Probably the latter for v1; per-breakpoint at v2.

## Implementation phases

1. **Schema** — `screens.yaml`, `tokens.yaml`, `components.yaml` Pydantic models. SVG validation. URI scheme extension
   for `project://design/...`.
2. **VD agent** — role config, MCP tools (`vd_propose_screen_roster`, `vd_add_wireframe`, `vd_add_state_layer`,
   `vd_finalize_wireframes`, `vd_import_design_system`), checklist-driven prompt.
3. **Browser index generator** — HTML + embedded SVG + state-layer toggling. Triggered after each wireframe APPEND.
   `/design serve` command for local HTTP server when file:// doesn't suffice.
4. **Per-ticket reference resolution** — orchestrator wires `visual_references` into the agent context at spawn.
5. **Design system import paths** — Claude Design integration when available; operator-supplied (read existing
   tokens.yaml from any source); defaults (minimal token set).
6. **`visual_compliance` reviewer agent** — vision-based diff between implementation screenshot and wireframe; tier
   senior; integrated into the PM federation.
7. **Bones/MVP/final integration** — visual review sensitivity per layer; design system permitted-deferred-to-MVP
   semantics.
8. **TUI affordances** — `/design wireframes`, `/design serve`, `/design reimport`, `/vd open-questions`, `/vd
   review-coverage`.
9. **Iteration paths** — push-cascade on PO journey changes; pull-escalation on dev wireframe-gap reports.
10. **Sweepers** — design-system usage tracker (which tokens/components are actually used); stale-wireframe detector.

## Change log

- 2026-05-01: Initial design (brent + claude). VD as a new role parallel to SA. Two-tier output (wireframes first;
  design system second). SVG wireframes viewable in browser via generated HTML index. Design system via Claude Design
  integration / operator-supplied / minimal defaults. Visual review enters PM federation as `visual_compliance` reviewer
  type. Bones / MVP / Final layering applies — wireframes alone enough for bones.
- 2026-05-01: VD scope expanded — VD is now the architect for the frontend, not just visual designer. Added third tier
  (`VD-frontend-arch`) for frontend stack / build / component pattern decisions. Default stack: HTMX + Alpine.js +
  custom CSS with CSS custom properties referencing tokens. Operator override path documented. SA design updated to
  clarify SA owns backend technical shape, VD owns frontend technical shape. Design system reframed as always-present
  (defaults applied at VD discovery start, not deferred to MVP); `default` is a permanent valid state rather than a
  placeholder. VD discovery loop reordered: foundational decisions (frontend arch + design-system source) first, then
  iterative wireframe walk.
