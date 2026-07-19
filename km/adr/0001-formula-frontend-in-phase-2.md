# ADR-0001 — Formula front-end built with the reference engine; Phase 4 hardens it

**Date** 2026-07-19 · **Status** accepted

## Context

The phase plan put the formula language in Phase 4, but the Phase 2 reference-engine gate requires a `prev()` feedback loop and the Phase 3 dual-engine gate requires `agg()` selectors, `prev(n>1)` and feedback loops. Derived items cannot be evaluated without parsing their formulas — the plan was unimplementable in its stated order.

## Decision

The restricted-AST parser, symbol table and builtin semantics (PRD §5.4) are implemented in Phase 2 as part of the reference engine, evaluated naively. Phase 4 does not introduce the language; it hardens it: whitelist enforcement, fuzz corpus, graph-build-time selector resolution, diagnostics.

## Consequences

- Phase 2 is larger, but the oracle can exercise the full semantic surface from the start — which is what makes it an oracle.
- Phase 4's gate (malicious-input fuzzing, vectorization proofs) is unchanged.
- Security hardening lands before the vectorized evaluator ever runs untrusted formulas in anger.
