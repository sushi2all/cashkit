# ADR-0022 — The mobile MLP is five features over the 21-intent grammar

**Date** 2026-08-19 · **Status** accepted · **Type** feature / product-scope · **Source** design session 2026-08-19 (.specstory/history/2026-08-19_19-54-10Z)

## Context

The product direction asked for a mobile personal cash-management app on the CashKit SDK with a chat/voice interface, product-managed before it is engineered. The brief had to land on ADR-0015 (command interpreter), ADR-0017 (local-first configurations) and ADR-0019 (enumerated intent grammar), with `km/notes/intent-schema-draft.md` as the concrete surface.

## Decision

The MLP is five features, each mapped to existing intents — nothing in it requires a new intent:

1. **Ask about cash** (R1–R6); the as-of date is always visible.
2. **Say what changed**, with a typed confirmation card before every write (M1–M5, M9).
3. **Forecast at a glance** with tap-to-trace (R7–R8).
4. **What-if**: scenario fork and compare (M7, R9).
5. **Actuals** with append-only corrections and coverage diagnostics (M6, R10).

The lovable core: private on-device, instant, every number explainable — the model never invents a number.

## Alternatives considered

Explicitly excluded from the MLP: bank sync, budgets-and-advice, formula authoring, multi-user, VAT.

## Risks and implications

The MLP binds the mobile app to the unscored v0 intent schema (21 intents). If the ADR-0019 scoring exercise changes the schema, the brief and the nine design.pen screens need revision.

Related: [[0015-agent-is-a-command-interpreter]], [[0017-local-first-adoption-requirement]], [[0019-intent-grammar-agent-surface]]
