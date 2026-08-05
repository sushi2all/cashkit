# ADR-0019 — The agent surface is an enumerated intent grammar, not free-form SDK composition

**Date** 2026-08-05 · **Status** accepted · **Type** api-design / dx · **Source** design session 2026-08-05

## Context

PRD §9 hands the agent the full SDK and a formula DSL and expects it to compose. That is a reasonable bet on a frontier model. It is not a reasonable bet on the 3–4B-class model that on-device execution implies (ADR-0017, configuration B), which will not reliably select among ~40 verbs and construct correct arguments.

The distinction that matters: **slot filling under a fixed schema** is something a small model does well; **open-ended API composition** is not. Which side CashKit lands on is determined by the shape of the command surface, and that surface is still soft.

## Decision

**Expose the agent an enumerated set of intents with typed slots, emitted under a fixed schema.** Roughly 15–25 intents covering the scope in ADR-0015. Example shape:

```
{intent: "project_balance", as_of: "2026-08-05", delta: -200000, horizon_months: 2}
```

Three rules follow, and they constrain v1 SDK design:

1. **Every reportable question is one call.** "Highest spending category" is an SDK verb, not something the agent assembles from `frame()` plus a group-by. If a small model has to compose analysis, it will compose it wrong, and wrongly-composed analysis returns a plausible number rather than an error.
2. **`as_of` is always supplied by the host, never by the model.** The wall-clock ban (ADR-0010) already forces an explicit as-of date; making it a host-filled slot removes an entire class of small-model error at no cost.
3. **Mutation intents are separated from read intents** and the host is free to require confirmation on the mutating ones. Relevant because actuals are immutable (ADR-0012) and a misrouted write is expensive to undo.

The intent schema is a host-side artefact, not engine code; ADR-0016 still holds.

## Alternatives considered

- **Full SDK surface with good docs**: rejected as the primary surface. It remains available for notebook and programmatic users, who are not the constrained case.
- **Defer the intent schema until a local model is actually selected**: rejected. The schema constrains what SDK verbs must exist; discovering that after the SDK is frozen means either shipping a bad surface or reopening it.

## Risks and implications

- Creates prerequisite work before further engine phases: draft the intent schema (15–25 entries) and score it against a small and a mid-size model. Roughly two days, and it tells you which single-call reporting verbs the SDK owes.
- An enumerated grammar caps expressiveness. Anything outside it is unreachable conversationally, which is a real product limit and an acceptable one under ADR-0015.
- Intent list becomes a versioned contract with its own compatibility burden.

Related: [[0015-agent-is-a-command-interpreter]], [[0017-local-first-adoption-requirement]], [[0010-determinism-guards]], [[0012-actual-corrections-append-only]]
