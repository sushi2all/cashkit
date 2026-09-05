# ADR-0033 — A past revision is a type, not a flag

**Date** 2026-09-05 · **Status** accepted · **Type** architecture · **Source** SDK architecture review 2026-09-05

## Context

PRD §6.6 says `at(ref)` returns a kit, not a book, so `kit.at("HEAD~5").run("downside").summary()` works. The implementation returned a live `CashKit` with a `bound_to` flag, and every write checked the flag and returned `CK-E030`. Five refusal branches, one catalogue code, and a hazard the docstrings spent paragraphs on: a bound kit shares the live root and the live ledger, so a write that slipped past a missing check would read history and land in the present. Session S5.6 had already found one such hole after S5.5 closed another.

## Decision

**Two types, one shape.** `ReadOnlyKit` holds every read: `book`, `scenarios`, `resolve`, `provenance`, `diff`, `describe_book`, `events_for`, `query_events`, `reconcile`, `run`, `validate`, `compare`, `read_export`, `history`, `at`, `diff_revisions`, `reproduce`. `CashKit(ReadOnlyKit)` adds the writes: `init`, `open`, `set_item`, `remove_item`, `unset`, `set_param`, `apply_macro`, `set_book`, `fork`, `flatten`, the four ledger writes, `commit`, `status`, `discard`. `at(ref)` returns a `ReadOnlyKit`.

A write on the past is therefore not a diagnostic to remember to return but a method that does not exist. `bound_to` becomes `revision: str | None`, read by `events_for` to truncate the ledger to the revision's watermark (ADR-0006) and by `run()` to key the run. `CK-E030` is retired from the catalogue: nothing can emit it.

## Alternatives considered

- **Keep the flag and add a structural test that every write checks it**: rejected. The test would enumerate the very list the type system enumerates for free, and a verb added under a different name would still be a hole until someone extended the list.
- **Return a `Book` from `at()`**: rejected by the PRD; the point of `at()` is that eras of the model compare through one API.

## Risks and implications

- `tests/test_construction.py` asserts structurally that `CashKit` adds exactly the write verbs and redefines no read, so a verb added later lands on the right side or the test fails.
- The read-only kit shares the live ledger object; safety now rests on the absence of write methods, not on a check inside each.

Related: [[0006-watermark-at-commit]], [[0012-actual-corrections-append-only]], [[0031-one-scenario-addressed-write-path]]
