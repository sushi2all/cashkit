# CashKit

Deterministic cash-flow modelling engine with an SDK-only surface, built so
LLM agents can build, mutate, and interrogate financial forecasts without
touching the underlying data structures.

Three properties spreadsheets do not have:

1. **Time as a first-class axis** — values computed period by period over an
   explicit horizon, with `prev()` as the only sequential dependency.
2. **Reproducibility by construction** — a run is identified by
   `(config revision, scenario, engine version)`; nothing reads the wall clock
   during evaluation.
3. **Introspectability** — every computed number traces to its formula,
   bindings and arithmetic; every configured value traces to the scenario
   level and commit that set it.

## Status

Under construction, phase by phase. See `PROMPT-fable5-implementation.md` for
the phase plan and `PRD-cashkit.md` for the full specification.

| Layer | Status |
|---|---|
| `cashkit/model` — Pydantic models, canonical YAML serialization | Phase 1, done |
| `cashkit/reference` — naive Decimal oracle | Phase 2 |
| `cashkit/engine` — vectorized int64 engine | Phases 3–4 |
| `cashkit/stores` — YAML+git config, SQLite ledger, DuckDB frames | Phases 5–9 |
| `cashkit/sdk` — public API | Phases 5–10 |
| `cashkit/cli` | Phase 10 |
| `evals/` — LLM SDK-call accuracy suite (outside the package) | ADR-0037, see below |

## Development

```bash
uv sync --group dev
uv run pytest                            # everything, uninstrumented
uv run pytest -m "not benchmark" --cov   # the coverage gate
```

Two commands, deliberately. The default run carries no coverage instrumentation
because the §5.2 performance budgets measure the engine, and under coverage.py's
trace hook they measure the trace hook instead. The gate run deselects those
budgets and fails below **90%** on `cashkit/engine` + `cashkit/model`; the
threshold and the covered packages live in `pyproject.toml`, and
`tests/test_coverage_gate.py` fails if either is quietly changed.

Key invariants (full list in `PROMPT-fable5-implementation.md`):

- No `float` in money paths — int64 minor units at 4 dp in the core,
  `Decimal` at the boundaries (enforced by a type-audit test).
- Nothing reads the wall clock in `engine/` or `model/` (enforced by a lint
  test).
- Errors are `Diagnostic` objects; exceptions are for programmer error only.
- Canonical serialization is byte-stable: phantom diffs are a build failure.

Design decisions made during implementation live in `DECISIONS.md`; measured
numbers per phase gate in `BENCHMARKS.md`; architecture decision records in
`km/adr/`.

## Evaluating LLM accuracy (`evals/`)

CashKit is built to be driven by LLM agents, so the question that matters is
how often a model, given a plain-language request, writes SDK calls that leave
the book **exactly right**. `evals/sdkeval` measures it
([ADR-0037](km/adr/0037-sdk-call-accuracy-eval.md); full manual in
[`evals/README.md`](evals/README.md)). It lives outside `cashkit/`: the engine
never depends on a model (ADR-0016).

### How a task is graded

Each **task family** is a generator. Every **variant** is a seeded draw with
its own amounts, dates, terms and wording. A variant holds:

- a **request**, describing the starting book in prose;
- a hidden **setup** that builds that book;
- a **reference solution**, used only to test the suite itself;
- **checks** whose expected figures are recomputed in `Decimal` from the
  numbers in the request, never by running the engine.

The model gets a fixed contract (`ROOT`, `kit` and `answer` are pre-bound;
reply with one Python block) plus the SDK reference
(`evals/sdkeval/context/sdk_reference.md`). Its script runs in a child
process, and the book is then **re-opened from disk** and graded:

- **Critical checks** decide pass or fail. They compare exact per-month cash
  and accrual, balances, items, parameters, scenarios, regimes, events and
  answers. There is no tolerance: off by a cent is wrong.
- **Hygiene checks** are reported apart: floats in money code, touching store
  files, git, `today()`, committing when not asked, writing during a question.

Different code that produces the same book scores the same. Only the outcome
is graded.

### Worked examples

These are variant 0 of five families, with the figures the suite actually
expects. `uv run python -m sdkeval show <family> -v 0` (from `evals/`) prints
any task in full.

**1. Payment terms, cash versus accrual (`net_terms`).** The request: *"The
book `plan_2027` exists (monthly, 2027, opening balance €35,000). Add revenue
item `gamma`: €12,000 invoiced monthly from February 2027, payment terms net 45
days."* A correct answer:

```python
kit.set_item(Item(
    id="gamma", name="Gamma", kind="flow", direction="in", tags={"customer": "gamma"},
    segments=[Segment(start=date(2027, 2, 1), recurrence=Recurrence(every=1, unit=Grain.MONTH),
                      amount=Amount(constant=Decimal("12000")))],
    settlement=Settlement.net(45),
))
```

The checks require **accrual** of 12,000 in every month from February to
December, and **cash** of 12,000 in every month from March to December. The
1 February invoice is paid on 18 March. The 1 December invoice is paid on
15 January 2028, outside the horizon, so only ten payments land. The closing
balance must be exactly 35,000 + 10 × 12,000 = **155,000**. Writing `"2m"`
where 45 days is meant, or forgetting the settlement, moves cash into the
wrong months and fails.

**2. A price quoted with VAT included (`vat_inclusive_price`).** The request:
*"The card statement shows €610 every month (VAT included, 22%,
deductible)."* Authored amounts in CashKit are **net of VAT**, so the right
amount is 610 / 1.22 = **−500** with `vat=VatSpec(rate="vat_standard")`. The
checks require accrual of −500 and cash of −610 in every month. A model that
authors −610 gets cash of −744.20 (VAT added twice), and the negative control
for this family is exactly that mistake.

**3. A conditional formula (`threshold_bonus`).** Sales vary by month (6,000;
7,000; … 14,500 in October). The request: *"In any month where sales are
strictly above €9,500, the team gets a bonus of 2% of that month's sales;
otherwise nothing. Use parameters `bonus_threshold` and `bonus_rate`."*

```python
kit.set_param("bonus_threshold", Decimal("9500"))
kit.set_param("bonus_rate", Decimal("0.02"))
kit.set_item(Item(id="bonus", name="Sales bonus", kind="derived", direction="out",
    formula='where(it("sales") > p.bonus_threshold, -p.bonus_rate * it("sales"), 0)'))
```

The expected values are **−290** in October, **−282** in November, **−208** in
December, and **0** in every other month. Both parameters must exist. The
formula language has `where` and no `if`; a version with the branches swapped
must fail.

**4. A what-if that must not touch base (`downside_param`).** The book has
seven items and parameters `price` = 30 and `units` = 40, and `product` is
derived as `p.price * p.units`. The request: *"Create a scenario `downside`
forked from base in which we only sell 24 units a month."*

```python
kit.fork("downside", parent="base", note="lower volume")
kit.set_param("units", Decimal("24"), scenario="downside")
```

The checks require:

- `downside` exists with parent `base`;
- `units` is 24 in `downside` and still 40 in base;
- `product` is **720** in every downside month;
- the downside closing balance is **−5,360**;
- **base still closes at 400**.

Writing the change into base instead of the scenario gives plausible numbers
in both places, and fails here.

**5. An idempotent import of actuals (`import_actuals`).** The request gives
seven bank-statement rows as CSV and says *"this script will be run again
tomorrow on the same file"*. The suite really does **run the script twice**
against the same book. The checks require:

- exactly **7** actual events with source `bank:main`;
- amounts summing to **1,603.48**;
- the bank's transaction ids stored as `ext_id`;
- a closing balance of **46,603.48**.

Importing with `kit.import_events(events, source="bank:main")` keyed on
`(source, ext_id)` passes. A script that makes up fresh ids on each run
imports the rows twice and fails.

### How the suite checks itself

A grader that cannot fail measures nothing, so `uv run pytest evals/tests`
(247 tests, about 2.5 minutes) enforces four things:

- **Every reference solution passes** against the real engine. This is where
  the independent arithmetic and the engine must agree.
- **Every family has a realistic mistake that must fail a critical check while
  still running**: a crash would prove nothing about the checks. The
  "Mistake it must catch" column below lists them.
- **An empty script fails every family.**
- **Every code block in the SDK reference shown to models runs** against the
  real SDK, and every diagnostic code is documented in it.

### First results (2026-11-05)

These runs used the dev split at temperature 0, with up to two turns; the
second turn shows the model its traceback or error diagnostics, never the
expected values.

| Model | Context | Pass rate | First turn |
|---|---|---|---|
| Haiku | none | 14% (6/43) | 14% |
| Haiku | SDK reference | 100% (102/102) | 98.0% |
| Sonnet | SDK reference | 100% (102/102) | 97.1% |

- Without the reference, models invent the API.
- With it, current Claude models solve the whole suite. The remaining
  first-turn misses were missing imports and an `Event` field the reference
  had not listed; the reference now lists every `Event` field.
- Details are in
  [`km/notes/2026-11-05-sdk-eval-first-results.md`](km/notes/2026-11-05-sdk-eval-first-results.md).

### Running it

```bash
cd evals
uv run python -m sdkeval list                                  # every family
uv run python -m sdkeval selftest                              # reference 100%, empty reply 0%
uv run python -m sdkeval run --model anthropic:claude-sonnet-5-5 --variants 3 --max-turns 2
uv run python -m sdkeval run --model openai-compat:my-model@http://localhost:8000/v1   # local / fine-tuned
uv run python -m sdkeval compare runs/A runs/B
```

### All cases tested

There are 51 families in 9 categories. Difficulty runs from 1 (one call,
direct) to 4 (several features composed from one brief). Each family is run
with as many seeded variants as requested. Variants 0–999 form the **dev**
split and variants from 1000 the **heldout** split, so prompts or training
data tuned on dev can be reported honestly on heldout.

**Construction** (11)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `create_book` | 1 | create_book with horizon, grain, opening balance and params | daily instead of monthly grain |
| `fixed_term_income` | 1 | an income with an inclusive end month (end date is exclusive) | end set to the last month (end is exclusive) |
| `monthly_expense` | 1 | a constant monthly outflow, signed and tagged | rent authored as income |
| `quarterly_cost` | 1 | a quarterly recurrence | monthly instead of quarterly |
| `escalating_rent` | 2 | annual escalation from a named param | rate as a literal, no param |
| `phased_contract` | 2 | one item with two consecutive segments | two items instead of one with two segments |
| `pipeline_probability` | 2 | probability-weighted pipeline deal with a flag | probability not applied |
| `retag_by_selector` | 2 | RetagItems over a selector in base | wrong selector |
| `seasonal_schedule` | 2 | an explicit amount schedule on specific months | schedule shifted by a month |
| `escalation_calendar_year` | 3 | escalation stepping each January, not on the anniversary | escalates on the anniversary |
| `payday_business_day` | 3 | day-of-month anchor with previous-business-day adjustment, daily grain | pays a day early |

**Settlement** (4)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `net_terms` | 2 | monthly invoices collected N days after invoice; accrual vs cash | paid immediately |
| `split_payment` | 2 | share split: part on invoice, the rest months later | shares swapped |
| `deposit_remainder` | 3 | fixed deposit leg plus a remainder leg | remainder paid on the invoice date |
| `withholding` | 3 | ritenuta d'acconto at settlement plus the manual F24 remittance item | remittance item missing |

**Tax** (4)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `manual_tax_item` | 2 | a non-native tax (IRES advances) as a manual cat:tax item | missing the manual_tax flag |
| `vat_inclusive_price` | 3 | a price quoted gross of VAT must be authored net | gross price authored (VAT counted twice) |
| `vat_quarterly` | 3 | output and input VAT on items, netted by a quarterly accrual-point regime | monthly regime |
| `iva_per_cassa` | 4 | VAT owed when collected (measure='cash') with long payment terms | accrual tax point |

**Formula** (5)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `cumulative_tracker` | 2 | cum() running total as a derived stock | derived flow instead of stock (moves cash) |
| `derived_agg` | 2 | a derived item over a tag selector with agg() | lists current items instead of agg() |
| `derived_commission` | 2 | a derived item = param × another item | literal rate in the formula |
| `lagged_royalty` | 3 | prev() lag: a cost computed from last month's value | same month instead of previous |
| `threshold_bonus` | 3 | elementwise where() with two params | branches swapped |

**Ledger** (5)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `forecast_events` | 2 | one-off forecast and committed events (not recurring segments) | refund recorded as forecast |
| `void_forecast` | 2 | tombstone a forecast event rather than deleting or negating it | offset with a negative event instead of voiding |
| `correct_actual` | 3 | correct an actual through correct_event (actuals are never voided) | voids the actual (refused) instead of correcting |
| `event_inherits_item` | 3 | a one-off event attached to an item inherits its VAT and terms | event not attached (no VAT, no terms) |
| `import_actuals` | 3 | idempotent import of bank actuals keyed on (source, ext_id); script runs twice | non-idempotent re-import |

**Scenario** (7)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `capital_injection` | 2 | opening_balance as the reserved param in a scenario | opening set to the raise alone |
| `delay_project` | 2 | ShiftItems: delay everything in a project by N months | delayed one month too many |
| `downside_param` | 2 | fork a scenario and override a param in it | what-if written into base |
| `scale_customer` | 2 | ScaleItems over a tag selector inside a scenario | scaled by the cut, not the remainder |
| `hire_vs_contractor` | 3 | add one item and remove another inside a scenario | contractor removed from base |
| `nested_scenarios` | 3 | a scenario forked from a scenario; changes compose down the chain | late scenario forked from base |
| `scenario_terms_change` | 3 | re-author one inherited item's settlement inside a scenario | new terms written into base |

**Query** (7)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `total_outflow` | 1 | total cash paid out over the horizon in a scenario | negative instead of positive |
| `breakeven_month` | 2 | first month from which net cash flow stays non-negative | answers the trough month |
| `cash_vs_accrual` | 2 | cash actually received in a month for an item with payment terms | reads accrual instead of cash |
| `min_cash` | 2 | lowest balance over the horizon | float answer |
| `runway_month` | 2 | first month the balance goes negative (summary().runway_end) | answers the period index, not the date |
| `scenario_gap` | 2 | compare closing balances of two existing scenarios | sign reversed |
| `intra_month_trough` | 3 | min cash on a daily book where the trough is inside a month | reports a month-end balance |

**Repair** (4)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `fix_inverted_sign` | 2 | an expense authored as an inflow must become an outflow | direction flipped but amount still positive |
| `fix_missing_param` | 2 | CK-E008: a formula reads a param the book does not define | wrong rate |
| `fix_unknown_reference` | 2 | CK-E001: a formula names an item that does not exist (typo) | deletes the failing item |
| `fix_cycle` | 3 | CK-E002: break a dependency cycle with prev() | two-month lag |

**Workflow** (4)

| Family | Diff. | What it tests | Mistake it must catch |
|---|---|---|---|
| `commit_after_build` | 2 | build, validate, then commit with a message | never commits |
| `full_build` | 3 | a multi-step build from one brief: book, params, items, terms, scenario | scenario never forked |
| `rerunnable_build` | 3 | a script that creates-or-opens the book; run twice, same result | plain create_book (second run gets None) |
| `startup_brief` | 4 | a whole book from a founder's brief: VAT, terms, split, event, scenario | two calendar months written as 60 days |
