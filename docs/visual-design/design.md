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
3. **Wireframes** (grey-screens): one HTML file per screen, structural-only — semantic elements, region markers,
   affordance labels, no real styling beyond the shared `wireframe.css`. Covers *every screen implied by L1 journeys*
   before any UI implementation begins. The wireframe HTML is also the starting code for the bones-layer implementation
   — additive transition, not throw-away.

The first two are *foundational* (set once at the start of VD discovery, modified rarely); the third is *iterative*
(walked screen-by-screen with the operator).

Browser-based viewing throughout: each wireframe is itself an HTML file rendered natively by the browser; jig generates
a thin `index.html` listing all of them via `<iframe>` embeds with state-toggle controls. Operator opens the index
locally to review. (Claude already knows how to open browser pages, so the operator never has to leave the
conversational flow for long.)

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

| Tier                 | Artifact                                                                                              | What's there                                                                                                                                   | "Done" means                                                                                                                                                                                                                  |
|----------------------|-------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **VD-frontend-arch** | `.jig/design/frontend.yaml`                                                                           | Stack choice (HTMX + Alpine + custom CSS by default), build tooling, code organization, component implementation pattern, accessibility target | Operator confirms (defaults applied if operator skips). One pass at start of VD discovery; rare changes thereafter.                                                                                                           |
| **VD-system**        | `.jig/design/system/tokens.yaml`, `.jig/design/system/components.yaml`, `.jig/design/system/brand.md` | Design tokens (color, spacing, typography), component specs (Button states, Input variants), brand voice + palette                             | **Always present** — defaults applied immediately so wireframes have something to render against; operator can replace via Claude Design or supply their own export. "Default" is a permanent valid state, not a placeholder. |
| **VD-wireframes**    | `.jig/design/wireframes/<screen-id>.html` + `<screen-id>.notes.md` per screen + shared `wireframe.css` + `index.html` | Grey-styled HTML per screen — semantic structure, region markers, affordance labels, state-variant markup via Alpine. Sidecar notes carry behaviors + state descriptions + cross-references. | Every screen implied by L1 journeys has a wireframe; operator confirms "all screens present" gate via the browser index. |

The three-tier stack: frontend architecture and design system are *foundational* (set once, modified rarely); wireframes
are *iterative* (one per screen, walked with the operator). Wireframes always render against a real design system from
the start because the default is always there.

## Artifacts on disk

```
.jig/
  design/
    frontend.yaml                    Stack choice, build tooling, code organization (VD-frontend-arch tier)
    wireframes/
      index.html                     Browser-viewable index — iframes the per-screen HTMLs with state-toggle controls
      wireframe.css                  Project-level stylesheet that grey-box styles every wireframe
      <screen-id>.html               One HTML wireframe per screen — semantic structure + affordance labels
      <screen-id>.notes.md           Per-screen interaction notes, state coverage, cross-references
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
   - **Read `.jig/design/references/` if non-empty** — operator-supplied mood boards, screenshots, sketches,
     competitor screenshots, brand guides. These bias initial wireframe drafts; operators with strong opinions
     converge faster because EXTRACT proposes something already in their mental model.
   - Propose initial screen list: every "the user does X on a screen" → a candidate screen
   - Operator confirms / merges / splits

4. Walk axis: screens (iterative)
   For each screen:
     NARRATIVE  ask "what does the user see on this screen? what regions? what's
                most prominent? what state can it be in (loading / empty / error /
                success)?"
     EXTRACT    propose an HTML wireframe (semantic elements, grey-box styling
                via wireframe.css, affordance labels, region markers); generate
                the .notes.md alongside (interactions, states).
                If references/ has relevant material for this screen (a screenshot
                of a similar competitor flow, a sketched layout the operator drew),
                use it as the structural starting point and adapt to the operator's
                narrative.
                If references/ is empty, propose from journey + screen narrative
                alone — agent is making the first draft so operator has something
                to react to rather than author from scratch.
                Run wireframe_lint after writing; fix any violations before CONFIRM.
     CONFIRM    operator opens the wireframe in browser (jig prints local URL),
                approves OR describes changes in conversation
     ITERATE    revise HTML based on operator feedback (small loop)
     APPEND     save approved HTML + notes; update screens.yaml roster
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

## Wireframe format — HTML wireframes (continuous with implementation)

**Wireframes are HTML files, not SVG.** Two reasons together carry the decision:

- **Agents write HTML well.** SVG generation has known failure modes (coordinate math, viewBox, stroke drift,
  accessibility omissions). HTML doesn't — agents have orders-of-magnitude more training on HTML than on SVG, and layout
  is handed to the browser rather than computed by the agent. The original "agents read SVG better than they write it"
  risk goes away.
- **The wireframe IS the starting code for the bones-layer implementation.** Same HTML file. Bones layer swaps the
  grey-box `wireframe.css` for the real design-system CSS, replaces placeholder text with real content, adds HTMX
  attributes for server-driven interactions and Alpine for client-side state. The wireframe-to-implementation transition
  is *additive*, not throw-away. Dev agent doesn't translate a sketch into code; they continue from where VD left off.

This fits the rest of the VD design directly. The default frontend stack stays HTMX + Alpine + custom CSS with CSS
custom properties (no build step) — see Frontend architecture section above. Wireframes use a small static utility CSS
layer with **Tailwind-shaped names** so agents can lean on their massive training on Tailwind conventions without anyone
needing to run a Tailwind build. Same utility classes carry through to the bones-layer implementation; only the CSS
custom property values driving them change.

### Canonical HTML wireframe — semantic HTML + utility CSS with Tailwind-shaped names

The agent uses standard semantic HTML for structure and a small set of utility classes for layout / spacing / sizing.
The class names match Tailwind conventions (`flex`, `gap-4`, `p-6`, `text-lg`, `border`) — agents have massive training
on them — but they're served by a static `wireframe.css` written once at VD discovery start. No build step, no compiler,
no node toolchain.

```html
<!-- .jig/design/wireframes/post-a-job.html — canonical, agent-authored directly -->
<!doctype html>
<html lang="en" data-screen-id="post-a-job">
<head>
  <meta charset="utf-8">
  <title>Wireframe — post a job</title>
  <link rel="stylesheet" href="../wireframe.css">
  <script src="https://cdn.jsdelivr.net/npm/alpinejs@3" defer></script>
</head>
<body class="min-h-screen flex flex-col" x-data="{state: 'default'}">

  <header class="border-b p-4">
    <small>Header</small>
  </header>

  <main class="container mx-auto max-w-2xl p-6 flex-1">
    <h1 class="text-2xl font-semibold mb-4">Post a job</h1>

    <form class="flex flex-col gap-4" x-show="state === 'default'">
      <label class="flex flex-col gap-1">
        <small>Title</small>
        <input type="text" name="title" class="border p-2" disabled>
      </label>

      <label class="flex flex-col gap-1">
        <small>Description</small>
        <textarea name="description" class="border p-2 h-32" disabled></textarea>
      </label>

      <button type="submit" class="border px-4 py-2 self-start" disabled>Post job</button>
    </form>

    <p x-show="state === 'submitting'" class="italic">Submitting…</p>
    <p x-show="state === 'error'" class="italic">Validation errors shown inline under each field</p>

    <p class="mt-6"><small>On click: validates title + description; on success → navigates to /jobs/:id</small></p>
  </main>

</body>
</html>
```

What makes this a *wireframe* and not a stylized page:

- **`wireframe.css` carries greyscale CSS variables.** The utility classes use `var(--color-primary)`, `var(--space-4)`,
  etc.; in wireframe mode all colors resolve to greys, all fonts to monospace. Agents write `class="bg-primary
  text-on-primary"` (familiar Tailwind-shape names); the visual outcome is greyscale because of the variable values, not
  because of class names.
- **Standard semantic HTML for structure** — `<header>`, `<main>`, `<form>`, `<button>`, `<small>`. No jig-specific
  markup.
- **`disabled` on form controls** so the wireframe doesn't accept input. This isn't a working form; it's a sketch.
- **Alpine `x-data` + `x-show`** for state-variant toggling. Same primitive that's the default in `frontend.yaml`; the
  browser index page provides controls to flip states.

### What the agent uses

The agent's vocabulary is "standard semantic HTML5 + the small utility-class set defined in `wireframe.css`":

- **Any semantic HTML5 element** — `<header>`, `<main>`, `<aside>`, `<footer>`, `<nav>`, `<section>`, `<article>`,
  `<dialog>`, `<form>`, `<fieldset>`, `<label>`, `<input>`, `<textarea>`, `<select>`, `<button>`, `<a>`, `<h1>`-`<h6>`,
  `<p>`, `<ul>`/`<ol>`/`<li>`, `<table>`, `<small>`, `<strong>`, `<em>`. Custom elements only if declared in
  `frontend.yaml`'s component spec.
- **Utility classes** — Tailwind-shaped names covering ~200-300 common patterns: layout (`flex`, `grid`, `gap-*`, `p-*`,
  `m-*`, `w-*`, `h-*`, `min-h-screen`, `max-w-*`, `container`, `mx-auto`, `flex-col`, `flex-row`, `items-*`,
  `justify-*`), typography (`text-xs/sm/base/lg/xl/2xl`, `font-medium/semibold/bold`, `italic`, `underline`,
  `text-left/center/right`), borders (`border`, `border-{t,r,b,l}`, `border-2`, `rounded`, `rounded-md`), color
  utilities driven by tokens (`bg-primary`, `bg-surface`, `text-primary`, `text-muted`, `border-default`).
- **Alpine for state** — `x-data="{state: '...'}"` on `<body>`, `x-show="state === '...'"` on state-variant elements.
  Permitted Alpine surface at wireframe stage limited to `x-data` and `x-show`.
- **Form controls always `disabled`** — wireframe forms don't accept input.
- **Single Alpine.js script tag in `<head>`** — no other `<script>` permitted.

What's prohibited (wireframe-HTML linter rejects with a structured comment):

- `style=` inline attributes
- Real color hexes anywhere outside `wireframe.css` (`/#[0-9a-fA-F]{3,8}/`)
- `<img src=>` with a real path (use a `<div>` with placeholder utility classes for image regions)
- `<script>` tags except the Alpine include
- Utility class names not in `wireframe.css`'s defined set (the linter has the canonical list)
- Alpine directives beyond `x-data` and `x-show`
- Custom HTML elements not in `frontend.yaml`

### The wireframe.css — static utility layer + greyscale tokens

A single hand-written stylesheet does the work — generated once at VD discovery start; agents don't edit it. Roughly
200-300 lines covering the most-used Tailwind utility names with implementations driven by CSS custom properties.
Variables drive colors, spacing, typography; the utility classes are stable, the variables shift between wireframe mode
and bones mode.

```css
/* .jig/design/wireframe.css — generated once at VD start */

/* Greyscale token values — what makes this "wireframe mode" */
:root {
  --color-bg: #f5f5f5;
  --color-surface: #fff;
  --color-primary: #888;
  --color-on-primary: #fff;
  --color-default: #2a2a2a;
  --color-muted: #888;
  --color-border: #888;
  --color-border-strong: #444;

  --font-mono: ui-monospace, 'SF Mono', monospace;
  --font-sans: var(--font-mono);   /* greyscale mode collapses sans → mono */

  --space-1: 4px; --space-2: 8px; --space-3: 12px; --space-4: 16px;
  --space-6: 24px; --space-8: 32px; --space-12: 48px;

  --text-xs: 11px; --text-sm: 13px; --text-base: 15px; --text-lg: 18px;
  --text-xl: 24px; --text-2xl: 32px;

  --radius-default: 0;             /* greyscale mode: no radius */
  --shadow-default: none;          /* greyscale mode: no shadow */
}

/* Reset + base */
* { box-sizing: border-box; font-family: var(--font-mono); color: var(--color-default); }
body { margin: 0; background: var(--color-bg); }
small, .text-sm { font-size: var(--text-sm); color: var(--color-muted); }
input, textarea, select, button { font: inherit; color: inherit; }
input:disabled, textarea:disabled, select:disabled, button:disabled { opacity: 1; cursor: default; }

/* Color utilities — read from variables */
.bg-primary { background: var(--color-primary); }
.bg-surface { background: var(--color-surface); }
.text-on-primary { color: var(--color-on-primary); }
.text-muted { color: var(--color-muted); }
.text-default { color: var(--color-default); }

/* Layout utilities — Tailwind-shape names */
.flex { display: flex; }
.flex-row { display: flex; flex-direction: row; }
.flex-col { display: flex; flex-direction: column; }
.flex-1 { flex: 1; }
.grid { display: grid; }
.items-center { align-items: center; }
.items-start { align-items: flex-start; }
.justify-between { justify-content: space-between; }
.gap-1 { gap: var(--space-1); } .gap-2 { gap: var(--space-2); }
.gap-3 { gap: var(--space-3); } .gap-4 { gap: var(--space-4); } .gap-6 { gap: var(--space-6); }

/* Spacing — full Tailwind name set for the most-used scales */
.p-1 { padding: var(--space-1); } .p-2 { padding: var(--space-2); }
.p-3 { padding: var(--space-3); } .p-4 { padding: var(--space-4); } .p-6 { padding: var(--space-6); }
.px-2 { padding-left: var(--space-2); padding-right: var(--space-2); }
.px-4 { padding-left: var(--space-4); padding-right: var(--space-4); }
.py-2 { padding-top: var(--space-2); padding-bottom: var(--space-2); }
.mb-2 { margin-bottom: var(--space-2); } .mb-4 { margin-bottom: var(--space-4); } .mb-6 { margin-bottom: var(--space-6); }
.mt-4 { margin-top: var(--space-4); } .mt-6 { margin-top: var(--space-6); }

/* Sizing */
.w-full { width: 100%; } .h-full { height: 100%; }
.min-h-screen { min-height: 100vh; }
.max-w-md { max-width: 28rem; } .max-w-2xl { max-width: 42rem; } .max-w-4xl { max-width: 56rem; }
.h-32 { height: 8rem; }
.container { width: 100%; }
.mx-auto { margin-left: auto; margin-right: auto; }

/* Typography */
.text-xs { font-size: var(--text-xs); } .text-sm { font-size: var(--text-sm); }
.text-base { font-size: var(--text-base); } .text-lg { font-size: var(--text-lg); }
.text-xl { font-size: var(--text-xl); } .text-2xl { font-size: var(--text-2xl); }
.font-medium { font-weight: 500; } .font-semibold { font-weight: 600; } .font-bold { font-weight: 700; }
.italic { font-style: italic; } .underline { text-decoration: underline; }

/* Borders */
.border { border: 1px solid var(--color-border); }
.border-b { border-bottom: 1px solid var(--color-border); }
.border-t { border-top: 1px solid var(--color-border); }
.border-strong { border-color: var(--color-border-strong); }
.rounded { border-radius: var(--radius-default); }

/* Element-default styling for sketched look */
header, main, aside, footer, nav, section, article {
  outline: 1px dashed var(--color-border); background: var(--color-surface); padding: var(--space-4);
}
input, textarea, select { border: 1px solid var(--color-border-strong); background: var(--color-surface); padding: var(--space-2); }
button { border: 1px solid var(--color-border-strong); background: var(--color-surface); padding: var(--space-2) var(--space-4); }
.placeholder { background: repeating-linear-gradient(45deg, #eee, #eee 8px, #ddd 8px, #ddd 16px); }
.placeholder-image { aspect-ratio: 16 / 9; }
```

That's the entire styling. About 200-300 lines once filled out. Hand-written once; agents use it via the class names. No
build, no compiler, no toolchain.

### Wireframe → bones continuity

The bones-layer dev agent starts from `.jig/design/wireframes/<screen-id>.html` and:

1. **Swaps the stylesheet** — `<link rel="stylesheet" href="../wireframe.css">` becomes `<link rel="stylesheet"
   href="../system/styles.css">`. The implementation styles.css is the same utility classes with **real token values**
   (real brand colors, real fonts, real radius / shadow scales). Same class names; only the CSS custom properties
   differ.
2. **Removes the `disabled` attributes** on form controls; adds form action + HTMX attributes per SA contract.
3. **Replaces placeholder text content** with real text or `hx-get` for server-rendered content.
4. **Replaces `<small>` affordance hints** (used as labels in the wireframe) with normal labels.
5. **Adds Alpine state-machine logic** beyond the wireframe's static state-variant `x-show`s — `x-on`, `x-bind`, real
   interactivity.
6. **Renders against the design system tokens** — same utility classes, real colors and typography. Visual outcome
   matches the wireframe's structural intent at bones-layer fidelity.

The HTML structure stays. The class names stay. Only the CSS custom property values differ + a few attribute-level
changes (disabled → enabled, real text replaces placeholders). No translation step, no build step, no toolchain.

### Incremental design-system enrichment

The transition between wireframe / MVP / Final is *editing the CSS variable values* in the active stylesheet:

- **Wireframe stage:** `wireframe.css` — greyscale colors, monospace, no radius, no shadow.
- **MVP stage:** `system/styles.css` (replaces wireframe.css) — operator (or Claude Design output) provides real color
  tokens, real font tokens, real radius / shadow scales. Same HTML, same utility classes; visual outcome jumps from
  greyscale wireframe to branded MVP because the variables resolve to real values now.
- **Final stage:** styles.css extends with media-query variants for responsive breakpoints, dark-mode variants (`@media
  (prefers-color-scheme: dark)`), accessibility-driven contrast adjustments. Still the same utility class set; the
  variants add specificity, not new vocabulary.

Each stage is additive on the previous. Operator iterates the variables directly OR through Claude Design (which emits a
tokens file); jig consumes either.

### When to opt into real Tailwind

For projects that genuinely need the full Tailwind utility set (200-screen apps with extensive variants, complex
hover/focus/dark-mode combinations beyond what the static utility layer covers), operator opts in via `frontend.yaml`:

```yaml
stack:
  styling: tailwind                  # default is custom-css-with-tokens
  build: vite                        # required when styling=tailwind
  package_manager: bun
```

This trades the no-build-step default for the full Tailwind expressiveness. Wireframes still work — same Tailwind class
names — but the wireframe.css gets generated by Tailwind at the build step instead of being hand-written. The continuity
story is preserved either way; the build-step decision is the operator's call.

### Wireframe-HTML linter

A deterministic Python module — `jig/visual_design/wireframe_linter.py` — runs on every wireframe save (and as part of
the per-commit reviewer cadence for any HTML file in `.jig/design/wireframes/`). Checks the rules above mechanically.
Violations come back as structured comments the agent can address; pattern is the same as the contract-compliance
reviewer.

### Per-screen notes (sidecar markdown, retained)

Behavior notes, state descriptions, and cross-references live in `.jig/design/wireframes/<screen-id>.notes.md` alongside
the HTML — same as the original design intent. The HTML carries the structural sketch; the notes carry the
operator-readable behavior + state details. Two files per screen, one structural, one prose. (We tried inlining metadata
into the HTML; lifting it back into a sidecar is cleaner — agents read both, operators read both, neither is
overloaded.)

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

### MCP tool surface for VD agents

The agent writes HTML directly via standard file-edit tools (Write / Edit). They don't need a primitive-composition
toolkit because HTML is something they can write reliably. Two thin wrappers help:

```
wireframe_lint(screen_id)                            — run the wireframe-HTML linter; return
                                                       structured violations (or empty if clean)
wireframe_get(screen_id)                             — return both <screen-id>.html and
                                                       <screen-id>.notes.md content
wireframe_set_notes(screen_id, states, behaviors,
                    cross_references)                — structured update of the notes.md
                                                       (so notes stay parseable)
```

The HTML itself is just a file. The agent edits it like any other source file in the project. The linter is the
discipline mechanism; if the agent strays from the wireframe vocabulary, it gets back a structured "you used a forbidden
inline style on line 23" comment and fixes it. Same pattern as contract-compliance review, just at authoring time.

### Why this is better than YAML+SVG

- **No renderer to maintain.** Browser renders HTML natively; we just need `wireframe.css` + a linter.
- **Wireframe → implementation continuum.** Bones-layer dev agents continue the wireframe rather than translating it.
  Less work, fewer translation errors.
- **Agent-friendly authoring format.** Agents are good at HTML; they don't need a primitive-composition toolkit to be
  reliable.
- **Operator can hand-edit.** HTML is universally editable; YAML schemas require operator to know our schema.
- **State variants are native.** Alpine.js was already the default for client-side state in implementation; using it for
  wireframe state-toggling is consistent.
- **Native browser viewing.** No SVG generation pipeline; index.html is just a list of `<iframe src=>` or `<object
  data=>` tags pointing at the wireframe HTMLs.

## Browser-based viewing

The TUI is text-only; the wireframes need a visual surface. Solution: jig generates a static `index.html` that embeds
each per-screen wireframe HTML via `<iframe>`, organized by suite + screen, with state-toggle controls per wireframe.
Operator opens the index in their browser (or runs `jig --print "/design serve"` which spawns a local HTTP server and
opens it — needed when iframes refuse to load over `file://`).

```html
<!-- .jig/design/wireframes/index.html — generated -->
<!doctype html>
<html><head><title>jig wireframes — &lt;project&gt;</title>
  <style>
    body { font-family: sans-serif; max-width: 1400px; margin: 0 auto; }
    .screen { border: 1px solid #ccc; margin: 24px 0; padding: 16px; }
    .screen iframe { width: 100%; height: 600px; border: none; background: #f5f5f5; }
    .state-toggle { display: flex; gap: 8px; margin: 8px 0; }
    .state-toggle button[aria-pressed="true"] { background: #444; color: #fff; }
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
      <div class="state-toggle">
        <button aria-pressed="true" data-state="default">default</button>
        <button data-state="loading">loading</button>
        <button data-state="error">error</button>
      </div>
      <iframe src="signup.html" title="signup wireframe"></iframe>
      <details><summary>Interaction notes</summary>
        <!-- markdown-rendered notes from signup.notes.md -->
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
- **10-25 HTML wireframes** — one `<screen-id>.html` per screen + sidecar `<screen-id>.notes.md`; state variants as
  `data-wireframe-state` markup toggled via Alpine; all linting clean
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

| Layer     | What gets implemented from VD                                                                                                                                       | Visual review strictness                                                             |
|-----------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------|
| **Bones** | Wireframe structure only — happy path screen renders with placeholder content; default tokens (no design system applied yet); no state coverage; no error handling. | Permissive: layout matches wireframe at coarse granularity.                          |
| **MVP**   | Wireframes + design system applied; critical states (default, error, success); core capability behaviors.                                                           | Standard: layout + token + component matches. State coverage for non-trivial states. |
| **Final** | All states; responsive breakpoints; accessibility (WCAG AA); polish; animation/transitions if specified.                                                            | Strict: full reviewer set runs; visual_compliance + accessibility-aware checks.      |

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
- Multi-brand / theme switching (single design system per project for v2).
- Interactive prototyping (wireframes are static SVG, not clickable prototypes).
- Replacing external design tools. Figma / Sketch / Penpot / Excalidraw stay in the operator's hands; jig consumes
  exports.

## Open questions

Continued from `problem.md`:

- **Source-of-truth and re-import** mechanics — operator-confirmed `/design reimport` ritual is the v2 answer; tighter
  integration is per-tool and deferred.
- **Operator-supplied SVG override** — when the operator wants to drop in their own SVG instead of accepting the agent's
  draft. Probably yes; needs a confirmation flow.
- **Visual review sensitivity** — strict / loose / configurable. Default loose at bones, strict at final; per-project
  override via `.jig/design/system/review_sensitivity.yaml`.
- **Claude Design integration mechanics** — API shape, export formats. Will land per Anthropic's product release; jig
  reserves the integration point in v2.

New ones surfaced during design:

- **Component library generation as a downstream output.** Should the dev agents generating UI code emit their
  components in a way that builds the project's actual component library, so the design-system YAML stays in sync with
  code? Probably yes but mechanism TBD.
- **Cross-suite screen sharing.** A screen that's used in multiple journeys / suites. Owned by which suite? Probably the
  L2 PO call but VD needs to handle it gracefully.
- **Mobile vs desktop.** Multiple wireframes per screen (one per breakpoint) or one wireframe with responsive notes?
  Probably the latter for v2; per-breakpoint at v2.

## Implementation phases

1. **Schema** — `screens.yaml`, `tokens.yaml`, `components.yaml` Pydantic models. SVG validation. URI scheme extension
   for `project://design/...`.
2. **VD agent** — role config, MCP tools (`vd_propose_screen_roster`, `vd_add_wireframe`, `vd_add_state_layer`,
   `vd_finalize_wireframes`, `vd_import_design_system`), checklist-driven prompt.
3. **Browser index generator** — `index.html` listing each wireframe via `<iframe>` embed with state-toggle controls.
   Triggered after each wireframe APPEND. Plus `wireframe.css` (project-level shared stylesheet) and
   `wireframe_linter.py` (Python module enforcing the constrained vocabulary). `/design serve` command for local HTTP
   server when file:// doesn't suffice.
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
- 2026-05-03: Discovery loop made explicit about the operator-may-or-may-not-have-input duality (problem.md reframing).
  Step 3 (build screen roster) now reads `.jig/design/references/` if non-empty to bias drafts toward operator's
  existing material. Step 4's EXTRACT now distinguishes the two paths — references-present uses them as structural
  starting points; references-empty proposes from journey narrative alone. Either way the operator reacts rather than
  authors from scratch; the system always produces something workable.
- 2026-05-03: **Wireframe format changed from SVG to HTML.** Originally addressed the "agents read SVG better than they
  write it" risk by proposing a YAML-canonical / SVG-derived approach with a deterministic Python renderer. Reversed in
  favor of HTML wireframes: agents write HTML reliably; HTML is renderable natively in a browser; *and* the wireframe IS
  the starting code for the bones-layer implementation (continuous, additive, not throw-away). `wireframe.css` does all
  the grey-box styling; a wireframe-HTML linter enforces the constrained vocabulary (allowed elements, no inline styles,
  no real colors, etc.). State variants via Alpine `x-data` toggling — same primitive that's already the default in
  `frontend.yaml`. Two files per screen: `<screen-id>.html` (structural) and `<screen-id>.notes.md` (behavior + states +
  cross-references). MCP tool surface shrinks dramatically — agents edit HTML directly via Write/Edit; only
  `wireframe_lint` and `wireframe_get/set_notes` wrappers remain.
- 2026-05-03: **Wireframe styling refined: semantic HTML + utility CSS with Tailwind-shaped names, NOT Tailwind
  itself.** Briefly proposed a Tailwind-preset approach for greyscale wireframe mode; rolled back because Tailwind
  requires a build step (or the Play CDN, which Tailwind itself flags as dev-only) and that contradicts the
  no-build-step value the rest of the stack commits to. Synthesis: hand-written `wireframe.css` (~200-300 lines) with
  utility class names that mirror Tailwind conventions (`flex`, `gap-4`, `p-6`, `text-lg`, `border`, `bg-primary`) so
  agents can lean on their massive Tailwind training, but implemented via CSS custom properties that drive the visual
  outcome. Wireframe → bones transition is changing the CSS variable values, not swapping stylesheets — same utility
  classes throughout. Operator can opt into real Tailwind via `frontend.yaml` for projects that warrant the build-step
  trade-off; default stays build-free.
