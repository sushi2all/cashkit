# ADR-0009 — Overrides resolve field-sparse along the chain; segments atomic

**Date** 2026-07-19 · **Status** accepted

## Context

The PRD stated override semantics three inconsistent ways: D5 "the Item is the atom of override", resolution rule 1 "item-level last-write-wins", rule 3 "scalar fields merge sparsely". Item-level LWW would block the Phase 7 gate requirement that a base `tags` correction propagates into a child that overrode a *different* field.

## Decision

One algorithm: for each field of each item, the nearest ancestor overlay that **recorded** that field wins; unrecorded fields fall through to the parent. `segments` is atomic (recorded whole or not at all). D5 is re-worded to what it actually meant: *authored* by whole Item (`set_item` takes the full value), *stored* field-sparse (only fields differing from the resolved parent are recorded — that's the by-value/computed-diff pipeline of D4).

## Consequences

- The Phase 7 propagation gate is now derivable from the rule instead of contradicting it.
- `provenance()` has a precise definition: the recording ancestor, per field.
- Two scenarios reaching identical resolved state by different overlay routes still diff empty, because diffs come from resolved books, not overlays.
