# ADR-0029 — Question turns never write; hosts confirm mutations

**Date** 2026-08-22 · **Status** accepted · **Type** ux / security · **Source** proto session 2026-08-22 (trial T11 in proto/TESTLOG.md)

## Context

ADR-0019 rule 3 separates read intents from mutation intents and permits hosts to require confirmation on the mutating ones. Trial T11 turned that permission into a requirement: asked "can I afford a 1500 EUR laptop in September?", gemini-2.5-flash-lite emitted two write ops — it modified the book while answering a question — and a prompt rule against it was demonstrably not enough.

## Decision

**The host enforces, structurally, that a question turn cannot change the book.** Enforcement is post-interpretation, on the artifact (a prompt rule alone is insufficient, and classifying the raw instruction is pre-interpretation routing, banned by ADR-0028):

1. Read intents and mutation intents are separate tool/op sets; a turn's mutations are held, never auto-applied, when they arrive alongside a question-shaped answer.
2. The host may require an explicit confirmation step for any mutation — the design.pen confirmation card (ADR-0022 feature 2) is that step in the mobile app.
3. Applying a held mutation is the user's act, not the model's.

## Alternatives considered

- Prompt-only rule: observed insufficient (T11).
- Classifying the instruction up front as question vs command: rejected — pre-interpretation.

## Risks and implications

Confirmation friction on every write is a UX cost the MLP already budgets for (typed confirmation card). The guard also covers the reverse failure (a command turn that mutates more than asked): unexpected ops surface in the same confirmation. Actuals immutability (ADR-0012) makes misapplied writes expensive — this gate is the cheap insurance in front of it.

Related: [[0012-actual-corrections-append-only]], [[0019-intent-grammar-agent-surface]], [[0022-mobile-mlp-five-features]], [[0028-flash-class-model-no-preroute]]
