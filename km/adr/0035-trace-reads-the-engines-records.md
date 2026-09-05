# ADR-0035 — `trace()` reads the engine's own records, never a re-derivation

**Date** 2026-09-05 · **Status** accepted · **Type** architecture · **Source** SDK architecture review 2026-09-05

## Context

A derived cell's trace evaluates sub-expressions through the engine's own `ColumnEvaluator` on a one-period window, so it cannot disagree with the run. A generative cell had no expression to evaluate, so `_trace_generated` walked the canonical rounding order (ADR-0003) a second time: it regenerated the segment's occurrences, recomputed escalation steps and factors, re-applied probability, re-split the settlement on a one-element array and re-placed each leg, then compared the total back to the engine's cell and set `Trace.reconciles` accordingly. About 350 lines, composed from engine primitives but orchestrated independently — a third orchestration of the rules the vectorized and reference engines already implement twice, with a flag to notice when it drifted.

## Decision

**Expansion records what it computed, and the trace reads it.** `expand_item` takes an optional `recorder` and appends one `OccurrenceRecord` per segment with live occurrences: the accrual ordinals and period indices, the base amounts, the escalation step counts and escalated amounts, the probability-weighted amounts, and — filled in by `settle_occurrences` — the net leg and target period per `DueTerm`. These are references to the arrays the engine scattered, not copies. `Engine.occurrences` keeps them per item, rebuilt with the item's column on a delta run.

`trace()` on a generative cell selects the occurrences (accrual measure) or legs (cash measure) that land in the period and renders the steps from the recorded values. The only derived quantity is the displayed escalation *factor*, computed from the recorded step count through the engine's factor table for the popover text; the amounts shown are the engine's. `Trace.reconciles` remains and still compares the steps' total to the cell, now as a check on selection rather than on arithmetic.

**No number moves.** The record is a side channel; the columns are computed exactly as before, so `ENGINE_VERSION` is unchanged and the dual-engine gate is unaffected. The reference engine records nothing: it is the oracle, not the thing traced.

## Alternatives considered

- **Keep the re-derivation behind the `reconciles` guard**: rejected. A guard that detects drift after the fact is weaker than a design in which drift has nowhere to occur, and the re-derivation was the largest block of engine-shaped logic outside `engine/`.
- **Record per-occurrence Python objects**: rejected. The engine is vectorized; recording the arrays it already holds costs a handful of references, whereas materializing objects per occurrence would cost more than the expansion itself on a day-grain book.

## Risks and implications

- Memory: the recorded arrays are the engine's working arrays, retained for the life of the `Engine`. For a 2 000-item, ten-year day-grain book that is the same order as the columns themselves.
- VAT is still not rendered as a trace step on the cash measure (it never was); a VAT-bearing item's cash cell can therefore report `reconciles=False`. Recording the allocated VAT parts is the natural next step and is deliberately left out of this change so it moves no behaviour.

Related: [[0003-canonical-rounding-order]], [[0013-ui-cell-semantics]], [[0034-reads-live-on-what-they-read]]
