# ADR-0004 — Cutover: blanket pre-cutover suppression, ledger authoritative

**Date** 2026-07-19 · **Status** accepted

## Context

The PRD never defined the cutover suppression rule; only the implementation prompt did, and its rule was per-item: "before cutover, suppress generation for items that have actual events." Under that rule, an item with zero imported actuals keeps generating forecast rows in the reconciled past — overstating cash for anything that simply didn't happen. That contradicts what `cutover` means: the last *reconciled* boundary, i.e. the ledger before it is complete.

## Decision

- `cutover` is a boundary date. Periods `< cutover` are the reconciled past; `>= cutover` is forecast (the cutover date itself is the first forecast period).
- Before cutover, generative expansion is suppressed for **all** items. Ledger events in that window are taken as-is, whatever their status.
- From cutover forward, generation resumes; `committed`/`forecast` events apply.
- An `actual` event dated `>= cutover` is included and does **not** suppress generation. `validate()` emits `CK-W003` ("actuals after cutover — reconcile and advance cutover") instead of guessing a dedup.

## Consequences

- No double-count and no gap at the boundary, by construction rather than by per-item heuristics.
- Partial reconciliation (some items reconciled, some not) is not representable — deliberate. The remedy is to reconcile (in the accounting system) and advance cutover. CashKit is a forecasting engine, not a reconciliation tool: it *consumes* reconciled facts (§1.2, §7.1); `cutover` just marks how far those facts are trustworthy, so the forecast starts from a true baseline. A clean cutover is input hygiene for the forecast, not a goal in itself.
- The post-cutover-actual case is surfaced loudly rather than silently deduplicated; dedup guesses are exactly the "silent numerical error" class the project forbids.
