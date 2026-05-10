---
title: <Service Name> — <Operation> Runbook
type: runbook
status: active
owner: <team-name>
created: YYYY-MM-DD
updated: YYYY-MM-DD
service: <service-name>
---

# <Service Name> — <Operation> Runbook

## When to use this

<What situation or alert triggers using this runbook? Be specific —
the person reading this is probably tired and needs to know within
ten seconds whether they're in the right place.>

## Prerequisites

<Access, credentials, tools, VPN, etc. required before starting. If
the reader doesn't have these, list where to get them.>

-
-

## Procedure

<Numbered, literal steps. Commands exactly as they should be run.
Expected output so the reader knows if they're on track.>

### 1. <Step>

```
<command>
```

Expected output:
```
<what success looks like>
```

### 2. <Step>

## Verification

<How to confirm the operation succeeded. Concrete checks, not vibes.>

-
-

## Rollback

<If this goes wrong, how to get back to a known-good state.>

## Troubleshooting

### Symptom: <observable problem>

<Cause and fix.>

### Symptom: <observable problem>

<Cause and fix.>

## Escalation

<Who to page / which channel to post in if this runbook doesn't resolve
the situation. Include on-call rotation reference if applicable.>

## Change log

- YYYY-MM-DD: Initial draft (<owner>)
