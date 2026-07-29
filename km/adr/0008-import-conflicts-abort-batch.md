# ADR-0008 — Import conflicts abort the batch; identical rows skip; void_event tombstones

**Date** 2026-07-19 · **Status** accepted · **Superseded in part by** [ADR-0012](0012-actual-corrections-append-only.md)

> The `void_event` clause below over-generalized scenario-overlay immutability into the ledger, leaving a mis-recorded actual with no resolution path. ADR-0012 adds `correct_event` and `Event.corrects`. `void_event` still refuses bare actuals; everything else here stands.

## Context

`import_events` promised both "all-or-nothing per batch" and "inserted / skipped / conflicted counts" — opposite behaviors if a conflicted row (same `(source, ext_id)`, different payload) can be skipped. Separately, forecast/committed events had no lifecycle: no way to cancel an order, and any deletion would break the append-only assumption the rowid-based watermark depends on.

## Decision

- A row whose `(source, ext_id)` exists with an **identical payload** is skipped (idempotent no-op, counted).
- A row whose key exists with a **different payload** is a conflict, and any conflict **aborts the entire batch** with per-row diagnostics (`CK-E010`). A conflicting upstream row means the source system rewrote history; that demands human attention, not a partial import.
- `void_event(book, event_id, note)` tombstones a `committed`/`forecast` event — the row is marked void, never deleted, so watermarks stay valid. It refuses `status="actual"` with a diagnostic.

## Consequences

- Re-import of the same file N times is provably idempotent (the Phase 5 gate).
- The ledger stays append-only in the physical sense; `at()` truncation semantics survive event cancellation.
- A source that legitimately amends rows must issue a new `ext_id` (or the amendment arrives as a void + new event) — pushed to the integration boundary, where it belongs.
