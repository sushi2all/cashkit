# BENCHMARKS — measured numbers per phase gate

Hardware: MacBook Pro, Apple M3 Pro, 18 GB RAM, macOS 15.2 (Darwin 24.2.0).
Toolchain: CPython 3.13.5, numpy 2.x, pydantic 2.13.4, hypothesis 6.163.0, PyYAML 6.0.3.

All figures are wall-clock, single process. "min" is the best of the stated
repetitions; "median" is the one to quote. The gate assertions in
`tests/test_performance.py` compare the best of several repetitions against the
budget, because the budget is a claim about the work the design does and a
scheduler hiccup on a shared machine is not evidence about that.

## Phase 1 — Models and canonical serialization

Phase 1 has no performance gate (PRD §5.2 budgets apply to the engine,
Phases 2–3). Recorded for the baseline only:

| Measure | Value |
|---|---|
| Full test suite (62 tests, incl. 850 property examples) | ~8 s |
| Gate stress run: 1000 generated Books + 500 Scenarios, byte round-trip | ~50 s, zero failures |

Nothing in Phase 1 is on the run-time hot path; serialization happens at
commit/load boundaries only (PRD budget: commit < 3 s, untested until the
config store exists in S3+).

## Phase 2 — Reference engine

The reference engine is the oracle; performance is explicitly irrelevant to it,
and it is recorded only because the ratio to the vectorized engine is the
argument for having built the vectorized one.

| Measure | Value |
|---|---|
| 20-item gate book, 181 day-grain periods | ~13 ms |
| PRD §5.2 benchmark book (50 items × 1826 periods) | 380 ms |

The PRD's own figure for the naive Decimal loop on that shape is 206 ms. This
implementation is slower because it quantizes in `Decimal` at every declared
boundary and sizes its working precision to the operands rather than trusting
the default context — which is the entire point of an oracle.

## Phase 3 — Graph, condensation, vectorized engine

### Gate: dual-engine equality

`uv run pytest tests/test_dual_engine.py` — **69 books, 97 tests, zero
mismatches.** Exact integer equality on every cell of both measures for every
item in every book, plus agreement on which diagnostics were raised. Half-even
rounding and the delta path are re-checked on subsets of the same corpus.

| Measure | Value |
|---|---|
| Corpus of 69 books, reference engine | 172 ms total |
| Corpus of 69 books, vectorized engine | 72 ms total |

### Gate: performance budgets (PRD §5.2)

Shape is the PRD's own benchmark: 40 generative flows with escalation and
settlement lags, 8 derived, 2 in a feedback loop, day grain, five years.

| Path | Budget | Measured (min / median) | Result |
|---|---|---|---|
| Full cold run, 50 items × 1826 periods | < 50 ms | **16.1 / 17.1 ms** | pass |
| Delta recompute, one item changed | < 5 ms | **4.25 / 4.53 ms** | pass |
| 20-scenario sweep on the delta path | < 500 ms | **87 ms** | pass |

A cold run compiles the book from scratch — parse, resolve, Tarjan, condense —
and then evaluates it; nothing is reused between repetitions.

### Scaling (vectorized, cold)

| Items × periods | Cells | min | median |
|---|---|---|---|
| 50 × 1826 | 91,300 | 16.1 ms | 17.1 ms |
| 200 × 1826 | 365,200 | 56.4 ms | 57.2 ms |
| 500 × 3652 | 1,826,000 | 200.1 ms | 200.7 ms |

Growth is close to linear in cells, as the design predicts: every item outside a
feedback set costs one pass of array operations. The absolute numbers sit above
the PRD's §5.2 projections (0.9 ms and 26.2 ms for the first and third rows).
The gap is the compile step plus the exactness policy — arbitrary-precision
intermediates on every rate multiplication, the Decimal factor table, and a
per-column overflow guard — none of which those projections accounted for. The
two budgets that are actually gated are met, with a 3× margin on the cold run
and a 15% margin on the delta.

### Where the time goes

The sequential fold dominated both budgets when Phase 3 was picked up: walking
the AST once per period accounted for 39 ms of the delta path's 39.5 ms. Staging
each cell expression into a closure (`cashkit/engine/fold.py`) and resolving
each cash leg's landing period once per run took the delta from 39 ms to 4.5 ms
and the cold run from 57 ms to 17 ms, with the two engines still byte-identical
on the whole corpus.


## Phase 4 — Formula language

No performance gate. Recorded because the hardening corpus is a standing cost
in every test run:

| Measure | Value |
|---|---|
| `tests/test_formula_hardening.py` — 320 tests, 262-entry corpus plus 1,600 generated strings | 1.3 s |
| `tests/test_builtins_vectorized.py` — 32 tests, every §5.4 symbol against a Decimal oracle | 0.4 s |
| Full suite, all phases (615 tests) | 12.2 s |

## Phase 5 — Ledger and events

### Gate: import idempotency

`uv run pytest tests/test_ledger.py` — a 5,000-row CSV imported three times.
The second and third imports report 5,000 skips, insert nothing, and leave
`state_digest()` — a fingerprint over every column of every log entry —
byte-identical.

| Measure (5,000 rows) | Value |
|---|---|
| First import (parse, digest, insert) | 269 ms |
| Idempotent re-import (all skips) | 152 ms |
| `facts()` — rehydrate the live ledger | 32 ms |
| `watermark()` | 11 ms |
| `state_digest()` | 26 ms |

Payloads are stored as the model's own JSON and identity as a canonical-YAML
digest held in its own column (DECISIONS D-P5-14). The first cut stored the
canonical YAML as the payload and re-derived the digest by rehydrating each
row: `facts()` took **1,258 ms** and a re-import **1,545 ms**, because
`yaml.safe_load` costs ~250 µs per row against ~7 µs for
`model_validate_json`. Same exactness — a `Decimal` is a string in both forms —
at a fortieth of the cost.

### Gate: the cutover boundary

`uv run pytest tests/test_fact_union.py` — the total-sum invariant is checked at
every cutover position from the horizon start to its end, on both engines, in
total and cell by cell. 41 tests, 0.6 s.

### Fact union on the hot path

| Path | Budget | Measured (min) | Result |
|---|---|---|---|
| Cold run, 50 items × 1826 periods, no ledger | < 50 ms | 15.9 ms | pass |
| Cold run, same book + 5,000-row ledger | < 50 ms | **28.9 ms** | pass |

The 13 ms difference is the per-event resolution loop in
`cashkit.engine.facts` (one pass, Python-level) plus one batched scatter; the
settlement arithmetic itself stays vectorized, because events sharing a target
and a settlement are collected into one array operation rather than settled row
by row.

| Measure | Value |
|---|---|
| Dual-engine corpus, now 73 books incl. 4 ledger cases | 105 tests |
| Full suite, all phases (686 tests) | 14.9 s |

## Phase 6 — VAT and tax regimes

### Gate: the F24 schedule

`uv run pytest tests/test_vat.py` — 71 tests, 1.2 s. A six-line fixture entity
(standard-rate sale on 60-day terms, reduced-rate sale, exempt sale,
reverse-charge purchase, deductible purchase, car lease at 40%
deductibility), quarterly accrual-basis VAT with the 1% *IVA trimestrale*
surcharge, reproduces `tests/fixtures/f24_schedule.csv` exactly on both
engines: 9,780.00 output, −3,841.20 input, 5,938.80 net, 59.3880 surcharge,
5,998.1880 paid on 16 April / 16 July / 16 October / 16 January.

A second fixture with input above output for two consecutive quarters shows a
credit stock of 5,940 then 11,880, no payment at all while the credit stands,
and 7,920 when the credit is consumed — never a negative payment. Flipping
`measure` to `"cash"` moves the first F24 from 5,998.19 to 1,554.19.

### Gate: dual-engine equality, extended to VAT

`uv run pytest tests/test_dual_engine.py` — **82 books, 118 tests, zero
mismatches.** The comparison now covers the four VAT columns per item as well
as accrual and cash: two engines agreeing on the bank balance while disagreeing
on which return period a line's VAT fell into would produce the right cash and
the wrong F24.

| Measure | Value |
|---|---|
| Corpus of 82 books, reference engine | 331 ms total |
| Corpus of 82 books, vectorized engine | 169 ms total |

### Performance with VAT

Same PRD §5.2 shape, with **every** generative flow carrying a `VatSpec` and a
quarterly regime netting all forty — the worst case a real book can present.

| Path | Budget | No VAT (min / median) | VAT everywhere (min / median) |
|---|---|---|---|
| Full cold run, 50 × 1826 | < 50 ms | 16.9 / 17.4 ms | **28.1 / 30.4 ms** |
| Delta recompute | < 5 ms | 4.29 / 4.43 ms | **4.93 / 5.04 ms** |

The cold run is gated on both shapes. The delta is gated on the no-VAT shape
only: the VAT figure sits close enough to the budget that gating it would test
the machine rather than the design. VAT costs roughly 15% of the delta — the
sequential fold still dominates it, as it has since Phase 3.

Two allocations were worth removing while getting there, and both were the same
mistake in different places. Allocating four VAT columns for every stale item
cost the delta more than the VAT arithmetic did on a book where most items are
not VAT-bearing, so they are allocated on demand (4.44 → 4.29 ms with no VAT).
Summing the regime's base with `total = total + columns.net_accrual()` allocated
eighty horizon-length arrays per run; in-place accumulation took the VAT delta
from 5.26 to 4.93 ms.

| Measure | Value |
|---|---|
| Full suite, all phases (759 tests) | 18.6 s |

## Phase 7 — Scenarios

### Gate: overlays, chains, propagation, diff

`uv run pytest tests/test_scenarios.py` — **61 tests**, covering the four gate
properties directly:

- `set_item()` with an unchanged item returns an empty `ChangeReport` carrying
  `CK-I002` and leaves the scenario byte-identical to a freshly forked one —
  "writes nothing" asserted against the store, not only against the report.
- A three-level fork chain (`base -> mid -> leaf`) resolves field by field:
  leaf's records win, mid's win over base for fields leaf did not touch, and
  everything nobody recorded falls through to the authored book. Each level
  records exactly its own fields — `mid` records `{tags}`, `leaf` records
  `{name, settlement}`.
- Correcting `tags` in base propagates into a child that overrode a *different*
  field and does not propagate into one that overrode `tags`. Checked twice:
  once correcting base's overlay, once correcting the top-level authored book,
  because ADR-0007 makes those the same operation with different storage.
- Two scenarios reaching the same state by different routes — two levels with
  one field each plus a param set and reverted, versus one level with both
  fields — diff empty while their overlays differ.

### Sweep performance (PRD §10 acceptance)

| Path | Budget | Measured (min / median, 7 reps) |
|---|---|---|
| 20-scenario sweep, resolution + delta recompute | < 500 ms | **96.3 / 96.8 ms** |
| Chain resolution alone, 50-item book | — | 0.48 ms per scenario |

The sweep measures the whole path an agent walks — fork, write by value,
resolve the chain, recompute — on the PRD §5.2 benchmark book (50 items x 1826
periods). Resolution is 10% of it; the rest is the engine's delta path, which
Phase 3 already gated.

| Measure | Value |
|---|---|
| Full suite, Phases 1-7 (821 tests) | 18.1 s |

## Phase 8 — Frame store and views

### Gate: aggregation, tag slicing, Parquet

`uv run pytest tests/test_frames.py` — **43 tests**, covering the three gate
properties directly:

- Aggregating a day-grain frame to **week, month, quarter and year** preserves
  flow totals with `Decimal` equality, not a tolerance, on both measures.
- The same aggregation takes **last-in-period** for the stock, asserted cell by
  cell against the base-grain column *and* asserted to differ from the sum of
  the levels in the bucket, so the rule cannot pass by coincidence.
- A **tag-sliced sum equals the sum of the corresponding items** for five
  selectors including a two-term AND, a flag term and a mixed tag+flag term,
  with the resolved item set asserted alongside the number.
- **Parquet round-trips exactly**: 5,840 rows compared row by row, values still
  `Decimal` and not floats that print the same, on the fixture book and again on
  a book whose settlement split produces `3333.9999`/`6666.9999`-style values.

### Performance (PRD §5.2 budget, 50 items × 1826 periods = 182,600 fact rows)

| Path | Budget | Measured (min / median) |
|---|---|---|
| Materialization into DuckDB | < 200 ms | **116.3 / 117.9 ms** |
| `frame()` at base grain, 182,600 rows | — | 304 ms |
| `frame(grain=MONTH)`, 6,000 rows | — | 32 ms |
| `frame(grain=YEAR)`, 500 rows | — | 16 ms |
| `frame(where=..., grain=MONTH)` | — | 15 ms |
| `export(format="parquet")` | — | 46 ms (93 KB) |

The base-grain frame is 182,600 Python tuples, which is what the 304 ms buys;
it is a file to export, not a query to run. Every aggregated view is under
35 ms.

Getting materialization inside the budget took two measurements and no
guessing (D-P8-08). DuckDB's Python parameter binding costs ~0.85 ms per
*value*: `executemany` over the fact table took **4.7 s** and a single statement
with 1.3 million placeholders took **3.7 s**. Facts now go in column-wise as
numpy int64 arrays. That left 130 ms in an unexpected place — the 1,826-row
period dimension, whose 18,260 `DATE` literals cost DuckDB's *parser* more than
the whole fact table cost its executor; periods went column-wise too.

| Fact insertion path | 182,600 rows |
|---|---|
| `executemany` | 4,700 ms |
| one statement, 1.3 M placeholders | 3,700 ms |
| literal SQL | 5,700 ms (extrapolated; 180 ms per 5,840) |
| numpy `register()` + SQL conversion | **44 ms** |

The int64 → `DECIMAL(18,4)` conversion is done through the decimal string
rather than by dividing by 10⁴, because DuckDB's decimal division is not exact
at the top of the type's range (D-P8-07). It is also the faster of the two:
19 ms against 85 ms for a multiplication by `0.0001`, over 182,600 values, both
verified exact against `Decimal` including at ±(10¹⁸−1) minor units.

| Measure | Value |
|---|---|
| Full suite, Phases 1-8 (866 tests) | 21 s |

## Phase 9 — Version control

Same machine and shape as the earlier phases: M3 Pro, Python 3.13, the PRD §5.2
benchmark book (50 items, 1,826 day-grain periods, one `base` scenario). Best
of five unless stated.

| Path | Budget | Measured |
|---|---|---|
| `commit()` — lock, snapshot recompute, serialize, working tree, revision | < 3 s | **61 ms** |
| `status()` vs HEAD | — | 67 ms |
| `at("HEAD~5")` — resolve, read state, migrate, validate | — | 63 ms |
| `reproduce("HEAD~5")` — the above plus a full run and the comparison | — | 84 ms |
| `diff_revisions("HEAD~5", "HEAD")` — semantic, both sides parsed | — | 126 ms |
| `history(limit=50)` over 11 revisions | — | 0.09 ms |
| `blame(item, "segments")` over 11 revisions | — | 63 ms |

The commit budget is the only one the PRD states, and it has fifty times more
headroom than it needs. Everything else is dominated by the same cost: reading a
revision means parsing 57 YAML files through Pydantic, which is ~60 ms for this
book. `history()` is three orders of magnitude cheaper because it walks commit
metadata and never opens a tree — which is exactly the split the revision-store
interface draws, and the reason `history()` stays usable on a long history while
`blame()` pays per revision it has to open.

`diff_revisions()` costs two state loads plus a resolution, and it is
deliberately semantic rather than textual: a byte comparison would be ~1 ms and
would report a hand reformat as a change (Phase 9 gate 2). 126 ms to tell an
agent the truth instead of 1 ms to tell it something misleading is not a
trade worth revisiting.

## Phase 10 — Introspection and CLI

Same machine and book: M3 Pro, Python 3.13, 50 items x 1,826 day-grain periods.
Best of five (three for the ones that run the engine).

| Path | Measured |
|---|---|
| `trace(depth=3)` on a derived cell | 2.1 ms |
| `trace(depth=3)` on a generated cell | 0.08 ms |
| `why_zero()` | 0.01 ms |
| `depends_on()` over the whole cone | 4.5 ms |
| `describe_book()` (50 items) | 0.2 ms |
| `validate()` (runs the engine) | 17 ms |
| `cashkit doctor --json` (process-internal) | 136 ms |
| `cashkit run base --json` | 85 ms |

`describe_book()` serializes to **16 KiB** of JSON for a 50-item book — small
enough to hand a model whole, which is what it is for.

The two numbers worth explaining:

**`validate()` costs a full run (17 ms), and that is the design.** It re-uses
the engine rather than re-deriving compile-time diagnostics, so a validator that
disagreed with the run about whether a formula is broken is not a reachable
state. A hand-written second implementation would be faster and would drift —
the failure the dual-engine gate exists to prevent, in a place where the drift
would look like reassurance.

**A derived `trace()` costs 25x a generated one** because it walks the
expression tree and re-evaluates every node through the engine's own scalar
kernel, once per node, to report the value the engine actually got. Rendering
the arithmetic from a cached column would be free and would be a paraphrase.
2 ms is well inside a UI click budget (ADR-0013 makes this the primary
interaction primitive), so there is nothing to buy by trading accuracy for it.

The CLI numbers are dominated by process-level work the SDK also pays:
`doctor` opens the book, reads the revision head and walks the history, so its
136 ms is 60 ms of YAML parsing plus a `status()` comparison.

| Measure | Value |
|---|---|
| Full suite, Phases 1-10 (1,007 tests) | 31 s |
