# CashKit — Product Requirements Document

**Version** 0.2 (design freeze candidate — pre-implementation review applied; see `km/notes/2026-07-19-prd-review.md` and `km/adr/`)
**Status** Ready for implementation
**Owner** Luca — Progress Lab S.r.l.

---

## 1. Summary

CashKit is a deterministic cash-flow modelling engine with an SDK-only interface, designed so that LLM agents can build, mutate, and interrogate financial forecasts without ever touching the underlying data structures.

It behaves like a spreadsheet — items, formulas, derived values, cross-references — but with three properties spreadsheets do not have:

1. **Time as a first-class axis.** Values are computed period by period over an explicit horizon, with cross-period feedback (`prev()`) as the only sequential dependency.
2. **Reproducibility by construction.** A run is identified by `(config revision, scenario, engine version)`. Nothing is read from the wall clock during evaluation. Re-running a six-month-old revision produces the six-month-old numbers, exactly.
3. **Introspectability.** Every computed number can be traced to its formula, bindings and arithmetic. Every configured value can be traced to the scenario level and commit that set it.

### 1.1 Primary users

| User | Interaction |
|---|---|
| Founder / CFO | Direct SDK use in a notebook or REPL; commits; reviews diffs |
| LLM agent | SDK calls only; builds scenarios, sweeps parameters, explains results, generates UIs |
| Downstream systems | Read-only: Parquet export, DuckDB/Quack queries |

### 1.2 Non-goals (see §7 for the full list)

CashKit is not an accounting system, not a general ledger, not a tax filing tool, and not a multi-tenant SaaS. It is a modelling engine that consumes facts from those systems.

---

## 2. Core design decisions

These are settled. Deviating from any of them invalidates parts of the architecture downstream.

| # | Decision | Reason |
|---|---|---|
| D1 | Base grain is **DAY** | Payment lags, business-day rolls and mid-month events are exact. Coarser grains are aggregations. Going the other way requires a rewrite. |
| D2 | **Two input kinds**: generative `Item` (segments) and literal `Event` (ledger rows), unioned into fact rows before derived evaluation | A contract is a pattern; an order line is a fact. Forcing either shape onto the other loses intent or exactness. |
| D3 | **Accrual and cash are separate measures**, not one value with an offset | Cash balance folds over cash; P&L views aggregate accrual; VAT tax point may key off either. |
| D4 | Scenarios are **authored by value**, stored as sparse overlays; diffs are computed, never input | `set_item(item_as_you_want_it)` is legible to humans and agents. Field-path patches are not. |
| D5 | **Overrides are authored by whole Item, stored field-sparse.** `segments` is an atomic field. | List-merge semantics on nested config is where these systems die. Resolution algorithm in §4.6. |
| D6 | `cutover` is a **committed value**, never `date.today()` | Reading the clock during evaluation destroys reproducibility, cache validity, and backtesting. |
| D7 | **int64 minor units** in the engine core; `Decimal` only at the boundaries | Decimal is exact but ~100× slower and does not vectorize. int64 at 4dp is exact for add/sub. |
| D8 | Formula semantics are **`where`, not `if`** — both branches always evaluate, selection is elementwise | Short-circuit semantics cannot be vectorized. This cannot be retrofitted without breaking every formula. |
| D9 | **Storage is split by access pattern**: config in git-tracked YAML, events in SQLite, frames in DuckDB/Parquet | Config needs readable diffs; events need transactional integrity and dedup; frames need columnar analytics. |
| D10 | Git is **an implementation detail of persistence**, never part of the agent tool surface | Agents call `commit()`; they never run git commands. |

---

## 3. Architecture

### 3.1 Layer diagram

```
┌──────────────────────────────────────────────────────────────┐
│  Agent / Notebook / Generated UI                             │
└───────────────────────────┬──────────────────────────────────┘
                            │  SDK only — no direct data access
┌───────────────────────────▼──────────────────────────────────┐
│  CashKit SDK                                                 │
│  construction · scenarios · execution · introspection · vcs  │
└───┬──────────────┬──────────────┬───────────────┬────────────┘
    │              │              │               │
┌───▼────────┐ ┌───▼────────┐ ┌───▼─────────┐ ┌───▼──────────┐
│ Config     │ │ Ledger     │ │ Engine      │ │ Frame Store  │
│ store      │ │ store      │ │             │ │              │
│ YAML+git   │ │ SQLite     │ │ graph→SCC→  │ │ DuckDB       │
│            │ │            │ │ vectorize→  │ │ (+ Parquet   │
│            │ │            │ │ fold        │ │  export)     │
└────────────┘ └────────────┘ └─────────────┘ └──────────────┘
```

### 3.2 Compilation pipeline

```
Scenario ──resolve──► Book (concrete, no overlay)
                        │
Items ──────expand──────┤
                        ├──► fact rows ──► derived eval ──► fold ──► Frame
Events ─────filter──────┘     (union MUST precede derived evaluation)
```

**Ordering constraint:** the union of generative and literal facts happens *before* derived evaluation. If it does not, `agg(tag="cat:revenue")` silently ignores actuals and every derived item is wrong.

**Cutover semantics:** `cutover` is a boundary date. Periods `< cutover` are the reconciled past; periods `>= cutover` are forecast (the cutover date itself is the first forecast period). Before cutover, generative expansion is suppressed for **all** items — reconciled means the ledger is the complete record of what happened; events in that window are taken as-is, whatever their status. From cutover forward, generation resumes and `committed`/`forecast` events apply. An `actual` event dated on or after cutover is included and does **not** suppress generation; `validate()` emits `CK-W003` ("actuals after cutover — reconcile and advance cutover") rather than guessing a dedup.

### 3.3 Storage layout

```
<book_root>/
  .cashkit/
    version                     # schema version, for migration on at()
    config.toml                 # store backends, engine settings
  book.yaml                     # grain, calendar, horizon, opening balance, cutover
  params.yaml                   # named scalars, sorted keys
  items/
    acme_impl.yaml              # ONE FILE PER ITEM
    rent.yaml
    overdraft_interest.yaml
  scenarios/
    base.yaml
    downside.yaml
  snapshots/
    base.summary.yaml           # computed; committed; outcome diff lives here
    downside.summary.yaml
  ledger.sqlite                 # events, revisions, import log  (git-ignored)
  frames.duckdb                 # materialized runs, cache        (git-ignored)
  exports/                      # Parquet, on demand              (git-ignored)
```

Git tracks: `book.yaml`, `params.yaml`, `items/`, `scenarios/`, `snapshots/`, `.cashkit/version`.
Git ignores: everything derived or high-volume.

Because actuals are immutable and append-only, a historical `at(ref)` needs to know whether the ledger has moved. Store a **ledger watermark** (max `rowid` + row count hash) on `book.yaml`, stamped by `commit()` — never by `import_events`, so imports do not dirty tracked config. A live run always uses the full ledger; only a run through `at(ref)` truncates the ledger to that revision's watermark. Snapshots record `engine_version` and the watermark; exact historical reproduction is guaranteed at matching engine version (see §6.6).

**Base is a scenario with `parent=None`, but its content lives in the top-level `book.yaml` / `params.yaml` / `items/` for diff legibility; `scenarios/base.yaml` is the (normally empty) overlay shell.** This is a storage-layout special case only — resolution, execution and the SDK treat base exactly like any other scenario; no code path may branch on "is this base".

### 3.4 Remote access

DuckDB's Quack protocol (core_nightly in v1.5.2; stable with DuckDB v2.0, September 2026) turns `frames.duckdb` into a server. This is the mobile/dashboard story: DuckDB-Wasm in the browser connects directly over HTTP, no backend middleware.

**Treat Quack as optional and not load-bearing until v2.0 ships.** Parquet export is the stable sharing path. The `FrameStore` protocol must abstract both.

---

## 4. Data model

All models are Pydantic v2. `Money` is `Decimal` at the boundary, int64 minor units internally.

### 4.0 Primitives

```python
class Grain(str, Enum):
    DAY = "day"; WEEK = "week"; MONTH = "month"; QUARTER = "quarter"; YEAR = "year"

Money = Decimal        # boundary type; int64 minor units at 4 dp inside the engine
Duration = str         # "<n>d" | "<n>w" | "<n>m" | "<n>y" — calendar semantics
                       # ("2m" = two calendar months, day clamped to month end)
PeriodRef = date       # segment boundaries are concrete ISO dates in v1

class PeriodRange(BaseModel):
    start: date
    end: date                           # exclusive: [start, end)

class CalendarSpec(BaseModel):
    fiscal_year_start_month: int = 1
    country: str | None = None          # seed for the holiday set, e.g. "IT"
    holidays: list[date] = []           # RESOLVED for the whole horizon at book
                                        # creation and committed. The `holidays`
                                        # package is only a seed; runtime never
                                        # consults it (reproducibility).
    weekend: set[int] = {5, 6}          # ISO weekday indices, Sat/Sun

class Watermark(BaseModel):
    max_rowid: int
    row_count: int
    content_hash: str                   # over (source, ext_id, date, amount) rows

class Amount(BaseModel):                # exactly one of the two set
    constant: Money | None = None
    schedule: list[tuple[date, Money]] | None = None
    # An `expression` variant is deliberately absent in v1: formulas belong to
    # derived items. Computed schedules are authored via the SDK.

class Escalation(BaseModel):
    rate: str | Decimal                 # param key or literal annual rate
    every_years: int = 1
    anchor: Literal["segment_start", "calendar_year"] = "segment_start"

class Diagnostic(BaseModel):
    severity: Literal["error", "warning", "info"]
    code: str                           # from the catalogue in §10.1
    item_id: ItemId | None = None
    field: str | None = None
    message: str
    suggested_fix: str
```

### 4.1 Book

```python
class Book(BaseModel):
    id: str
    base_grain: Grain = Grain.DAY
    calendar: CalendarSpec          # fiscal year start, holiday set, business-day rules
    horizon: PeriodRange            # [start, end)
    opening_balance: Money
    cutover: date                   # last reconciled period boundary — NEVER today()
    ledger_watermark: Watermark | None
    params: dict[str, Decimal]      # named scalars: vat_standard, inflation, fx_eur_usd.
                                    # Keys match [a-z][a-z0-9_]* so formula access
                                    # p.<key> is 1:1; dotted keys are rejected (CK-E007)
    items: dict[ItemId, Item]
    tax_regimes: list[TaxRegime]
```

**`params` is the lever surface.** Anything an agent might sweep must be a param, not a literal inside a formula. VAT rates, escalation rates, FX, churn, headcount cost — all params. `opening_balance` is also a reserved param key: setting it in a scenario overrides the Book field, so capital-injection cases are sweepable.

### 4.2 Item — the generative input

```python
class Item(BaseModel):
    id: ItemId
    name: str
    kind: Literal["flow", "derived", "stock"]  # "stock" is valid on derived items only
                                               # in v1; a generative stock is rejected
                                               # with a diagnostic (CK-E012)
    direction: Literal["in", "out"] | None    # display only; storage is signed.
                                              # add_item() rejects amounts whose sign
                                              # contradicts direction (CK-E011) — an
                                              # agent authoring rent as positive/"out"
                                              # must not silently create an inflow
    tags: dict[str, str]                      # dimensional: {customer: "acme", cat: "revenue"}
    flags: set[str]                           # boolean: {"committed", "pipeline"}
    currency: str = "EUR"
    segments: list[Segment]                   # empty for derived
    formula: str | None                       # derived only
    settlement: Settlement | None
    vat: VatSpec | None
    agg_rule: Literal["sum", "last", "mean"] = "sum"   # how to aggregate to coarser grain
```

```python
class Segment(BaseModel):
    start: PeriodRef
    end: PeriodRef | None                     # None = open-ended
    recurrence: Recurrence                    # REQUIRED — one-offs are Events, not segments
    amount: Amount                            # constant | explicit schedule (§4.0 —
                                              # no expression variant in v1)
    escalation: Escalation | None
    probability: Decimal = 1                  # pipeline weighting

class Recurrence(BaseModel):
    every: int
    unit: Grain
    anchor: Literal["period_start", "period_end", "day_of_month", "eom"] = "period_start"
    day: int | None = None                    # day_of_month anchor; values past the
                                              # month's end clamp to the last day
                                              # (31 → Feb 28/29)
    business_day_adjust: Literal["none", "prev", "next"] = "none"
```

**`Recurrence` is non-optional by design.** A single occurrence is an `Event` with `status="forecast"`. This removes the one-off special case from the generative path entirely.

### 4.3 Event — the literal input

```python
class Event(BaseModel):
    id: EventId
    date: date
    amount: Money
    status: Literal["actual", "committed", "forecast"]
    item: ItemId | None = None        # inherits tags / vat / settlement from the item
    tags: dict[str, str] = {}         # merged over the item's; event wins on conflict
    vat: VatSpec | None = None        # overrides item's
    settlement: Settlement | None = None
    currency: str = "EUR"
    source: str | None = None         # "bank:IT60X...", "erp:INV-2026-0114"
    ext_id: str | None = None         # idempotency key — UNIQUE(source, ext_id)
    note: str | None = None
```

The `item` reference is the master-data hook without master-data machinery. An order line references "Acme maintenance" for tags, VAT rate and payment terms, supplying only date and amount. A mixed-VAT order is two events sharing `tags.order`.

**`UNIQUE(source, ext_id)` is the only thing preventing double-counted actuals on re-import.** Non-negotiable.

### 4.4 Settlement

```python
class Settlement(BaseModel):
    due: list[DueTerm]                # empty list = never settles (accrual only)

class DueTerm(BaseModel):
    share: Decimal | None = None      # fraction of accrued amount
    amount: Money | None = None       # or a fixed sum (deposit, retainer)
    remainder: bool = False           # "whatever is left" — at most one per list
    offset: Duration                  # 30d, 2m, 0d
    basis: Literal["accrual", "period_end", "month_end"] = "accrual"
    adjust: Literal["none", "prev", "next"] = "none"
    withholding: Decimal = 0          # ritenuta d'acconto — reduces cash received
```

Constructors for ergonomics: `Settlement.net(60)`, `Settlement.immediate()`, `Settlement.split([(0.3, "0d"), (0.7, "90d")])`.

**Validation at `add_item()` time, not run time:**
- Either all entries use `share` and they sum to exactly `1` (Decimal, no float tolerance),
- or a mix of `amount` entries with exactly one `remainder: true`.
- Fixed `amount` entries consume first and **pay in full even when they exceed the accrued amount** — a deposit larger than delivered work is real cash. `remainder` takes what is left, **clamped to zero with a warning diagnostic** (`CK-W001`; partial delivery produces this legitimately).
- Negative accrued amounts (credit notes): `share` splits apply sign-symmetrically. A negative accrual meeting fixed-`amount` terms routes entirely through `remainder` and emits `CK-W002` — fixed legs never flip sign.
- **Withholding is one leg only:** it reduces the cash moved at settlement. The counter-leg — remittance to the state via F24 when you are the payer, or the tax credit when your client withholds — is **not generated by the engine** and must be modelled per §7.2. `validate()` emits `CK-W004` when withholding is in use and no `cat:tax` item covers the remittance.

### 4.5 VAT and tax

```python
class VatSpec(BaseModel):
    rate: str | Decimal = "vat_standard"    # param key by default, literal allowed
    treatment: str = "standard"             # standard | exempt | reverse_charge |
                                            # out_of_scope | export | split_payment
    recoverable: Decimal = 1                # input VAT only; <1 for partial deductibility

class TaxRegime(BaseModel):
    id: str
    accumulates: str                        # tag selector defining the base
    measure: Literal["accrual", "cash"] = "accrual"   # the tax point
    periodicity: Literal["monthly", "quarterly", "annual"]
    payment_offset: Duration                # e.g. 16d — Italian F24 on the 16th
    surcharge: Decimal = 0                  # 1% on IVA trimestrale
    credit_handling: Literal["carry", "refund_annual"] = "carry"
    annual_adjustment_month: int | None = None
```

**All authored amounts are VAT-exclusive (net).** `Segment.amount` and `Event.amount` never include VAT. The engine computes VAT per line from `VatSpec`, grosses up the settlement cash leg (a 1,000 invoice at 22% collects 1,220), and routes the VAT component through the `TaxRegime` schedule. There is no VAT-inclusive authoring mode.

**Engine integration:** each regime materializes as synthetic derived items (`_tax:<regime_id>:liability` flow and `_tax:<regime_id>:credit` stock) injected into the dependency graph **before condensation** — so credit carry-forward participates in `prev()` feedback and the cash fold sees tax payments like any other flow. For a VAT regime, `accumulates` defaults to every item carrying a `VatSpec`; for other regimes it is an explicit tag selector (grammar in §5.4).

`TaxRegime` is deliberately generic. VAT is one instance. The decomposition — **a rate at the line, a schedule at the entity** — holds for VAT, IRAP, IRES and social contributions; only the accumulation base and schedule differ.

**Tax point matters more than anything else here for cash.** With `measure="accrual"` (Italian default) you owe VAT on invoice date. With 60-day customer terms you pay the state on 16 March for an invoice settling in May. That working-capital hole is precisely what a cash forecast exists to surface, and it only appears if VAT is wired to accrual, not to cash. `IVA per cassa` (available under €2M turnover) is `measure="cash"` — worth being able to model both, since opting in is a real decision.

**Credit carry-forward:** input > output in a period is a *credit stock*, not a negative payment. Accumulate it, offset future liability, zero out only on annual adjustment or refund claim. Modelling it as a cash inflow is materially wrong in an investment year.

### 4.6 Scenario

```python
class Scenario(BaseModel):
    id: str
    parent: str | None                  # scenarios fork from scenarios
    note: str = ""
    params: dict[str, Decimal]          # sparse
    items: dict[ItemId, ItemOverlay]    # sparse — only fields that differ
    added: dict[ItemId, Item]           # full items, new in this scenario
    removed: set[ItemId]
    event_overrides: dict[EventId, EventOverlay]   # status != "actual" ONLY
```

**Resolution rules — three, and they stay boring:**

1. Resolution is **field-sparse along the parent chain**: for each field of each item, the nearest ancestor overlay that *recorded* that field wins; unrecorded fields fall through to the parent. (`set_item` is authored by whole value, but only fields differing from the resolved parent are recorded — the by-value/computed-diff pipeline of D4.)
2. `segments` is atomic. Touch one, replace the list. No positional patching, no ID matching, no partial merge.
3. Consequence of rule 1: a later correction to `tags` or `settlement` in base propagates into scenarios that did not override those fields, and does not propagate into ones that did.

**Actuals are immutable across all scenarios.** A downside case cannot rewrite March's bank statement; it can only change what is forecast from `cutover` forward.

---

## 5. Evaluation engine

### 5.1 Two-tier evaluation

Build the dependency graph *including* `prev()` edges, then take its condensation:

- **Trivial SCCs** (no cycle) — the overwhelming majority. Each item is one column expression over the entire horizon. Segment expansion is date-index masking; escalation is a power over a year-index vector; settlement lag is an array shift; `agg(tag=...)` is a row-sum over a resolved slice.
- **Non-trivial SCCs** — items in a genuine `prev()` cycle: cash balance, overdraft interest, VAT credit carry, revolving credit. Typically 2–8 items. Only these get the sequential per-period fold.

Everything outside the feedback set is pre-summed into a single `net[t]` vector, so the sequential loop iterates over one number per period regardless of item count. When no feedback item has a conditional, the fold degenerates to `cumsum`.

```python
def run(book: Book) -> Frame:
    graph = build_graph(book)              # prev() edges included
    comp = condensation(graph)             # SCCs, topologically ordered
    cols = {}
    for scc in comp:
        if scc.is_trivial:
            cols[scc.item] = evaluate_column(scc.item, cols, book)   # vectorized
        else:
            cols.update(fold_scc(scc, cols, book))                   # sequential
    return Frame.from_columns(cols, book)
```

### 5.2 Measured performance

Benchmarked at 5 years × day grain (1826 periods), 50 items (40 generative flows with escalation and settlement lags, 8 derived, 2 in a feedback loop):

```
naive Decimal per-cell loop          206.0 ms
vectorized + python fold               0.8 ms      (260×)
vectorized + cumsum fold               0.1 ms     (1645×)
delta recompute (one item changed)     0.1 ms     (3158×)

scaling (vectorized, cold):
     50 items ×  1826 periods     91,300 cells       0.9 ms
    200 items ×  1826 periods    365,200 cells       3.1 ms
    500 items ×  3652 periods  1,826,000 cells      26.2 ms
   2000 items ×  3652 periods  7,304,000 cells     100.5 ms

20-scenario sweep on the delta path                  1.4 ms
```

The naive loop meets a 5s budget for one scenario and fails it at 20 scenarios (4.1s) or 200 items (16s). The vectorized design has three orders of magnitude of headroom.

**Performance budgets:**

| Path | Budget |
|---|---|
| Delta recompute (UI interaction) | < 5 ms |
| Full run, cold | < 50 ms |
| Frame materialization into DuckDB | < 200 ms |
| Commit (serialize + snapshots + git) | < 3 s |

Because evaluation is this cheap, **the run cache is not load-bearing**. Recompute-on-doubt is a legitimate strategy, which removes cache invalidation as a correctness risk.

### 5.3 Numeric policy

- Engine core: **int64 minor units at 4 decimal places**. Max ≈ 9×10¹⁴ currency units.
- Add/subtract: exact.
- Multiply by rate: scale → multiply → divide with one declared rounding policy (half-up by default, configurable to banker's). Rounding happens at declared boundaries only, never implicitly.
- Escalation factors `(1+r)^n` are computed **in Decimal**, once per distinct `(rate, n)` pair, converted to scaled int64 multipliers, and applied as a vectorized integer multiply. The distinct-factor count is tiny (rates × years), so this costs nothing and removes float from the money path entirely. A float64 fast path is admissible later only behind a property test proving byte-identity with the Decimal factor table, half-up tie cases included.
- Rate multiplications (scale → multiply → divide) run their intermediates through arbitrary-precision ints (or a checked int128 path). Silent int64 wraparound is forbidden; an overflow pre-check failure raises — it never truncates.
- **Rounding order is canonical and fixed:** base amount → escalation → probability weighting → settlement share split → withholding → VAT per line. Each step rounds to 4 dp under the declared policy before the next step. In a `share` split the last term absorbs the rounding residual so legs sum exactly to the accrued amount. The reference engine implements the identical order — dual-engine byte-equality depends on it.
- Cross-currency aggregation is an **error diagnostic** (`CK-E020`), never a silent sum: `agg()` spanning mixed currencies, or a cash fold over mixed-currency items, refuses. Conversion arrives with multi-currency support (§7.3).
- `Decimal` at parse, serialize, and display. The engine core never sees it.
- **Never float for money.** With the Decimal factor table above, there is no float exception at all.

### 5.4 Formula language

Restricted Python AST walk. Whitelisted node types. No `eval`, no attribute access, no calls outside a fixed builtin table.

| Symbol | Meaning |
|---|---|
| `it("acme_impl")` | value of another item, this period |
| `prev("cash", n=1, init=0)` | value n periods back — the only cycle-breaker; `n` must be a literal. For `t < n` it yields `init` (literal or param ref: `prev("cash", init=p.opening_balance)` seeds the cash fold) |
| `p.vat_standard` | named param |
| `agg(tag="cat:revenue")` | sum over items matching a selector, this period |
| `cum("revenue")` | running total since horizon start |
| `t.index`, `t.month`, `t.is_quarter_end`, `t.is_business_day` | period metadata |
| `where(cond, a, b)` | **elementwise select — both branches always evaluated** |
| `min`, `max`, `clip`, `round_`, `abs_` | safe builtins |

**`if_` does not exist.** Only `where`. Any function added later must be expressible as a masked column operation.

**Division is masked-safe.** Because both `where` branches always evaluate, `a / x` executes at `x == 0` by design. Elementwise division by zero yields `0`; a warning diagnostic (`CK-W005`) attaches only when the zero-division cell is *selected* by the enclosing `where` (or there is no `where`). Division rounds to 4 dp under the book's declared policy, like every other rounding boundary.

**Selector grammar** (shared by `agg()`, `retag()`, `TaxRegime.accumulates`, `frame(where=...)`): space-separated terms, ANDed. A term is `key:value` (tag equality) or `flag:name` (flag membership). No OR, no negation, no wildcards in v1 — model finer slices as tags.

`agg()` selectors resolve to concrete item IDs at graph-build time so the DAG stays static. A selector that would make an item depend on itself is rejected with a diagnostic.

### 5.5 Frame format

Canonical storage is **tidy/long**: one row per `(period, item, measure)`.

```
period_start | period_end | item_id | measure  | value | currency | status
2026-03-01   | 2026-03-01 | acme    | accrual  | 12000 | EUR      | forecast
2026-03-01   | 2026-03-01 | acme    | cash     |     0 | EUR      | forecast
```

`measure` as a column (not `accrual_value` / `cash_value` columns) means adding measures later — VAT, FX-converted, headcount — costs nothing.

Tags live in a separate item-dimension table, joined on demand. **Do not denormalize tags into the fact table**; you will fight it the first time tags change.

Wide format, coarser-grain aggregation and tag slicing are **views computed on demand**. Aggregation respects `Item.agg_rule`: flows sum, stocks take last-in-period.

---

## 6. SDK specification

Every operation is a named, validated, logged command returning a structured result. Agents never touch data structures directly.

### 6.1 Construction

```python
def create_book(id, grain=DAY, horizon, opening_balance, calendar=None) -> BookRef
def add_item(book, spec: ItemSpec) -> ItemRef            # validated; returns diagnostics
def add_derived(book, id, formula, tags=None) -> ItemRef # formula parsed + DAG-checked NOW
def set_param(book, key, value, note="") -> ChangeReport
def retag(book, selector, tags) -> int                   # count affected
def add_tax_regime(book, regime: TaxRegime) -> None
def set_cutover(book, date, note) -> ChangeReport
def validate(book) -> list[Diagnostic]
```

### 6.2 Ledger

```python
def add_event(book, event: Event) -> EventRef
def import_events(book, rows: Iterable[Event], source: str) -> ImportReport
    """Idempotent on (source, ext_id). A row whose (source, ext_id) exists with an
       identical payload is skipped; one that exists with a DIFFERENT payload is a
       conflict, and any conflict aborts the whole batch (all-or-nothing, CK-E010)
       with per-row diagnostics. Returns inserted / skipped / conflicted counts."""
def query_events(book, where=None, since=None, until=None) -> Table
def void_event(book, event_id, note) -> ChangeReport
    """Tombstone a committed/forecast event (append-only: the row is marked void,
       never deleted, so watermarks stay valid). Refuses status='actual' with a
       diagnostic."""
def reconcile(book, until: date) -> ReconciliationReport
    """Compare actuals to what was forecast for the same window. Feeds set_cutover."""
```

### 6.3 Scenarios

```python
def fork(scenario, id, note="") -> ScenarioRef
def set_item(scenario, item: Item, note="") -> ChangeReport
def set_param(scenario, key, value, note="") -> ChangeReport
def unset(scenario, item_id) -> ChangeReport              # revert to parent's version
def remove_item(scenario, item_id) -> ChangeReport
def apply_macro(scenario, macro, note="") -> ChangeReport  # expands NOW to concrete overlays
def resolve(scenario) -> Book                              # materialized, inspectable
def diff(a, b) -> ScenarioDiff                             # semantic, from resolved books
def provenance(scenario, item_id) -> Provenance            # which ancestor set each field
def flatten(scenario, new_id) -> ScenarioRef               # collapse chain to standalone
```

`ChangeReport` returns the fields **actually recorded as different**. An agent that writes an item and changes nothing gets told so, rather than silently bloating the overlay.

Macros (`ShiftItems`, `ScaleItems`, `RetagItems`) expand immediately to concrete overrides. Nothing deferred, nothing stored as a rule — post-macro state is indistinguishable from having typed the items out.

### 6.4 Execution

```python
def run(scenario, cutover_override=None) -> RunRef
    """Deterministic. cutover_override marks the run non-cacheable and excludes it
       from snapshots — it is a deliberate query, not a property of the model."""
def frame(run, grain=None, measures=None, where=None, status=None) -> Table
def pivot(run, index="period", columns="tag:customer", values="cash") -> Table
def summary(run) -> RunSummary          # min cash + period, runway, breakeven, totals
def compare(runs: list[RunRef], metric="cash") -> Table
def export(run, path, format="parquet") -> Path
```

### 6.5 Introspection — the part that makes agents work

```python
def trace(run, item, period, depth=3) -> Trace
    """Formula, resolved bindings, arithmetic, recursively. NON-NEGOTIABLE:
       an agent asked 'why is March negative' must walk the computation, not guess."""
def why_zero(run, item, period) -> Explanation
    """Distinguishes the five zero causes: (1) period outside every segment,
       (2) probability 0, (3) upstream zero propagated through the formula,
       (4) generation suppressed by cutover, (5) settlement produced no cash
       leg this period (empty `due`, or remainder clamped to zero)."""
def depends_on(book, item) -> Graph
def dependents_of(book, item) -> Graph
def describe_book(book) -> BookDescription
    """Schema, item list with tags, available measures and params.
       Lets a model generate a UI without inventing fields that do not exist."""
```

**Errors are data, not exceptions.** Every fallible operation returns `Diagnostic(severity, code, item_id, field, message, suggested_fix)`. An agent can loop on structured diagnostics; it cannot loop on a stack trace. Exceptions are reserved for programmer error (bad types, missing store).

### 6.6 Version control

```python
def commit(message, scenarios=None, author="agent") -> Revision | None
    """Serialize state, recompute affected snapshots, stage, commit.
       Returns None if the tree is unchanged."""
def status() -> WorkingState           # structured diff, never a git porcelain string
def discard(items=None) -> ChangeReport
def history(item=None, scenario=None, field=None, limit=50) -> list[Revision]
def at(ref) -> CashKit                 # read-only kit bound to a past revision
def diff_revisions(a, b, scenario=None) -> RevisionDiff
def blame(item, field) -> list[Revision]
```

`at()` returns a **kit, not a book**, so `kit.at("HEAD~5").run("downside").summary()` works and eras of the model compare through one API. Loaded from the object store via pygit2 — no worktree checkout, no branch switching, no "the agent left the repo in a weird state."

Cache key: `(revision_sha, scenario_id, engine_version, ledger_watermark)`.

**Single writer:** write operations take an exclusive lockfile at `.cashkit/lock` (O_EXCL, pid + timestamp). A second concurrent writer receives `CK-E013` naming the lock holder; stale locks (dead pid) are reclaimed with `CK-W010`. This is the "fails loudly, never merges silently" mechanism, and it covers all three stores.

### 6.7 Two-tier commit model

Not every change deserves a commit. Exploratory sweeping stays in memory; `commit()` marks meaningful boundaries ("revised Acme terms", "Q3 plan as presented"). Otherwise history becomes noise and the review gate loses its value.

---

## 7. Out of scope

### 7.1 Explicitly not built

| Area | Why | What to do instead |
|---|---|---|
| General ledger / double-entry accounting | CashKit forecasts cash, it does not keep books | Import from the accounting system |
| Invoice generation, AR/AP workflow | Different system, different lifecycle | Import invoices as events |
| Order headers, product master data, BOM | Item-as-line covers the modelling need | Tag events with `tags.order` |
| Tax filing, e-invoicing (SdI, FatturaPA) | Regulatory, not analytical | Separate integration |
| Bank connectivity (PSD2, CBI) | Operational plumbing | Feed the ledger via `import_events` |
| Multi-tenant SaaS, auth, RBAC | Single-entity tool | Deploy per entity |
| Consolidation across legal entities | Requires intercompany elimination logic | Separate books; aggregate externally if needed |
| Real-time streaming updates | Batch recompute is 1–26 ms | Recompute on demand |
| Automatic currency hedging logic | Modelling, not execution | FX rates as scalar params per scenario; per-period rate series and revaluation are deferred (§7.3) |
| Optimization / solver ("find the price that hits breakeven") | Different problem class | Parameter sweep + external solver on the SDK |

### 7.2 Tax mechanics deliberately not native

These require order-level or counterparty-level data the model does not carry. They are represented as **manually entered items or events**, never as engine features. This is an escape hatch that costs nothing and keeps the engine honest about what it does and does not know.

| Mechanic | Handling |
|---|---|
| IRAP, IRES | Manual item, schedule from the commercialista's projection |
| INPS / INAIL contributions | Manual recurring item per employee cohort |
| TFR accrual and payout | Manual stock item + payout events |
| Split payment (PA invoices) | `treatment="split_payment"` zeroes the VAT cash leg; the receivable is net |
| Intrastat, OSS thresholds | Out of scope entirely |
| Pro-rata deductibility | Approximate via `VatSpec.recoverable` at item level |
| Tax credits and incentives (e.g. Transizione 5.0) | Manual item with the expected offset schedule |
| Ravvedimento, penalties, instalment plans | Manual events |
| Advance payments (acconti IRES/IRAP, acconto IVA December) | Manual events on the known statutory dates |

See §9.5 for how the agent skill must communicate this.

### 7.3 Deferred, not rejected

- Monte Carlo / probabilistic horizons (structure supports it: `probability` exists on segments)
- Multi-currency with full revaluation (rates as params works; revaluation of balances does not yet)
- Postgres backend (only if concurrent human editing or a hosted UI arrives — Quack's multi-writer support weakens this case). The UI itself is designed and scheduled as the post-v1 deliverable: interaction model in ADR-0013, delivery strategy in ADR-0014 (single-user local-first, so this Postgres trigger stays un-pulled).
- Branch-based propose-and-review workflow for agent changes

---

## 8. Installation

### 8.1 Requirements

- Python ≥ 3.11
- No system services. Everything is a file.

### 8.2 Install

```bash
pip install cashkit                      # core: engine, SDK, YAML+SQLite stores
pip install "cashkit[duckdb]"            # frame store, aggregation, Parquet export
pip install "cashkit[git]"               # pygit2-backed revision control
pip install "cashkit[all]"
```

Dependency set (core): `pydantic>=2`, `numpy>=1.26`, `pyyaml`, `python-dateutil`, `holidays`.
Optional: `duckdb>=1.5`, `pygit2>=1.14`, `polars`, `pyarrow`.

### 8.3 Initialize a book

```bash
cashkit init ./acme-cashflow \
    --grain day \
    --horizon 2026-01-01:2031-01-01 \
    --opening-balance 250000 \
    --currency EUR \
    --calendar IT \
    --git
```

Creates the layout in §3.3, an initial commit, and a `base` scenario.

### 8.4 Verify

```bash
cashkit doctor            # store connectivity, schema version, engine version, git state
cashkit validate          # semantic diagnostics on the current book
cashkit run base --summary
cashkit status            # structured working-state diff (wraps SDK status())
cashkit commit -m "..."   # human commit path from the shell (wraps SDK commit())
cashkit history [item]    # revision list (wraps SDK history())
```

`cashkit doctor` must be runnable by an agent as a first action and must return structured JSON with `--json`.

### 8.5 Migration

`.cashkit/version` holds the schema version. When models change, a migration reads old commits through an upgrade path. **Without this, `at()` breaks on anything older than the last refactor and the historical-reproducibility argument evaporates.** Migrations are forward-only, tested against a fixture repo with at least three schema generations.

### 8.6 Remote (optional)

```bash
cashkit serve --quack --port 8080 --token $TOKEN   # exposes frames.duckdb read-only
```

Bind to localhost by default. For public access, put a reverse proxy in front. Quack is `core_nightly` until DuckDB v2.0 — gate this behind a feature flag and do not make any workflow depend on it.

---

## 9. Agent skill / plugin specification

A skill packaged for Claude Code, Cowork and compatible agent runtimes, instructing an LLM how to install and use CashKit correctly.

### 9.1 Package layout

```
cashkit-skill/
  SKILL.md                    # entry point, <500 lines, progressive disclosure
  reference/
    sdk-api.md                # full signature reference
    formula-language.md       # grammar, builtins, where-not-if rule
    data-model.md             # every model with field semantics
    tax-handling.md           # §9.5 — native vs manual
    recipes.md                # canonical task patterns
    troubleshooting.md        # diagnostic codes → fixes
  scripts/
    bootstrap.py              # detect or create a book, verify install
    validate_book.py          # run diagnostics, format for agent consumption
```

### 9.2 SKILL.md trigger description

> Use when the user wants to build, modify, query or explain a cash-flow forecast, financial plan, runway analysis, or scenario comparison using CashKit. Triggers: "cash flow model", "runway", "forecast scenario", "what if we ...", "when do we run out of cash", any mention of `cashkit`, or the presence of a `.cashkit/` directory in the workspace. Do NOT trigger for accounting entries, invoice generation, tax filing, or general spreadsheet work.

### 9.3 Mandatory behavioural rules for the agent

These go at the top of SKILL.md, stated as hard rules.

1. **Never read or write store files directly.** No opening `items/*.yaml`, no SQL against `ledger.sqlite`, no pandas on `frames.duckdb`. Every operation goes through the SDK. Direct manipulation bypasses validation and produces states the engine cannot reason about.
2. **Never run git commands.** Use `kit.commit()`, `kit.history()`, `kit.at()`. Git is a persistence detail.
3. **Run `validate()` after any structural change** and before `commit()`. Surface error diagnostics to the user; do not commit through them.
4. **Never set `cutover` to today.** It is the last *reconciled* boundary. Advancing it is a business decision the user makes after reconciliation.
5. **Use `trace()` before explaining any number.** Do not infer why a value is what it is; walk it.
6. **Author scenarios by value.** `set_item(item_as_you_want_it)`. Never construct overlays or field paths by hand.
7. **Prefer params to literals.** If a number might be swept, `set_param` it.
8. **Commit at meaningful boundaries only.** Exploratory sweeps stay in memory.
9. **Report `ChangeReport` contents back to the user.** If it says nothing changed, say so rather than claiming success.
10. **Actuals are immutable.** Never attempt to override an event with `status="actual"` in a scenario.

### 9.4 Canonical recipes (reference/recipes.md)

Each recipe is a complete, runnable snippet. Minimum set:

| Recipe | Covers |
|---|---|
| New book from scratch | `create_book`, opening balance, calendar, first commit |
| Add a customer contract with phases | multi-segment item, escalation, settlement terms |
| Add an expense with quarterly VAT | `VatSpec`, `TaxRegime`, param-referenced rate |
| Import actuals from CSV | `import_events`, idempotency, `ImportReport` handling |
| Monthly close | `reconcile` → review → `set_cutover` → `commit` |
| Build a downside scenario | `fork`, `set_item`, `apply_macro`, `diff` |
| Answer "when do we run out of cash" | `run`, `summary`, `trace` on the trough period |
| Explain a variance | `frame(status=...)`, group-by, `trace` |
| Backtest a forecast | `at("HEAD~12")`, run, compare to actuals |
| Model a non-native tax | §9.5 pattern |

### 9.5 Tax handling instructions — required content

This section must be explicit in the skill, because an LLM's default behaviour is to assume the engine handles tax comprehensively and to produce a forecast that silently omits large, certain outflows. **That failure mode is the single most dangerous one in this system**: a cash forecast missing IRES advances or contributions is not slightly wrong, it is wrong by the amount that causes the crisis it was built to predict.

**What the engine handles natively:**

- Per-item VAT rate (`VatSpec.rate`, param-referenced by default)
- VAT treatment classes: standard, exempt, reverse charge, out of scope, export, split payment
- Input VAT partial deductibility (`recoverable`)
- Periodic VAT netting and payment scheduling via `TaxRegime`
- Tax point selection: accrual (default) vs cash (`IVA per cassa`)
- VAT credit carry-forward as a stock
- Withholding at settlement (`DueTerm.withholding` — ritenuta d'acconto)

**What must be modelled as separate manual items or events** — the agent must ask about each of these when building a book for a real entity, and must state clearly in any forecast summary which of them are present and which are absent:

- **IRES / IRAP** — including June/November advances (acconti). Manual item, amounts from the commercialista's projection or prior-year basis.
- **INPS / INAIL contributions** — manual recurring item, sized per employee cohort, with the correct monthly/quarterly payment dates.
- **TFR** — accrual as a stock item, payout as events on departure.
- **Acconto IVA (December)** — statutory advance, manual event.
- **Tax credits and incentives** — Transizione 5.0, R&D credit, patent box. Manual item with the expected offset schedule. These reduce F24 payments rather than arriving as cash.
- **Instalment plans, ravvedimento, penalties** — manual events.
- **Intrastat, OSS, pro-rata beyond the item-level approximation** — out of scope. Flag to the user; do not approximate silently.

**Required agent behaviour:**

> When building or reviewing a book for a real legal entity, run through the non-native tax checklist and produce an explicit coverage statement:
>
> ```
> Tax coverage in this forecast:
>   ✓ VAT (quarterly, accrual tax point, standard 22%)
>   ✓ Ritenuta d'acconto on consultant payments
>   ✗ IRES/IRAP — NOT MODELLED. Advances typically June and November.
>   ✗ INPS contributions — NOT MODELLED.
>   ✗ TFR — NOT MODELLED.
> Forecast understates cash outflows by the omitted items.
> ```
>
> Never present a forecast for a real entity without this statement. If the user supplies figures for a missing item, add it with `flags={"manual_tax"}` and tag `cat:tax` so it is visible in every tag-based view.

`validate()` supports this: it emits an info diagnostic when a book has a `TaxRegime` but no items tagged `cat:tax` outside VAT, on the reasonable assumption that a real entity owes more than VAT.

### 9.6 Installation instructions in the skill

The skill must instruct the agent to:

1. Run `cashkit doctor --json`. If it fails, `pip install "cashkit[all]"`.
2. Look for `.cashkit/` in the workspace. If found, load it; do not create a second book.
3. If creating a book, ask for: horizon, opening balance, currency, base grain, fiscal calendar, VAT periodicity, and whether the entity is on `IVA per cassa`. Do not guess these.
4. After `init`, immediately run the §9.5 tax checklist.

---

## 10. Acceptance criteria

The system is done when all of the following hold.

**Correctness**
- Round-trip property test: `parse(serialize(book)) == book` for generated books, and `serialize(parse(s)) == s` byte-for-byte for canonical documents. Phantom diffs are a build failure.
- Dual-engine test: naive Decimal reference implementation and vectorized int64 engine agree exactly on a fixture corpus of ≥50 books, including escalation, settlement splits, VAT netting and feedback loops.
- Settlement shares summing to 1 is enforced in Decimal with zero tolerance.
- `run()` at a given `(sha, scenario, engine_version, watermark)` is byte-identical across processes and machines.

**Performance**
- Full cold run, 50 items × 1826 periods: < 50 ms
- Delta recompute after a single item change: < 5 ms
- 20-scenario sweep: < 500 ms
- Commit including snapshot recompute: < 3 s

**Agent usability**
- `describe_book()` output is sufficient to generate a working UI with no field invention.
- `trace()` explains any cell to depth 3 without the agent reading source data.
- Every failure mode returns a `Diagnostic` with a `suggested_fix`, not an exception.
- An agent given only SKILL.md builds a 20-item book with VAT and a downside scenario, unaided, and produces the §9.5 coverage statement.

**Version control**
- `at("HEAD~N")` at matching engine version reproduces historical numbers exactly, across a schema migration boundary. On an engine-version mismatch the snapshot comparison reports the delta — it never fails silently.
- `diff_revisions()` shows nothing for a pure reformat.
- Config diff and outcome diff appear in the same commit.

### 10.1 Initial diagnostic catalogue

Codes are stable identifiers: the set grows, codes never change meaning. E = error, W = warning, I = info.

| Code | Trigger |
|---|---|
| CK-E001 | Unknown item id in `it()`, or `agg()` selector resolving to nothing at graph build |
| CK-E002 | Circular dependency without `prev()` — cycle members named |
| CK-E003 | Formula rejected: disallowed AST node, unknown identifier, or non-literal `n` in `prev()` |
| CK-E004 | Settlement shares do not sum to exactly 1 |
| CK-E005 | Settlement mixes `share` and `amount`, or has more than one `remainder` |
| CK-E006 | Scenario overlay touches an event with `status="actual"` |
| CK-E007 | Dotted or otherwise invalid param key |
| CK-E008 | Unknown param referenced by a formula or `VatSpec.rate` |
| CK-E009 | Invalid `Recurrence` (unit, day out of range) |
| CK-E010 | Import conflict: `(source, ext_id)` exists with different payload — batch aborted |
| CK-E011 | Amount sign contradicts `direction` |
| CK-E012 | Generative item with `kind="stock"` |
| CK-E013 | Concurrent writer: lock held |
| CK-E020 | Cross-currency aggregation or fold |
| CK-W001 | Settlement remainder clamped to zero (fixed terms exceed accrual) |
| CK-W002 | Negative accrual routed through remainder on a fixed-amount settlement |
| CK-W003 | `actual` event dated on/after cutover |
| CK-W004 | Withholding in use with no `cat:tax` remittance item |
| CK-W005 | Division by zero in a selected branch |
| CK-W010 | Stale writer lock reclaimed |
| CK-I001 | `TaxRegime` present but no non-VAT `cat:tax` items (§9.5) |
| CK-I002 | `ChangeReport` empty — the write recorded nothing |
