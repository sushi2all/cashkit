# Handoff — Session S3 (Phases 5–6: facts entering evaluation)

**Date** 2026-08-05 · **Status** Phases 5 and 6 gates passed, committed.
**Suite** 615 → **759** passing (~17 s).

## What was built

### Phase 5 — the ledger and the fact union

- **`cashkit/stores/ledger.py`** — SQLite, and the only module in the package
  that imports `sqlite3`. One append-only table, `ledger_entries`: an event is
  an `event` entry, voiding one is a `void` entry naming it, correcting one is
  a `void` plus an `event` carrying `corrects`. No `DELETE`, no `UPDATE` — the
  ADR-0006 watermark is a sequence number over that log, and `at(ref)`
  truncation is `seq <= max_rowid` and nothing else.
  `import_events` is idempotent on `(source, ext_id)`; conflicts abort the
  batch; `void_event` refuses a bare actual and names `correct_event`;
  `correct_event` tombstones and appends, inheriting status, requiring a note.
- **`cashkit/engine/facts.py`** — the event side of the union. Resolves each
  event to the column it lands in, which is its item's when it has one and a
  synthetic dimension carrier when it does not. Runs **before** the graph is
  built, because `agg()` selectors resolve to concrete ids at graph-build time
  and a carrier created later would be invisible to every aggregate.
- **`cashkit/model/reports.py`** — `ChangeReport`, `ImportReport`, `EventRef`.
  Phase 7 will return `ChangeReport` from scenario writes, so they live with
  the model rather than in a store.
- Both engines gained `run(book, events=...)`. Neither knows a ledger exists:
  assembling the sequence — tombstones out, corrections in, watermark applied —
  is `LedgerStore.facts()`.

### Phase 6 — VAT and tax regimes

- **`cashkit/engine/vat.py`** — per-line VAT, closing the canonical rounding
  order at the position D-P2-14 reserved for it. The cash leg is grossed up; a
  line's VAT is computed once and *allocated* across its legs with the last
  absorbing the residual.
- **`cashkit/engine/tax.py`** — regimes as `_tax:<id>:liability` (flow) and
  `_tax:<id>:credit` (stock), injected into the graph before condensation
  (ADR-0005). The schedule folds over *return* periods — twenty in a five-year
  day-grain book, not 1,826. Also `tax_diagnostics()`, which produces `CK-W004`
  and `CK-I001` ready for Phase 10's `validate()` to absorb.
- `RunResult` carries four VAT columns per VAT-bearing item (output and input,
  on each tax point) and the dual-engine gate compares them.

## What the gates proved

### Phase 5 — `tests/test_ledger.py`, `tests/test_fact_union.py` (62 tests)

A **5,000-row CSV imported three times**: the second and third report 5,000
skips, insert nothing, and leave `state_digest()` — a fingerprint over every
column of every log entry — byte-identical. It stays idempotent when the source
regenerates its surrogate ids, because identity is `(source, ext_id)` and the
row's own `id` is excluded from the payload comparison (D-P5-02).

The **total-sum invariant** is checked in its strongest form: the same economic
world is built at *every* cutover position from the horizon start to its end,
with the ledger carrying exactly the reconciled months, and the whole-horizon
totals must not move. A double-count makes them grow as cutover advances; a gap
makes them shrink. Checked on both engines, in total and cell by cell, with
60-day terms so a pre-cutover leg lands after the boundary and any double-count
shows up in cash rather than accrual.

Append-only-ness is proved structurally as well as behaviourally: the entry
count only grows, a correction adds exactly two rows, an idempotent re-import
adds none, and the module's own source is searched for `DELETE`/`UPDATE`.

### Phase 6 — `tests/test_vat.py` (71 tests)

A six-line fixture entity — standard-rate sale on 60-day terms, reduced-rate
sale, exempt sale, reverse-charge purchase, deductible purchase, car lease at
the Italian 40% deductibility — on quarterly accrual-basis VAT with the 1% *IVA
trimestrale* surcharge reproduces `tests/fixtures/f24_schedule.csv` exactly on
both engines: 9,780.00 output, −3,841.20 input, 5,938.80 net, 59.3880
surcharge, **5,998.1880** paid on 16 April, 16 July, 16 October and 16 January.
The CSV carries the per-line arithmetic in its header, so the expected numbers
are readable without running anything.

A second fixture with input above output for two consecutive quarters shows a
credit stock of 5,940 then 11,880, **no payment at all** while it stands, and
7,920 when it is consumed — never a negative payment. Flipping `measure` to
`"cash"` moves the first F24 from 5,998.19 to 1,554.19, which is the
working-capital hole the accrual tax point creates.

### Dual-engine equality, extended twice

**82 books, 118 tests, zero mismatches.** The corpus gained four ledger cases
and nine VAT cases, and `coverage_of` re-derives thirty new labels from the
books themselves, so the coverage claim still cannot rot into a stale list. The
comparison now includes the VAT columns: two engines agreeing on every cash cell
while disagreeing about which return period a line's VAT fell into would produce
the right bank balance and the wrong F24.

Performance (M3 Pro, full numbers in `BENCHMARKS.md`): cold run 16.9 ms without
VAT and 28.1 ms with every flow VAT-bearing, both against a 50 ms budget; delta
4.29 ms and 4.93 ms against 5 ms. The 5,000-row ledger adds 12 ms to a cold run.

## Changes to earlier sessions' code

- **`cashkit/model/primitives.py`** (own commit `a714d0b`, D-P6-08):
  `Diagnostic.item_id` was typed `ItemId`, whose grammar deliberately excludes
  the ids the engine synthesizes, so *any* diagnostic about one raised
  `ValidationError` out of the engine on ordinary book content. Widened to a
  `DiagnosticSubject`. `ItemId` is untouched — authored ids still cannot start
  with `_`, which is exactly why a synthetic id can never collide.
- **`cashkit/engine/expand.py`**: `_scatter` became public `scatter_add`;
  `split_legs` now returns a `SplitLegs` carrying both the pre-withholding and
  post-withholding legs (VAT rides the former, D-P6-02); `settle_occurrences`
  and `FoldSettlement.apply` take an optional VAT sink.
- **`cashkit/engine/graph.py`**: `compile_book` takes an optional `PeriodIndex`
  and injects the regimes' synthetic items before condensation; it now resolves
  `VatSpec.rate` params (`CK-E008`), which Phases 2–4 deliberately did not.
- **`cashkit/engine/run.py`**, **`cashkit/reference/engine.py`**: the fact union
  and the VAT/tax paths. `RunResult` gained a `vat` field.
- **Catalogue**: six new codes (`CK-E014`…`CK-E019`) and a rewritten
  `suggested_fix` on `CK-E010` naming `correct_event` (ADR-0012 §4).
  `tests/test_diagnostics_catalogue.py` now asserts the catalogue equals the PRD
  set *plus an explicitly enumerated additions set*, so growth stays deliberate.
- The wall-clock and no-float lints now cover `stores/` too.

## Decisions recorded

**D-P5-01…17** — the single append-only log; import identity excluding `id`;
keyless rows refused; the new codes; corrections keeping `source` but not
`ext_id`; derived correction ids; the referential check order; events never
suppressed by cutover; synthetic carriers for unattached events; `model_construct`
keeping `ItemId` strict; events refused on derived items; events outside the
horizon; the watermark hashing voids; JSON payloads with a canonical-YAML
identity digest; where the reports live; the engine taking a sequence rather
than a store; and the union preceding graph construction.

**D-P6-01…11** — VAT per line and allocated; VAT on the taxable amount rather
than on what withholding leaves; `direction` deciding the side; what each
treatment does; signed contributions so the net is a sum; return periods on the
fiscal year; tax items as schedule-filled graph nodes; the `Diagnostic` widening;
an event's VAT reaching a regime only through its item; `refund_annual`; and VAT
columns being part of the gate.

No new PRD conflicts. C-P1-01 remains the only one.

## First thing S4 should verify

Re-run `uv run pytest` — **759 tests must pass** (~17 s). Then, before touching
scenarios, read **D-P5-09 and D-P5-10 together**: the engine now puts items into
`Book.items` that no one authored — `_event:<digest>` carriers and
`_tax:<regime>:*` — and it does so by `model_copy`, which does not revalidate.
Scenario resolution must never see them: they are runtime products of the ledger
and the regimes, they are rebuilt on every compile, and an overlay that recorded
one would be resurrecting a value the next run recomputes. `Scenario.items`,
`added` and `removed` should be resolved against the *authored* book, and the
synthetic ids should be filtered out of anything an agent can address.

Three more things S4 should know:

- **`compile_book` now owns the tax items**, so `Engine.book` and
  `_Reference.book` are the *augmented* book, not the one passed in. Anything
  that needs the authored book must keep its own reference to it.
- **`ChangeReport` already exists** in `cashkit/model/reports.py` with
  `target` / `changed` / `created` / `diagnostics` and an `empty` property that
  Phase 7's `CK-I002` case wants. Extend it rather than minting a second one.
- **`Engine.delta` recompiles and re-resolves the facts**, so a scenario sweep
  that changes tags moves both `agg()` membership and a regime's base. That is
  correct and it is also the reason both are resolved at graph-build time.

## Open items, deliberately not done here

- **An attached event's own `tags` do not move `agg()` membership** (D-P5-09).
  They are row metadata; making them move membership would require the event to
  be its own dimension row, contradicting §5.5's item-dimension design. Phase 8
  should surface per-event tags on the frame row, and `validate()` should warn
  when an event's tags would change the membership of a selector its item does
  not match. This is the one place in S3's work where a user could be surprised
  by a number, and it is written down rather than papered over.
- **`RunResult.rows()` still stamps every row `status="forecast"`.** The ledger
  now knows each fact's real status; the frame layer (Phase 8) should carry it,
  which is what makes `frame(status=...)` in PRD §6.4 mean anything.
- **An event dated outside the horizon is dropped, cash legs included**
  (D-P5-12), the same rule generative occurrences follow. Opening receivables
  are therefore invisible. Recorded as a known limitation, not a decision anyone
  should be happy with.
- **Test coverage is still not measured** (S2's open item, unchanged): the ≥90%
  target needs `pytest-cov`, which would change the dependency set.
- **Where the rounding policy is stored** is still open (D-P2-01), still waiting
  for the session that introduces `.cashkit/config.toml`.
- `km/notes/architecture-deck.html` appeared in the working tree during this
  session and was **not** written by it. Left untracked.
