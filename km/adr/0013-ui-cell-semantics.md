# ADR-0013 — UI cell semantics: every cell edit resolves to a model mutation

**Date** 2026-08-04 · **Status** accepted · **Type** ux / api-design · **Source** orchestration session 2026-07-29

## Context

End users need both interaction modes: describing changes in plain language works for structural edits, but "click the cell and type the number" is better for scalar corrections. In a spreadsheet the cell *is* the input; in CashKit a cell is an **output** of `(period, item, measure)` — generated from segment amount × escalation × probability × settlement split. "Type 50 000 into March" is therefore ambiguous across at least five different edits: change the segment amount, split the segment at March, override March only, add an Event, or change the escalation rate. Storing a per-cell value to dodge the ambiguity would rebuild Excel with extra steps and destroy the property that makes CashKit worth building: every number traces to a generator.

## Decision

1. **A cell edit always resolves to an Item, Event, or param change. There is no anonymous per-cell value.**
2. **Cell taxonomy by backing** — the entire UI interaction model:

   | Cell backed by | Click behaviour |
   |---|---|
   | Event `status="actual"` | Not editable, **correctable**: "Record a correction" (ADR-0012), mandatory note; afterwards both rows shown — original struck, correction linked. Visually distinct from an ordinary edit; a correction leaves a scar by design. |
   | Event forecast/committed | Edit amount in place — the one true direct-manipulation case. |
   | Item segment (generated) | Popover exposing the arithmetic ("12 000 × 1.03² × 0.9"); edit the input clicked through to, or convert to a point Event. Never silently pick an interpretation. |
   | Formula item (derived) | Read-only; show formula and bindings, navigate to an editable upstream. |

3. **Typing a one-off number into an empty or generated cell creates a forecast `Event`** — the model's existing concept for one-offs; auditable, dated, no new machinery.
4. **`trace()` is the primary interaction primitive.** Click → `trace()` → the returned tree *is* the edit menu. `why_zero()` explains empty cells the same way.
5. **Two modalities, one mutation path.** Plain language (agent) for structural edits; direct manipulation for scalar edits. Both emit the same SDK calls into the same scenario overlay — the agent is not a separate mode with a separate write path. The UI shows the call that was made. Everything lands in an overlay, never in base directly; `commit()` is Save.

## Alternatives considered

- **Per-cell override storage** (spreadsheet semantics): rejected — kills traceability, reintroduces every spreadsheet failure mode the PRD §1 exists to escape.
- **Silent best-guess interpretation of generated-cell edits**: rejected — violates "diagnostic over guess"; the popover asks instead.

## Risks and implications

- Phase 10 (`trace()`, `why_zero()`, `describe_book()`) is UI-critical, not introspection polish — its gate quality directly bounds UI quality.
- The taxonomy assumes `trace()` output is complete enough to drive an edit menu; if a gap appears, it is a Phase 10 defect, not a UI workaround.
- Locks in: no UI-private mutation API; the SDK surface is the only write path (consistent with the SDK-only non-negotiable).
