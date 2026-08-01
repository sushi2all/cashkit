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

