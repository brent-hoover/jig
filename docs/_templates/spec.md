---
id: REQ-<AREA>-<NUM>
title: <Short requirement title>
type: spec
status: draft
owner: <username>
created: YYYY-MM-DD
updated: YYYY-MM-DD
depends_on: []
implements: []
---

# <Title>

## Context

<One or two paragraphs. What is this component, and why does it exist?
Link to the parent problem/design doc if relevant.>

## Requirements

<EARS-style requirements. Each gets an ID derived from the doc ID.
Tag the EARS pattern: ubiquitous, event-driven, state-driven, optional,
or unwanted behavior.>

### REQ-<AREA>-<NUM>.1 (ubiquitous)

The system shall <behavior>.

**Acceptance:** <Concrete, observable criterion. How do we verify this?>

### REQ-<AREA>-<NUM>.2 (event-driven)

When <trigger>, the system shall <response>.

**Acceptance:**

### REQ-<AREA>-<NUM>.3 (unwanted behavior)

If <unwanted condition>, the system shall <safe response>.

**Acceptance:**

### REQ-<AREA>-<NUM>.4 (state-driven)

While <state>, the system shall <behavior>.

**Acceptance:**

## Explicit non-requirements

<Things this component does NOT do, even though it might seem like
it should. Prevents scope creep during implementation.>

-
-

## Open questions

<Unresolved questions that block implementation. A spec with open
questions is not ready for `status: approved`.>

- [ ]
- [ ]

## Change log

- YYYY-MM-DD: Initial draft (<owner>)
