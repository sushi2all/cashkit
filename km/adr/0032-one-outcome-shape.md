# ADR-0032 — One outcome shape: `ChangeReport` for writes, `(handle, diagnostics)` for handles

**Date** 2026-09-05 · **Status** accepted · **Type** architecture · **Source** SDK architecture review 2026-09-05

## Context

PRD §6.5 requires every fallible operation to return diagnostics rather than raise. Honouring that verb by verb produced five report shapes for one concept: `ChangeReport`, `ItemRef(ChangeReport)`, `AffectedCount` (an `int` subclass carrying diagnostics so `retag` could stay `-> int`), `BookRef` (a kit handle plus diagnostics) and the `(kit, diagnostics)` tuple from `open()` and `at()`. The kit's own `_authored_write` docstring recorded the cost: "the §6.1 verbs return three different report types and each has to shape its own refusal". An agent looping on results had to know which shape each verb used.

## Decision

- **Every write returns a `ChangeReport`.** `target`, `changed`, `created`, `diagnostics`, `ok`, `empty`. Subclasses that *add* fields to that shape stay: `ImportReport` (counts), `CommitReport` (the revision), `ExportReport` (the path). `ItemRef` and `AffectedCount` are removed; the count `retag` returned is `len(report.changed)`.
- **Every handle-returning call returns `(handle, diagnostics)`.** `create_book`, `CashKit.open` and `ReadOnlyKit.at` share the tuple; `handle` is `None` exactly when the diagnostics say why. `BookRef` is removed.
- **Reads return their model** (`Table`, `RunSummary`, `Trace`, `Resolution`, …), each carrying a `diagnostics` field where the read can fail partially.

## Alternatives considered

- **Keep `retag -> int` as PRD §6.1 types it**: rejected. A malformed selector and a selector matching nothing must not be the same integer, and the `int` subclass that fixed it was the least legible object on the surface.
- **A generic `Result[T]` for everything**: rejected as over-general. Writes and handles are the two shapes that exist; naming them is enough.

## Risks and implications

- Breaking for callers that read `report.item_id` or `ref.kit`; the service and the CLI are updated in the same commit.
- PRD §6.1's `-> ItemRef`, `-> int` and `-> BookRef` are superseded by this ADR.

Related: [[0031-one-scenario-addressed-write-path]]
