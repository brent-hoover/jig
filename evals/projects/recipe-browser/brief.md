# Recipe Browser

A small web app that reads markdown recipe files from disk and
renders them in the browser.

## Audience

- Home cooks who keep recipes as plain markdown files in a folder
  (synced via Dropbox / git / iCloud) and want to browse them in a
  more readable shape than a text editor.
- People who don't want a "smart" recipe app — no logins, no cloud,
  no subscriptions — just their own files rendered nicely.

## Pitch

Drop markdown files into `content/recipes/`. Run the app. Open
`http://localhost:8000`. See an index of recipes; click one; read it.

```
content/recipes/
  beef-stew.md
  pancakes.md
  miso-soup.md
```

Each file is plain markdown with a small frontmatter block:

```
---
title: Beef Stew
tags: [stew, beef, slow-cook]
servings: 4
---

# Beef Stew

## Ingredients

- ...
```

## Non-goals (product-level)

- Authentication, multi-user accounts, sharing.
- Editing recipes inside the app. The file is the source of truth.
- Cloud sync. Files live wherever the user puts them.
- Image management beyond a markdown image link.
- Search beyond tag filtering. (List → tag-filter → click is enough.)
- Mobile-app version. Web only; mobile browsers must work.

## Intended scope

Bones:
- Operator runs the app pointing at `content/recipes/`. The index
  page lists every recipe by title. Clicking a row renders that
  recipe's markdown body.

MVP:
- Tag filter on the index page (click a tag, see only recipes with
  that tag).
- Frontmatter `servings` field shown on the detail page.
- Responsive layout: usable on phones (single-column) and desktop
  (two-column with the body wider).

Final (out of MVP scope):
- Search by ingredient.
- Print stylesheet.

## Capabilities (what the user can do)

- `list-recipes` — view every recipe in `content/recipes/` as an
  index.
- `view-recipe-detail` — open one recipe and read its rendered body.
- `filter-by-tag` — narrow the index to recipes carrying a tag.
- `responsive-layout` — usable on a 320px-wide screen and a 1200px-
  wide screen.

## Modules (architecture)

- `recipe-loader`: walks `content/recipes/`, parses frontmatter,
  returns `Recipe` value objects. Pure filesystem reads.
- `web-frontend`: serves the index and detail routes; renders
  templates; references VD-authored wireframes for layout/styling.

## External dependencies

None. Recipes are filesystem-local markdown.

## Wireframes (VD scope)

VD authors two wireframes:

- `recipes-index.html` — list view: title + tags per row, tag
  filter chips above the list. Mobile (320px), tablet (768px), and
  desktop (1200px) breakpoints.
- `recipe-detail.html` — one-recipe view: title, tags, servings,
  rendered markdown body. Same three breakpoints.

Both wireframes are linted by `wireframe-linter` and reviewed by
`visual_compliance` + `responsive` reviewers. Tickets that change
the rendered HTML in `web-frontend` must reference one of these
wireframes via `visual_references` so the visual reviewer can
gate the diff.

## Test data

Sample recipes for the eval corpus, committed under
`evals/projects/recipe-browser/content/recipes/`:

- `beef-stew.md` — tags: stew, beef, slow-cook
- `pancakes.md` — tags: breakfast, quick
- `miso-soup.md` — tags: soup, japanese, quick

(Three is enough to test list rendering, detail rendering, and tag
filtering.)

## Tracer

A Playwright (or equivalent) smoke that:

1. Boots the app pointing at the sample corpus.
2. Loads `/`. Asserts three rows render. Asserts each row shows the
   recipe title and at least one tag.
3. Clicks the "quick" tag chip. Asserts only `pancakes` and
   `miso-soup` remain.
4. Clicks `pancakes`. Asserts the detail page renders the markdown
   body (a heading + an ingredient list).
5. Resizes viewport to 320px. Asserts the layout doesn't horizontally
   scroll.

Tracer is exercised by the bones bundle; later tickets that touch
either module or the wireframes must keep it green.

## Synthetic-operator notes

Pin these answers per eval run:

- L1 discovery: one persona ("home cook with a markdown stash"), one
  journey ("open the app, find a recipe, read it").
- L3 elaboration: accept the four capabilities listed above.
- VD discovery: accept the two-wireframe split (index + detail) at
  three breakpoints.
- SA: accept the two-module split.
- PM planning: bones-then-MVP layering. `responsive-layout`
  capability lands in MVP, not bones.
- Reviewer-question prompts: prefer "no" / "skip" unless the
  question is structurally necessary.

## Pass/fail summary

- Tracer green at end of bones run.
- All bones tickets in `resolved` state.
- No `provisioning_failed` / `auto_commit_failed` /
  `reviewer_spawn_failed` SystemEvents.
- Critical reviewer comments == 0 at handoff.
- `visual_compliance` reviewer ran on every UI ticket and found no
  critical findings against the pinned wireframes.
- Total cost / wall-clock within budget set by Phase 0.4 thresholds.
