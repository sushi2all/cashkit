# ADR-0012 — Actuals are correctable in the ledger, immutably; corrections are append-only

**Date** 2026-07-29 · **Status** accepted · **Supersedes in part** [ADR-0008](0008-import-conflicts-abort-batch.md) (the `void_event` actual-refusal clause)

## Context

"Actuals are immutable" conflated two different systems.

The **scenario overlay** rule is about what-ifs: a downside case must not rewrite March's bank statement. Non-negotiable #5 and PRD §9 both scope it correctly — "no code path allows a **scenario overlay** to modify an event with `status="actual"`", "never override an actual **in a scenario**".

ADR-0008 generalized that into the **ledger**: `void_event` "refuses `status="actual"` with a diagnostic". Combined with conflict-aborts-the-batch, a mis-recorded actual has no resolution path:

- A wrong actual arrives (typo, bad `ext_id` mapping, bank feed error).
- It cannot be voided — refused for being an actual.
- A corrected payload cannot be re-imported under the same `(source, ext_id)` — conflict, `CK-E010`, batch aborts.
- ADR-0008 directs the source to issue a new `ext_id` — which double-counts, because the wrong row remains.

`CK-E010` says a human must intervene, and no operation exists for that human to perform.

The distinction the model needs: the **fact** is immutable — what happened in the world cannot change. The **record** of the fact can be wrong, and correcting a record is itself an event: dated, attributed, auditable. This is the reversal entry of double-entry accounting, not an edit.

## Decision

1. **Scenario-overlay immutability is unchanged and absolute.** No overlay may modify, void, or correct an event with `status="actual"`. Corrections live in the ledger, never in a scenario.

2. **`correct_event(book, event_id, corrected_payload, note) -> ChangeReport`.** Atomic: tombstones the original row and appends a new event carrying `corrects=<original_event_id>`, inheriting the original's `status`. `note` is mandatory — a correction without a stated reason is not auditable. No code path performs an in-place `UPDATE` on an event row.

3. **`void_event` continues to refuse `status="actual"` bare.** Voiding an actual without recording what replaces it destroys the fact rather than correcting the record. Its diagnostic's `suggested_fix` names `correct_event`.

4. **`CK-E010`'s `suggested_fix` names `correct_event`**, closing the loop the import abort currently opens.

5. **`Event.corrects: EventId | None = None`.** Structural rules enforced at the model: `corrects != id` (no self-correction); a correcting event requires `note`. Referential rules — target exists, target is not already corrected, target is not a tombstone — are ledger-level diagnostics per the D-P1-07 structural-vs-diagnostic split, assigned codes in Phase 5.

6. **The fact union excludes tombstoned rows and includes correcting rows**, the same filter already required for `committed`/`forecast` voids.

## Consequences

- **`at(ref)` reproduces the pre-correction number** for any revision whose watermark predates the correction. This is correct, not a defect: reproducibility means reproducing what was believed at that revision, errors included. The correction appears in every run recorded after it. A destructive edit would silently rewrite history and break the §1 reproducibility guarantee — so append-only correction and the ADR-0006 watermark scheme reinforce each other.
- The ledger stays physically append-only; `at()` truncation and rowid-based watermarks survive corrections unchanged.
- The audit trail shows both the error and the fix. A correction leaves a scar by design.
- **UI consequence:** an actual cell is not editable but *is* correctable. Clicking offers "Record a correction", requires a note, and afterwards displays both rows — the original struck, the correction linked. Visually distinct from an ordinary edit.
- Sources that legitimately amend rows still issue new `ext_id`s; the batch abort plus an explicit `correct_event` is the human-in-the-loop resolution, keeping the guess out of the engine.
- Phase 5 owns the ledger mechanics and the new diagnostic codes. Phase 1 owns only the `Event.corrects` field and its structural rules.
