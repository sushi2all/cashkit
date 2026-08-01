# Handoff — Session S2 (Phases 2–4: the evaluation engine)

**Date** 2026-08-01 · **Status** Phases 2, 3 and 4 gates passed, committed.

## How this session ran

S2 ran in two stretches. The first agent built Phase 2 end to end and roughly
half of Phase 3, then was killed repeatedly by platform infrastructure — a
stream watchdog, not a quality failure. The orchestrator committed its working
state verbatim (`1a9bcf6 wip(phase-3)`) and respawned the session fresh. This
note covers both stretches; commits `3e2a1bb`, `101d31d` and `1a9bcf6` are the
first agent's, `309adcb` onward are the second's.

The bet that the repository is the only channel between sessions held here at a
finer grain than intended: the second agent picked up half-finished Phase 3 code
from `DECISIONS.md`, the ADRs and the code itself, with no context carried over.
One thing had been lost — `columns.py` and `reference/engine.py` both cited
`DECISIONS D-P2-20`, an entry the interruption had prevented from being written.
It has been reconstructed from the code and recorded.

## What was built

### Phase 2 — the reference engine and the formula front-end

- `cashkit/engine/numeric.py` — int64 minor units at 4 dp. Exact integer
  rounding under a declared policy (half-up default, banker's selectable),
  sign-symmetric so a credit note rounds as the mirror of the invoice it
  reverses. Products widen to Python ints rather than wrapping; every stored
  column is checked against an addition-safe ceiling. The ADR-0002 Decimal
  escalation factor table is computed at a precision that makes the factor
  exact, and applied as an exact integer ratio (D-P2-12).
- `cashkit/engine/calendars.py` — `Duration` arithmetic with month-end
  clamping, business-day rolls on Python weekday numbering (C-P1-01), a period
  index for every grain, and recurrence expansion phased on the segment start
  (D-P2-03).
- `cashkit/engine/formula.py` — the PRD §5.4 symbol table as a closed node set.
  Numeric literals are read from the source text, so no float ever appears;
  `where`, never `if_`; the selector grammar.
- `cashkit/engine/graph.py` — the dependency graph including `prev()` edges,
  Tarjan condensation, `agg()` selectors resolved to concrete ids at build
  time, illegal cycles named and their members disabled.
- `cashkit/engine/result.py` — int64 columns plus diagnostics: the single type
  both engines return, which is what makes the dual-engine gate a byte
  comparison rather than a tolerance check.
- `cashkit/reference/engine.py` — the naive `Decimal` oracle. Independent
  arithmetic for every rounding boundary, in the canonical order of ADR-0003,
  with cutover suppression (ADR-0004). A permanent deliverable.

### Phase 3 — condensation, columns, and the sequential fold

- `cashkit/engine/expand.py` — vectorized segment expansion: occurrence dates
  as an ordinal array, escalation as a binary search over anniversary
  boundaries plus one integer multiply per distinct factor, probability another
  multiply, accrual a scatter-add. Settlement split into `split_legs` (the
  arithmetic, shared by both evaluation tiers) and `leg_targets` (the calendar
  placement), with `FoldSettlement` resolving a derived item's landing periods
  once per run (D-P3-03).
- `cashkit/engine/columns.py` — every AST node as a masked column operation,
  with one evaluator over two kernels (array and scalar) so the promotion rules
  and division-flag propagation exist once.
- `cashkit/engine/fold.py` — **new in the second stretch.** The sequential tier
  compiles each feedback member's expression once into a closure over the
  period index, resolving everything that does not vary with `t`. Sound because
  the value kind of every node is statically determined, and because a rate can
  only come from a literal, a param or rate arithmetic — so every rate is a
  compile-time constant (D-P3-01).
- `cashkit/engine/run.py` — the two-tier evaluator and the delta path.

### Phase 4 — hardening

The language was built in Phase 2 (ADR-0001), so Phase 4 changed behaviour in
four places and otherwise added proof: dispatch by explicit table instead of by
name, bounds on source length, literal magnitude and `prev(n=)`, and `CK-E007`
for a malformed param key.

## What the gates proved

### Phase 2 — `tests/test_reference_engine.py`

A 20-item book (`tests/gate_book.py`) with multi-segment items, escalation
across an anniversary, share and fixed-amount splits, a clamped remainder, a
credit note through fixed terms, withholding, business-day rolls both ways, an
explicit schedule, an accrual-only item, probability weighting, `agg`/`cum`/
`where` formulas, a masked division by zero and a `prev()` feedback loop
between cash and quarter-end interest. Hand-verified against
`tests/fixtures/hand_verified.csv`; coverage of a verified period is complete —
every item and both measures, zeros included — so no value goes unlooked-at.

### Phase 3 — `tests/test_dual_engine.py`, `tests/test_performance.py`

**97 tests, 69 books, zero mismatches.** Exact integer equality on every cell of
both measures of every item, plus agreement on which diagnostics each book
raises. `tests/corpus.py` builds the corpus in three layers — focus books,
cross-product sweeps (anchor × business-day adjustment, settlement basis × leg
adjustment, recurrence unit, base grain) and seeded random books — and
`coverage_of` re-derives what the corpus exercises from the books themselves,
so the coverage claim tracks the corpus instead of restating it. Six books are
deliberately broken, because the engines must agree on diagnostics too.

Performance (M3 Pro, numbers and hardware in `BENCHMARKS.md`): cold run
**16.1 ms** against a 50 ms budget, delta recompute **4.25 ms** against 5 ms,
20-scenario sweep **87 ms** against 500 ms.

The gate found one defect: `Engine.delta` reported only the diagnostics of the
items it recomputed, so a warning silently disappeared when its item fell
outside the dependency cone. Fixed by bucketing runtime diagnostics per item
(D-P3-02).

### Phase 4 — `tests/test_formula_hardening.py`, `tests/test_builtins_vectorized.py`

**352 tests.** A 262-entry corpus of hostile and malformed sources — attribute
access, `__builtins__` and dunder walks, comprehensions, lambdas, walrus,
imports, f-strings, every disallowed operator, malformed calls to every
builtin, deep nesting, oversized sources — plus 1,600 generated strings and
token soup. Every one yields a `Diagnostic` with a `suggested_fix`; none
raises, none is accepted, none executes. Non-execution is proved twice: a
recorder wrapped around every dangerous builtin stays silent, and the parser's
own source is walked for calls that could execute anything.

`test_every_ast_node_type_is_decided` walks the `ast` grammar and asserts every
expression node type is either translated or rejected, so a Python release
adding a node type fails the gate rather than widening the language.

`tests/test_builtins_vectorized.py` proves every §5.4 symbol is a column
operation: the whole-horizon result equals the elementwise result, and both
equal a hand-written `Decimal` oracle. The required symbol set is derived from
the parser's own tables.

Two real holes were found and closed, both by the structural half of the gate:

1. Call dispatch was `getattr(self, f"_call_{name}")`, so every translator
   method whose name began with that prefix was reachable from a formula
   string. `numeric(1, 2)` reached the variadic-builtin handler with the wrong
   signature and raised `TypeError` out of the parser (D-P4-01).
2. `1e400` parsed and then raised `MoneyOverflowError` when promoted to a money
   column — an exception on book content, which the error policy forbids
   (D-P4-03).

## Decisions recorded

- **D-P2-01…19** — the first agent's, spanning the rounding policy as a run
  parameter, schedule semantics, recurrence phase, settlement defaults, the two
  value kinds, the escalation ratio, cutover in Phase 2, VAT deferred to Phase
  6, and the cross-session repair of a Phase 1 emitter defect (D-P2-19).
- **D-P2-20** — reconstructed: `where()` always yields money, never a
  per-period rate.
- **D-P3-01…05** — the staged fold; delta diagnostics; the settlement split;
  what may and may not be memoized; the corpus being deterministic with derived
  coverage.
- **D-P4-01…06** — the whitelist being the translator while the call surface is
  an explicit table; three parse bounds; literal and lag bounds; `CK-E007`;
  `agg()` resolution and self-membership; how non-execution is proved.

No new PRD conflicts. C-P1-01 (weekday numbering) remains the only one.

## Changes to earlier sessions' code

- Phase 1, first stretch: `cashkit/model/canonical.py` gained `\\uXXXX` escapes
  for U+FFFE/U+FFFF, which `yaml.safe_load` refuses (commit `101d31d`,
  D-P2-19). The golden fixture is byte-unchanged.
- Phase 3 code inherited from the first stretch was extended, not rewritten:
  `columns.py` gained exact-type dispatch and constant memoization, `expand.py`
  was split along the arithmetic/placement seam, `run.py` was rewired to the
  staged fold. The behaviour it already had was byte-identical to the reference
  engine on the gate book before any of that, and still is.

## First thing S3 should verify

Re-run `uv run pytest` — **615 tests must pass** (~12 s). Then, before touching
the ledger, read **D-P2-13 and ADR-0004 together**: cutover suppression is
already implemented on the *generative* side, in `expand_item`, and an
occurrence dated before cutover is dropped entirely including cash legs that
would have landed after it. Phase 5 owns only the *event* side of the union.
If S3 re-implements suppression rather than adding the ledger half on top, the
pre-cutover world will be double-suppressed and the total-sum invariant at the
boundary will not hold.

Two more things S3 should know:

- **The fact union must happen before derived evaluation.** `Engine._evaluate`
  expands every generative item before the component loop for exactly this
  reason; ledger events must enter at the same point, not after.
- **`Engine.delta` recompiles the graph on any item change** and that is not
  optional: `agg()` selectors resolve against tags at compile time, so editing
  a tag moves aggregate membership. Anything the ledger adds to the graph has
  to survive that recompile.

## Open items, deliberately not done here

- **VAT is not applied** (D-P2-14). The canonical rounding order stops after
  withholding; `Item.vat` is read by nobody and `VatSpec.rate` params are not
  resolved, so the engine never implies VAT support it does not have. Phase 6
  slots in at the declared position.
- **Test coverage is not measured.** The ≥90% target on `engine/` and `model/`
  is a definition-of-done item for the project, and measuring it needs
  `pytest-cov`, which would change the dependency set. Left for whichever
  session adds it deliberately.
- **Where the rounding policy is stored** is still open (D-P2-01). It is a run
  parameter today; if it must survive across machines it belongs on the Book,
  not in an untracked config file. Flagged for the session that introduces
  `.cashkit/config.toml`.
