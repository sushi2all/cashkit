# ADR-0034 — Reads live on what they read; `validate()` is the run; `resolve()` carries its diagnostics

**Date** 2026-09-05 · **Status** accepted · **Type** architecture · **Source** SDK architecture review 2026-09-05

## Context

Three reads had two homes each.

- `summary()`, `trace()` and `why_zero()` were methods on `RunRef`, while `frame()`, `pivot()` and `export()` were methods on the kit taking a `RunRef`, because the frame store lived on the kit. The kit's delegates hid the real signatures behind `**kwargs`, on the one object `describe_book()` exists to make un-guessable.
- `validate()` ran the engine and then added five checks the engine "had no reason to make" (`CK-E011`, `CK-E012`, `CK-W004`, `CK-W006`, `CK-I001`), so `run().diagnostics` and `validate()` disagreed by design and a de-duplication rule lived in the validator.
- `ScenarioSet` offered `resolution()`, `resolve()` and `diagnostics()` for one computation; `resolve()` returned the book and dropped the diagnostics, with a docstring asking callers to fetch them separately. The service called it twenty-six times and never did.

## Decision

- **`RunRef` carries the kit it came from** and gains `frame()`, `pivot()` and `export()`, with explicit signatures. `compare(runs)` and `read_export(path)` stay on the kit, since they are not about one run. No kit method takes `**kwargs`.
- **The authoring checks move into `compile_book`.** A run's diagnostics are the whole catalogue a book can produce, in both engines (the reference engine shares the compiler, so the dual-engine gate covers them). `validate(scenario)` is `ordered(run(scenario).diagnostics)`: errors first, de-duplicated, nothing computed twice. The checks skip synthetic items, so a delta recompile over the engine's augmented book reports what a full run reports.
- **`resolve(scenario)` returns a `Resolution`** — `book`, `origins`, `removed_by`, `event_overlays`, `diagnostics` — on the scenario set and on the kit. `resolution()` and `diagnostics()` are gone. A resolved book never travels without the problems resolution refused to guess about.
- **`history(item=, field=)` replaces `blame(item, field)`**, which was that call with a larger limit.

## Alternatives considered

- **Hang the frame store on `RunRef` directly**: rejected. The store is one per kit, opened lazily, and shared by `compare`; the run keeps a reference to the kit instead.
- **Keep `validate()` as a superset of the run**: rejected. Two implementations of "what is wrong with this book" is the drift the dual-engine gate exists to prevent, one layer up.

## Risks and implications

- Books with a regime and no `cat:tax` items now see `CK-I001` on every run, and a book with withholding and no remittance leg sees `CK-W004`. Both are information the validator already reported; tests asserting exact diagnostic sets were updated to expect them.
- `RunRef` holds a reference to its kit; a run outlives nothing it did not already outlive.

Related: [[0021-engine-is-content-free]], [[0031-one-scenario-addressed-write-path]], [[0033-read-only-kit]]
