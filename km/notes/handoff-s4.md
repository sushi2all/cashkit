# Handoff — Session S4 (Phases 7–8: scenarios, frames)

**Date** 2026-08-05 · **Status** Phases 7 and 8 gates passed, committed.
**Suite** 759 → **866** passing (~21 s).

## What was built

### Phase 7 — scenarios

- **`cashkit/sdk/scenarios.py`** — `ScenarioSet`, holding the authored book and
  its scenarios, with the whole PRD §6.3 surface: `fork`, `set_item`,
  `set_param`, `unset`, `remove_item`, `apply_macro`, `resolve`, `diff`,
  `provenance`, `flatten`, plus `resolve_events` for the ledger side. In memory
  and storage-free — persisting scenarios is S5's config store, and the phase is
  testable without a filesystem.
- Resolution walks the chain root to leaf applying `removed`, then `added`, then
  the recorded fields of each overlay, so the nearest ancestor that *recorded* a
  field wins (ADR-0009). `segments` arrives whole or not at all; there is no
  merge routine that could be asked to do otherwise, and a structural test walks
  the SDK's own source for `segments[...]` subscripts and `zip`/`enumerate` over
  segments.
- **`cashkit/sdk/macros.py`** — `ShiftItems`, `ScaleItems`, `RetagItems`, each a
  pure function from a matched item to the item as it should become. They route
  through `set_item`, so a macro that changes nothing records nothing.
- **`Provenance`, `ScenarioDiff`, `ItemDiff`, `ParamDiff`, `FieldOrigin`** join
  `ChangeReport` in `cashkit/model/reports.py`, as S3's handoff asked.
- Four catalogue codes, `CK-E021`…`CK-E024`.

### Phase 8 — frame store and views

- **`cashkit/stores/frames.py`** — `FrameStore` protocol and
  `DuckdbFrameStore`. Tidy/long facts, one row per
  `(period, item, measure, status)`; a period dimension carrying every coarser
  bucket; an item dimension; **tags in their own table**, joined on demand.
  `duckdb` is imported here and nowhere else in the package, asserted by walking
  every module's imports.
- **`cashkit/sdk/views.py`** — `summary()` and `balance_series()`, computed off
  the int64 columns with no `duckdb` and no `cashkit.stores` import. "When do we
  run out of cash" is the question the system exists to answer and it works on a
  core install.
- **`cashkit/model/table.py`** — `Table`, a frozen dataclass of columns and rows.
  No pandas, no polars; money is `Decimal` on the way out.
- `bucket_of` and `GRAIN_COLUMN` were added to `engine/calendars.py` in their
  own commit (see *Changes to earlier sessions' code*).

## What the gates proved

### Phase 7 — `tests/test_scenarios.py` (61 tests)

`set_item()` with an unchanged item returns an empty `ChangeReport` carrying
`CK-I002` **and leaves the scenario byte-identical to a freshly forked one** —
"writes nothing" is asserted against the store, not only against the report, and
again one level down when rewriting an existing override with its own value.

A three-level chain (`base → mid → leaf`) resolves per field: leaf's records
win, mid's win over base for fields leaf did not touch, base falls through for
everything nobody recorded, and each level records exactly its own fields —
`mid` records `{tags}`, `leaf` records `{name, settlement}`.

Correcting `tags` in base propagates into a child that overrode a *different*
field and does not propagate into one that overrode `tags`. Checked twice: once
through base's overlay, once by correcting the top-level authored book, because
ADR-0007 makes those the same operation with different storage.

Two scenarios reaching the same state by different routes — two levels with one
field each plus a param set and reverted, versus one level with both fields —
`diff()` empty while their overlays differ, and the diff asserted non-empty
against base so it is not vacuous.

Twenty-scenario sweep including chain resolution: **96 ms against 500 ms**.

### Phase 8 — `tests/test_frames.py` (43 tests)

Aggregating a day-grain frame to **week, month, quarter and year** preserves
flow totals with `Decimal` equality on both measures. The stock takes
last-in-period, asserted cell by cell against the base column *and* asserted to
differ from the sum of the levels in the bucket, so the rule cannot pass by
coincidence.

A tag-sliced sum equals the sum of the corresponding items for five selectors —
a single tag, two ANDed tags, a flag term, a mixed tag+flag term — with the
resolved item set asserted alongside the number.

Parquet round-trips exactly: 5,840 rows compared row by row, values still
`Decimal`, and again on a book whose settlement split produces
`3333.9999`/`6666.9999`-style values.

Materialization of the PRD §5.2 shape (182,600 fact rows): **116 ms against the
200 ms budget**, after replacing per-value parameter binding — which cost 4.7 s
— with a column-wise numpy path. Full numbers and the three insertion paths
measured are in `BENCHMARKS.md`.

## Changes to earlier sessions' code

- **`cashkit/engine/calendars.py`** (own commit `9d56c58`, D-P8-02): added
  `bucket_of()` and `GRAIN_COLUMN`. Purely additive. Grain buckets must agree
  with `PeriodIndex.is_quarter_end` and the VAT return periods on what a quarter
  is; a second statement of the fiscal convention inside the frame store would
  drift, and the drift would be invisible — the frame would quietly disagree
  with the F24 schedule about which quarter a number belongs to. It also keeps
  `summary()` free of the DuckDB extra.
- **`cashkit/model/reports.py`**, **`cashkit/model/__init__.py`**: new result
  models (`Provenance`, `ScenarioDiff`, `ItemDiff`, `ParamDiff`, `FieldOrigin`,
  `RunSummary`) and `Table`. Additive; nothing existing changed shape.
- **`cashkit/model/diagnostics.py`**: `CK-E021`…`CK-E024`.
- **Lints**: `sdk/` joined the wall-clock and no-float audits. A `ShiftItems`
  macro reading the clock would make a resolved book depend on when it was
  resolved.
- **`pyproject.toml`**: `duckdb` joins the **dev** group (it stays an optional
  runtime extra). `pyarrow` deliberately not added — DuckDB reads and writes
  Parquet natively.

## Decisions recorded

**D-P7-01…14** — resolution lives in `sdk/`; four new codes; `changed` means the
fields whose *record* moved; `removed`→`added`→`items` ordering inside one
scenario; a book carrying synthetic items is refused with an exception;
`flatten` produces `parent=None` against the authored book; `fork`/`flatten`
return `ChangeReport`; macros round at the authoring boundary; `diff` covers
`opening_balance` and event overrides; event overrides never write the ledger;
pinning a parent's value is deliberately inexpressible; the change-path naming;
`opening_balance` money-checked twice; `sdk/` joins the lints.

**D-P8-01…15** — one module imports duckdb; `bucket_of` in `calendars.py`;
calendar-aligned buckets; a stock never sums; only `mean` rounds and it rounds
like the engine; **`status` stays what the run reported**; minor units reach
`DECIMAL(18,4)` through their decimal string; column-wise insertion; `Table` is
not a DataFrame; synthetic items materialized and flagged; `summary()` outside
the extra; min-cash and runway read at base grain; the `(untagged)` pivot column;
`compare` reports `None` not zero; duckdb in the dev group.

**C-P8-01** — new PRD conflict: §5.5's example frame row shows an inclusive
`period_end`, contradicting §4.0's half-open `PeriodRange`. Implemented
half-open throughout.

## First thing S5 should verify

Re-run `uv run pytest` — **866 tests must pass** (~21 s). Then read
**D-P8-06** before touching anything that reports status: the frame carries one
status per run because a cell is a *sum* of contributions whose statuses can
differ, and splitting the value by status needs the engine to emit per-status
columns. That is an engine change touching `settle_occurrences`, both engines
and the byte-equality gate — out of S4's scope, and the alternative (re-deriving
leg placement inside the store) is the second-implementation drift the
dual-engine gate exists to prevent. S3 handed this to Phase 8; Phase 8 hands it
on with the reason written down rather than a guess in the column.

Three more things S5 should know:

- **`ScenarioSet` refuses a book carrying `_event:`/`_tax:` items** (D-P7-05).
  When you wire `at(ref)` and `commit()`, keep the authored book and the
  engine's augmented book distinct — `Engine.book` is the augmented one, and the
  frame store wants *that* while scenario resolution wants the other. The two
  requirements are mirror images and both are asserted.
- **`Scenario` is not yet persisted.** `ScenarioSet` is in memory; the config
  store (`scenarios/*.yaml`, ADR-0007's layout) is S5's, and the canonical
  emitter already round-trips `Scenario` from Phase 1. `flatten` and
  `provenance` will need the store to preserve overlay recordedness — the
  emitter does, via recorded-`None` (D-P1-01), but it is worth a round-trip test
  on a chain rather than on a single scenario.
- **The rounding policy is still unstored** (D-P2-01, open since S2). The frame
  store now has one too (`DuckdbFrameStore(policy=...)`, used by `mean`
  aggregation), so the session that introduces `.cashkit/config.toml` has two
  callers to satisfy, not one.

## Open items, deliberately not done here

- **Per-cell `status`** — above, D-P8-06.
- **An attached event's own tags still do not move `agg()` membership**
  (D-P5-09). S3 asked Phase 8 to surface per-event tags on the frame row; it
  does not, because the frame's grain is `(period, item, measure)` and an event's
  tags belong to the event, not to the cell it contributes to. Surfacing them
  needs an event dimension table keyed by event id — a small addition once the
  frame carries event-level rows, which is the same change as per-cell status.
  Both wait on the same decision.
- **`retag(book, selector, tags)`** (PRD §6.1) is not implemented: it is the
  *construction* side, and `RetagItems` covers the scenario side. It belongs
  with `add_item`/`add_derived` in the construction SDK.
- **Test coverage is still not measured** (open since S2): the ≥90% target needs
  `pytest-cov`, which would change the dependency set.
- **`km/adr/0015`…`0020`, `km/adr/pending-spec-updates.md` and
  `km/notes/architecture-deck.html`** appeared in the working tree during this
  session and were **not** written by it; `km/adr/index.md` was modified to list
  them. All left untracked and unstaged. Nothing in them conflicts with Phases
  7–8 (ADR-0016 forbids an LLM dependency inside `cashkit/`, and there is none),
  but ADR-0018 — "the revision store is an interface; git is one implementation"
  — bears directly on S5's Phase 9 and should be read before it starts.
