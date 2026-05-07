Foundations (read first):

  ┌─────┬─────────────┬──────────────────────────────────────────────────────────┬────────┐
  │  #  │    Path     │                           What                           │  Time  │
  ├─────┼─────────────┼──────────────────────────────────────────────────────────┼────────┤
  │ 1   │ TENETS.md   │ 5 principles everything else follows                     │ 10 min │
  ├─────┼─────────────┼──────────────────────────────────────────────────────────┼────────┤
  │ 2   │ ontology.md │ Canonical vocabulary — Suite, Module, Contract, VD, etc. │ 8 min  │
  └─────┴─────────────┴──────────────────────────────────────────────────────────┴────────┘

  Workflow designs (read in this order — they build on each other):

  ┌─────┬──────────────────────────────────┬──────────────────────────────────────────────┬────────┐
  │  #  │               Path               │                     What                     │  Time  │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 3   │ docs/v2.0/multi-level-spec/problem.md │ Why we need multi-level spec (the BDUF       │ 5 min  │
  │     │                                  │ problem at the spec layer)                   │        │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 4   │ docs/v2.0/multi-level-spec/design.md  │ PO workflow (L0-L4), suites, journey-driven  │ 15-20  │
  │     │                                  │ discovery                                    │ min    │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 5   │ docs/v2.0/sa-architecture/problem.md  │ Why SA contracts (coherence under            │ 5 min  │
  │     │                                  │ decomposition)                               │        │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 6   │ docs/v2.0/sa-architecture/design.md   │ SA design — contracts, behavioral DbC,       │ 25-30  │
  │     │                                  │ risks, spikes, what-a-normal-pass-produces   │ min    │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 7   │ docs/v2.0/visual-design/problem.md    │ Why visual design matters; what we missed    │ 5 min  │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 8   │ docs/v2.0/visual-design/design.md     │ VD role, wireframes (SVG), design system,    │ 20 min │
  │     │                                  │ browser viewing                              │        │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 9   │ docs/v2.0/pm-workflow/problem.md      │ Why we need a PM layer (BDUF problem at      │ 5 min  │
  │     │                                  │ execution)                                   │        │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │     │                                  │ Planner+Coordinator, build plan,             │ 25-30  │
  │ 10  │ docs/v2.0/pm-workflow/design.md       │ bones/MVP/final, federated reviewers,        │ min    │
  │     │                                  │ tiering                                      │        │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 11  │ docs/v2.0/dev-environment/problem.md  │ Why service isolation (the third leg of      │ 5 min  │
  │     │                                  │ isolation)                                   │        │
  ├─────┼──────────────────────────────────┼──────────────────────────────────────────────┼────────┤
  │ 12  │ docs/v2.0/dev-environment/design.md   │ Provisioning strategies, lifecycle hooks,    │ 15-20  │
  │     │                                  │ namespace isolation                          │ min    │
  └─────┴──────────────────────────────────┴──────────────────────────────────────────────┴────────┘

  Capture-now / consume-later (read for "is this what we're committing to"):

  ┌─────┬────────────────────────────────┬──────────────────────────────────────────────────┬──────┐
  │  #  │              Path              │                       What                       │ Time │
  ├─────┼────────────────────────────────┼──────────────────────────────────────────────────┼──────┤
  │ 13  │ docs/v2.0/analytics/problem.md      │ Event capture — fine for everything; 25 event    │ 10   │
  │     │                                │ types now in code                                │ min  │
  ├─────┼────────────────────────────────┼──────────────────────────────────────────────────┼──────┤
  │     │                                │ Six 2.0+ commitments (intent layer, synthetic    │ 12   │
  │ 14  │ docs/v2.0/agent-leverage/problem.md │ operator, quartermaster, adversarial pairing,    │ min  │
  │     │                                │ ensemble, renderers)                             │      │
  └─────┴────────────────────────────────┴──────────────────────────────────────────────────┴──────┘

  Deferred (skim to confirm the deferral is right):

  ┌─────┬───────────────────────────────┬───────────────────────────────────────────────────┬──────┐
  │  #  │             Path              │                       What                        │ Time │
  ├─────┼───────────────────────────────┼───────────────────────────────────────────────────┼──────┤
  │ 15  │ docs/v2.0/learnings/problem.md     │ Facts vs heuristics; heuristics deferred until    │ 5    │
  │     │                               │ corpus exists                                     │ min  │
  │  #  │             Path              │                       What                        │ Time │
  ├─────┼───────────────────────────────┼───────────────────────────────────────────────────┼──────┤
  │ 15  │ docs/v2.0/learnings/problem.md     │ Facts vs heuristics; heuristics deferred until    │ 5    │
  │     │                               │ corpus exists                                     │ min  │
  ├─────┼───────────────────────────────┼───────────────────────────────────────────────────┼──────┤
  │ 16  │ docs/v2.0/agent-testing/problem.md │ Six kinds of agent-driven testing; deferred       │ 5    │
  │     │                               │ behind v1 simulator                               │ min  │
  └─────┴───────────────────────────────┴───────────────────────────────────────────────────┴──────┘

  Code worth eyeballing (cheaper to fix now than after wiring):

  ┌─────┬─────────────────────────┬────────────────────────────────────────────────────────┬───────┐
  │  #  │          Path           │                          What                          │ Time  │
  ├─────┼─────────────────────────┼────────────────────────────────────────────────────────┼───────┤
  │ 17  │ jig/analytics/events.py │ All 25 typed event classes — vocabulary check, name    │ 10    │
  │     │                         │ check, field check                                     │ min   │
  └─────┴─────────────────────────┴────────────────────────────────────────────────────────┴───────┘

  Reference (skip unless you want to see the discipline):

  ┌─────┬─────────────────────────────────┬────────────────────────────────────────────────┬──────┐
  │  #  │              Path               │                      What                      │ Time │
  ├─────┼─────────────────────────────────┼────────────────────────────────────────────────┼──────┤
  │ 18  │ docs/_templates/problem.md      │ Template using the new sequence (problem →     │ 3    │
  │     │                                 │ simplest → complications)                      │ min  │
  ├─────┼─────────────────────────────────┼────────────────────────────────────────────────┼──────┤
  │ 19  │ jig/analytics/store.py +        │ Persistence + async emit (small files, mostly  │ 5    │
  │     │ emitter.py                      │ mechanical)                                    │ min  │
  └─────┴─────────────────────────────────┴────────────────────────────────────────────────┴──────┘

  Reading order tip: the top-to-bottom order intentionally mirrors the workflow sequence the docs
  describe. By the time you hit dev-environment, you'll have all the context for why service isolation
  matters. By the time you hit agent-leverage, you'll know what the baseline is that those commitments
  are extending.

  If you find anything that reads wrong or contradicts something earlier, easier to flag during reading
   than after — the docs cross-reference each other and inconsistencies multiply.