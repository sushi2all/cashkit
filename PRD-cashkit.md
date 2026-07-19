# CashKit — Product Requirements Document

**Version** 0.1 (design freeze candidate)
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
| D5 | **The Item is the atom of override.** `segments` is an atomic field. | List-merge semantics on nested config is where these systems die. |
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

Because actuals are immutable and append-only, a historical `at(ref)` needs to know whether the ledger has moved. Store a **ledger watermark** (max `rowid` + row count hash) on `book.yaml`; a run at an old revision uses the ledger truncated to that watermark.

### 3.4 Remote access

DuckDB's Quack protocol (core_nightly in v1.5.2; stable with DuckDB v2.0, September 2026) turns `frames.duckdb` into a server. This is the mobile/dashboard story: DuckDB-Wasm in the browser connects directly over HTTP, no backend middleware.

**Treat Quack as optional and not load-bearing until v2.0 ships.** Parquet export is the stable sharing path. The `FrameStore` protocol must abstract both.

---

## 4. Data model

All models are Pydantic v2. `Money` is `Decimal` at the boundary, int64 minor units internally.

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
    params: dict[str, Decimal]      # named scalars: vat.standard, inflation, fx.eur_usd
    items: dict[ItemId, Item]
    tax_regimes: list[TaxRegime]
```

**`params` is the lever surface.** Anything an agent might sweep must be a param, not a literal inside a formula. VAT rates, escalation rates, FX, churn, headcount cost — all params.

### 4.2 Item — the generative input

```python
class Item(BaseModel):
    id: ItemId
    name: str
    kind: Literal["flow", "derived", "stock"]
    direction: Literal["in", "out"] | None    # display only; storage is signed
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
    amount: Amount                            # constant | expression | explicit schedule
    escalation: Escalation | None
    probability: Decimal = 1                  # pipeline weighting

class Recurrence(BaseModel):
    every: int
    unit: Grain
    anchor: Literal["period_start", "period_end", "day_of_month", "eom"] = "period_start"
    day: int | None = None
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
- Fixed `amount` entries consume first; `remainder` takes what is left. If the accrued amount is smaller than the fixed entries, **clamp to zero and emit a warning diagnostic** (partial delivery produces this legitimately).

### 4.5 VAT and tax

```python
class VatSpec(BaseModel):
    rate: str | Decimal = "vat.standard"    # param key by default, literal allowed
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

1. Item-level last-write-wins along the parent chain.
2. `segments` is atomic. Touch one, replace the list. No positional patching, no ID matching, no partial merge.
3. Scalar fields merge sparsely — so a later correction to `tags` or `settlement` in base propagates into scenarios that did not override them.

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
- Escalation `(1+r)^n` uses a float64 intermediate, exact at these magnitudes — **but must be property-tested against a Decimal reference implementation in CI**.
- `Decimal` at parse, serialize, and display. The engine core never sees it.
- **Never float for money.** The float64 use above is confined to a rate exponentiation with a rounding step.

### 5.4 Formula language

Restricted Python AST walk. Whitelisted node types. No `eval`, no attribute access, no calls outside a fixed builtin table.

| Symbol | Meaning |
|---|---|
| `it("acme_impl")` | value of another item, this period |
| `prev("cash", n=1)` | value n periods back — the only cycle-breaker; `n` must be a literal |
| `p.vat_standard` | named param |
| `agg(tag="cat:revenue")` | sum over items matching a selector, this period |
| `cum("revenue")` | running total since horizon start |
| `t.index`, `t.month`, `t.is_quarter_end`, `t.is_business_day` | period metadata |
| `where(cond, a, b)` | **elementwise select — both branches always evaluated** |
| `min`, `max`, `clip`, `round_`, `abs_` | safe builtins |

**`if_` does not exist.** Only `where`. Any function added later must be expressible as a masked column operation.

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
    """Idempotent on (source, ext_id). Returns inserted / skipped / conflicted counts
       plus per-row diagnostics. NEVER partially applies: all-or-nothing per batch."""
def query_events(book, where=None, since=None, until=None) -> Table
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
    """Out of segment? probability 0? upstream null? suppressed by cutover?"""
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
| Automatic currency hedging logic | Modelling, not execution | FX rates as param time series; conversion as a derived measure |
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
- Postgres backend (only if concurrent human editing or a hosted UI arrives — Quack's multi-writer support weakens this case)
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
- Round-trip property test: `serialize(parse(x)) == x` byte-for-byte, over generated books. Phantom diffs are a build failure.
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
- `at("HEAD~N")` reproduces historical numbers exactly, across a schema migration boundary.
- `diff_revisions()` shows nothing for a pure reformat.
- Config diff and outcome diff appear in the same commit.
