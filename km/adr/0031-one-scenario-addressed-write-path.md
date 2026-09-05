# ADR-0031 — One scenario-addressed write path; every write persists

**Date** 2026-09-05 · **Status** accepted · **Type** architecture · **Source** SDK architecture review 2026-09-05

## Context

PRD §6.1 and §6.3 specified two authoring surfaces: construction verbs on the book (`add_item`, `add_derived`, `set_param`, `retag`, `add_tax_regime`, `set_cutover`) and scenario verbs on a scenario (`set_item`, `set_param`, `apply_macro`, …). As implemented, the first lived on `CashKit` and the second on `kit.scenarios`, a `ScenarioSet`. Writing an item to base was `kit.add_item(spec)`; writing the same item to a fork was `kit.scenarios.set_item("downside", spec)`.

ADR-0007 states that base is privileged in storage only and that *no code path may branch on "is this base"*. The split surface pushed exactly that branch onto every caller: the service applier chose a verb by `target == BASE_SCENARIO` in three places. It also produced three inconsistencies nobody had decided on:

- base writes ran the two-tier admission rule (refuse what is wrong in isolation, record and report what the book may fix later) and scenario writes ran none of it;
- base writes persisted to the working tree and scenario writes stayed in memory, so callers sprinkled `kit.save()` by hand;
- `retag` on the book and `RetagItems` on a scenario were two spellings of one operation, and the only way to set a book field such as `horizon` was to reach below the SDK line (`kit.scenarios.set_book(...)` followed by `kit.save()`).

## Decision

**One write path, addressed by scenario, on the kit.** `CashKit` carries `set_item`, `set_param`, `remove_item`, `unset`, `apply_macro`, `fork`, `flatten` and `set_book`; each authoring verb takes `scenario: ScenarioId = "base"`. `add_item`, `add_derived`, `retag`, `set_cutover` and `add_tax_regime` are removed: an item is authored by value with `set_item` whatever its kind, `retag` is `apply_macro(RetagItems(...))`, and `set_book(cutover=…, opening_balance=…, horizon=…, calendar=…, tax_regimes=…)` writes the remaining Book fields. `ScenarioSet` stays as the in-memory owner of the authored book and its overlays and is no longer part of the public surface.

**Exactly one branch knows base's id, and it is the write router.** `ScenarioSet.set_item`, `set_param` and `remove_item` addressed to `base_id` write the authored book; any other id writes an overlay. This is the storage split of ADR-0007 applied at the point where a write chooses its file. Resolution, execution and every caller stay branch-free.

**The admission rule applies to every scenario.** `set_item` refuses what is wrong in isolation (`CK-E003`, `CK-E004`, `CK-E005`, `CK-E011`, `CK-E012`) and records-and-reports what the book as a whole may settle (`CK-E001`, `CK-E002`, `CK-E008`, `CK-E019`, `CK-E020`), computed as the compile-diagnostic delta between the scenario resolved before and after the write. A fork gets the same call-time news base always had.

**Every write that recorded something is written to the working tree before it returns.** `save()` is no longer public. The CLI, a human's editor and the next process see what the kit holds; PRD §6.7's two-tier model is unchanged, because the working tree is not a revision and `commit()` still marks the boundaries.

## Alternatives considered

- **Keep both surfaces and document the branch**: rejected. The branch is the defect; documenting it makes it permanent.
- **Route base writes through overlays in `scenarios/base.yaml`**: rejected. It would fill the base shell ADR-0007 keeps empty for diff legibility and move the branch into the config store instead of removing it.
- **Persist nothing until `commit()`**: rejected. `create_book` and `cashkit init` already write the tree, the CLI reads it, and a book that exists on disk in a state its own kit disagrees with is the silent failure this project ranks worst.

## Risks and implications

- Breaking change to PRD §6.1/§6.3 signatures and to the service applier; both updated in the same commit. Precedent for deviating from §6 signatures: `BookRef`, `AffectedCount`, C-S55-01.
- Two compiles per write (before and after) to compute the reported delta. Compilation of a 50-item book is milliseconds (BENCHMARKS.md); the cost is accepted for call-time diagnostics.
- `tax_regimes` is now authored as a whole list, the way `segments` is atomic. Replacing one regime means giving the list again.

Related: [[0007-base-scenario-storage]], [[0009-field-sparse-overrides]], [[0032-one-outcome-shape]], [[0033-read-only-kit]]
