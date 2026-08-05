# ADR-0018 — The revision store is an interface; git is one implementation

**Date** 2026-08-05 · **Status** accepted · **Type** architecture · **Source** design session 2026-08-05

## Context

PRD D10 states git is an implementation detail of persistence, never part of the agent tool surface. That guarantees agents never run git commands. It does not guarantee the engine can run without git, and §6.6 (`commit`, `history`, `at`) is specified in terms of pygit2 object-store reads.

Offline mobile (ADR-0017, configuration B) makes this load-bearing. libgit2 on iOS or compiled to WASM is the least tractable dependency in the stack, more so than numpy (works in Pyodide), DuckDB (DuckDB-Wasm exists) or SQLite.

The timing is favourable: `grep -rl pygit2 cashkit/` currently returns nothing. The git store has not been written. This is a decision available for free right now and expensive after S5.

## Decision

**Define the revision store as an interface, with git as the first implementation, before writing the git store.**

The interface carries what §6.6 actually needs: write a revision from a snapshot, list revisions, read a book state at a revision, diff two revisions. Nothing git-shaped leaks through it: no refs, no trees, no oids in signatures.

At least a second implementation must be plausible on paper at design time (an append-only SQLite revision table is the obvious candidate) as the test that the interface is not a git wrapper with different nouns.

Only the git implementation ships in v1. The point is the seam, not a second backend.

## Alternatives considered

- **Write the git store directly, extract an interface if mobile happens**: rejected. `at()` and `history()` are used throughout the SDK and the UI design; retrofitting a seam under live call sites is exactly the refactor that ADR-0014's UI work would collide with.
- **Drop git entirely for a SQLite revision log**: rejected. Git buys real things here (content addressing, cheap history, an escape hatch for humans) and the historical-reproducibility argument in PRD §8.5 is built on it.

## Risks and implications

- Costs a small amount of design time in S5 and a slightly more abstract `stores/` layer. Cheap.
- The interface must not accidentally encode git semantics that a SQLite log cannot honour (merge, branching). Keep it linear: v1 has no branch-based workflow anyway (PRD §7.3 defers it).
- Does not by itself make mobile possible. CPython-on-device and DuckDB remain open; this removes the hardest one.

Related: [[0017-local-first-adoption-requirement]], [[0014-ui-delivery-post-v1]]
