---
title: Project Spec Format
type: reference
status: active
owner: brent
created: 2026-05-11
updated: 2026-05-11
---

# Project Spec Format

The shape of `.jig/spec/project.structured.yaml` and what goes in each field. Schema lives in
[`jig/spec_schema.py`](../../jig/spec_schema.py); the Pydantic models are authoritative.

## Top-level shape

```yaml
name: todoapp
summary: A simple web-based todo list manager.
spec_version: 1
generated_at: 2026-05-11T12:00:00Z
capabilities:
  - ...
non_goals:
  - ...
```

| Field          | Type                | Notes                                              |
|----------------|---------------------|----------------------------------------------------|
| `name`         | `str`               | Project name.                                      |
| `summary`      | `str`               | One-paragraph description of the project.          |
| `capabilities` | `list[Capability]`  | Ordered list. Empty list is valid.                 |
| `non_goals`    | `list[NonGoal]`     | Ordered list. Empty list is valid.                 |
| `generated_at` | `datetime` (UTC)    | Set on every regen.                                |
| `spec_version` | `int`               | Must equal `1`. Bumped on breaking schema changes. |

## Capability

```yaml
- id: due-dates
  title: Due dates
  state: planned
  summary: Users can give todos due dates.
  user_story:
    as: busy professional
    want: due dates on my todos
    benefit: I never miss a deadline
  behaviors:
    - id: set-due-date
      description: Set a date on any todo.
      examples: []
      acceptance_criteria:
        - A date can be attached to any todo and persists across reloads.
  acceptance_criteria: []
  examples: []
  done_enough: []
  excluded:
    - Recurring schedules.
  open_questions: []
  tickets:
    - T-0042
  aliases:
    - deadlines
  created_at: 2026-05-01T09:00:00Z
  last_updated: 2026-05-11T12:00:00Z
  state_changed_at: 2026-05-01T09:00:00Z
```

| Field                 | Type                       | Required | Notes                                                              |
|-----------------------|----------------------------|----------|--------------------------------------------------------------------|
| `id`                  | `str` (kebab-case)         | yes      | Stable identity. Matches `^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`.        |
| `title`               | `str`                      | yes      | Human-readable name.                                               |
| `state`               | `CapabilityState`          | yes      | See "CapabilityState" below.                                       |
| `summary`             | `str`                      | no       | Prose description. Defaults to `""`.                               |
| `user_story`          | `UserStory \| null`        | no       | WHO/WHAT/WHY framing. Defaults to `null`.                          |
| `behaviors`           | `list[Behavior]`           | no       | Concrete things the capability does. Defaults to `[]`.             |
| `acceptance_criteria` | `list[str]`                | no       | Capability-level AC; used when no behaviors. Defaults to `[]`.     |
| `examples`            | `list[GivenWhenThen]`      | no       | Reserved; not currently populated. Defaults to `[]`.               |
| `done_enough`         | `list[DoneEnoughBlock]`    | no       | Reserved; not currently populated. Defaults to `[]`.               |
| `excluded`            | `list[str]`                | no       | Capability-scoped non-goals. Defaults to `[]`.                     |
| `open_questions`      | `list[str]`                | no       | Unresolved questions. Defaults to `[]`.                            |
| `tickets`             | `list[str]`                | no       | Ticket IDs derived from this capability. Defaults to `[]`.         |
| `aliases`             | `list[str]` (kebab-case)   | no       | Prior IDs, kept for stable URI/ticket linkage. Defaults to `[]`.   |
| `created_at`          | `datetime` (UTC)           | yes      | When the capability first appeared. Preserved across regen.        |
| `last_updated`        | `datetime` (UTC)           | yes      | Bumped whenever the capability's content changes.                  |
| `state_changed_at`    | `datetime` (UTC)           | yes      | When `state` last transitioned.                                    |

**Cross-field rule:** if `state` is `planned`, `in_progress`, or `built`, the capability must have at
least one acceptance criterion — either capability-level (`acceptance_criteria`) or via behaviors (each
`Behavior` already requires ≥1 AC). `backlog`, `planned_uncommitted`, and `archived` do not require AC.

### `CapabilityState`

Enum values:

| Value                  | Meaning                                                    |
|------------------------|------------------------------------------------------------|
| `backlog`              | Idea-stage. No commitment to build.                        |
| `planned_uncommitted`  | Identified for future work; not yet committed.             |
| `planned`              | Committed to build. AC required.                           |
| `in_progress`          | Open ticket(s) implementing it. AC required.               |
| `built`                | All tickets resolved. AC required.                         |
| `archived`             | Retired; no longer part of the active product.             |

### `Behavior`

```yaml
- id: set-due-date
  description: Set a date on any todo.
  examples:
    - "User picks 2026-06-01 in the date picker; it appears on the todo row."
  acceptance_criteria:
    - A date can be attached to any todo and persists across reloads.
```

| Field                 | Type               | Required | Notes                                    |
|-----------------------|--------------------|----------|------------------------------------------|
| `id`                  | `str` (kebab-case) | yes      | Unique within the parent capability.     |
| `description`         | `str`              | yes      | One-line "what it does".                 |
| `examples`            | `list[str]`        | no       | Illustrative scenarios. Defaults to `[]`.|
| `acceptance_criteria` | `list[str]`        | yes      | At least one required (`min_length=1`).  |

### `UserStory`

```yaml
user_story:
  as: busy professional
  want: due dates on my todos
  benefit: I never miss a deadline
```

| Field     | Type  | Required | Notes                                                           |
|-----------|-------|----------|-----------------------------------------------------------------|
| `as`      | `str` | yes      | The persona. (Field name is `as_` in Python; YAML key is `as`.) |
| `want`    | `str` | yes      | The capability they want.                                       |
| `benefit` | `str` | yes      | Why they want it.                                               |

### `GivenWhenThen` (reserved)

```yaml
- given: A todo with no due date
  when: The user picks 2026-06-01 in the date picker
  then: The todo displays "due 2026-06-01" and persists across reloads
```

| Field   | Type  | Required | Notes               |
|---------|-------|----------|---------------------|
| `given` | `str` | yes      | Precondition.       |
| `when`  | `str` | yes      | Action.             |
| `then`  | `str` | yes      | Expected outcome.   |

Extra fields rejected (`extra="forbid"`).

### `DoneEnoughBlock` (reserved)

```yaml
- layer: mvp
  criteria:
    - The date picker is keyboard-accessible.
    - The detail page renders the due date.
```

| Field      | Type                              | Required | Notes                                       |
|------------|-----------------------------------|----------|---------------------------------------------|
| `layer`    | `"bones" \| "mvp" \| "final"`     | yes      | Which layer this slice belongs to.          |
| `criteria` | `list[str]`                       | yes      | At least one required (`min_length=1`).     |

Extra fields rejected (`extra="forbid"`).

## NonGoal

```yaml
- id: no-multi-user
  text: Multi-user / sharing
  rationale: Single-user is the explicit point of the product.
  aliases:
    - no-collab
```

| Field       | Type                       | Required | Notes                                          |
|-------------|----------------------------|----------|------------------------------------------------|
| `id`        | `str` (kebab-case)         | yes      | Stable identity.                               |
| `text`      | `str`                      | yes      | What's excluded.                               |
| `rationale` | `str`                      | no       | Why it's excluded. Defaults to `""`.           |
| `aliases`   | `list[str]` (kebab-case)   | no       | Prior IDs. Defaults to `[]`.                   |

## ID and alias rules

Every `id` and every entry in `aliases` (capability or non-goal) must be kebab-case matching
`^[a-z0-9]([a-z0-9-]*[a-z0-9])?$`:

- Lowercase letters, digits, hyphens only.
- Must start and end with a letter or digit (no leading/trailing hyphen).
- Single-character slugs allowed (e.g. `a`).
- Underscores, spaces, and uppercase letters rejected.

## Change log

- 2026-05-11: Initial draft (brent)
