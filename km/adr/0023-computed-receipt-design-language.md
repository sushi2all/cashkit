# ADR-0023 — The app's visual identity is Calm-fintech with a computed-receipt language

**Date** 2026-08-19 · **Status** accepted · **Type** ux · **Source** design session 2026-08-19 (.specstory/history/2026-08-19_19-54-10Z)

## Context

Three low-fi directions were sketched for the mobile app: A calm European fintech, B deterministic terminal, C warm ledger. Direction A won but its first pass read as generic; the brief asked for something ownable.

## Decision

Direction A, extended with a **computed-receipt language**: calm light base, serif display numerals, dotted-leader ledger rows, and tiny monospace provenance stamps (as-of date, item ids, book revision) on every computed figure. No chat bubbles — the user's words render as an editorial quote, the system's answers as receipts. The receipt is the identity: it makes the engine's determinism visible on every screen.

## Alternatives considered

- **B, deterministic terminal**: rejected — intimidates non-technical users and reads dark-only.
- **C, warm ledger**: rejected — reads like a toy next to real money decisions.
- **Plain A**: rejected by the user as vanilla.

## Risks and implications

The receipt language is now the contract for every screen: stamps, leaders and provenance conventions must stay consistent as screens are added. `design.pen` carries the nine reference screens.

Related: [[0013-ui-cell-semantics]], [[0022-mobile-mlp-five-features]], [[0024-whatif-state-separation]]
