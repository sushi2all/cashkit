# ADR-0024 — Hypothetical figures are stamped; book state stays visually separate until Apply

**Date** 2026-08-19 · **Status** accepted · **Type** ux · **Source** design session 2026-08-19 (.specstory/history/2026-08-19_19-54-10Z)

## Context

A review pass found the home screen mixing two states: an answer card included a pending trip while the header showed pre-trip figures, with no signal that the answer was hypothetical. Confusing recorded state with hypothetical state is the exact error class the engine exists to prevent; the UI must not reintroduce it.

## Decision

What-if answers (computed on a throwaway overlay) carry an explicit **WHAT-IF / INCLUDES PENDING** stamp. The header and sparkline always show the book's own committed figures, in neutral color, until the user taps Apply. Applied-state markers appear only on screens where the change is real. The rule generalizes: **every hypothetical figure carries a visible provenance stamp distinct from committed book state.**

## Alternatives considered

Showing what-if numbers as if committed — rejected outright; it silently mixes hypothesis into record.

## Risks and implications

This is a UI invariant the eventual app must honor on every surface, not a styling choice. It pairs with the engine's scenario overlay semantics (ADR-0007): the UI distinction mirrors a storage distinction that already exists.

Related: [[0007-base-scenario-storage]], [[0023-computed-receipt-design-language]]
