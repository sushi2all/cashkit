# ADR-0007 — Base scenario: uniform semantics, privileged storage location

**Date** 2026-07-19 · **Status** accepted

## Context

The anti-pattern list forbids special-casing the base scenario ("base is a scenario with `parent=None`"), yet the storage layout puts base's content (`book.yaml`, `params.yaml`, `items/`) at top level, outside `scenarios/`, with a `scenarios/base.yaml` whose content was undefined. The two statements read as a contradiction.

## Decision

Both stand, scoped: base is special in **storage only**. Its content lives at top level because one-file-per-item is what makes git diffs legible — the whole point of D9. `scenarios/base.yaml` is the (normally empty) overlay shell with `parent=None`. Resolution, execution and the SDK treat base identically to every other scenario; no code path may branch on "is this base".

## Consequences

- `add_item(book, …)` writes to the top-level item files; `set_item(scenario, …)` writes overlays. The construction/scenario API split maps 1:1 to the storage split.
- The "no special-casing" rule now has a testable meaning: grep the engine and SDK for base-conditionals, find none.
