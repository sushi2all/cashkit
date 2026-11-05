CashKit is a deterministic cash-flow modelling engine. You drive it only
through its Python SDK: one object, `CashKit`, holds a book, its scenarios, its
ledger and its history. Money is `decimal.Decimal` everywhere — never float.

## Hard rules

1. Never read or write the book's files (`*.yaml`, `ledger.sqlite`,
   `frames.duckdb`, `.cashkit/`) and never run git. Everything goes through
   the SDK.
2. Money and rates are `Decimal("…")` — never float literals, never `float()`.
3. **Authored amounts are VAT-exclusive (net).** A price "including VAT" must
   be converted to net before authoring; the engine adds VAT itself.
4. **Signs carry direction.** Inflows are positive with `direction="in"`;
   outflows are negative with `direction="out"`. A contradiction is refused
   (`CK-E011`).
5. Dates are authored explicitly. Never use `date.today()`/`datetime.now()`;
   never set `cutover` to today.
6. Errors are data: every write returns a `ChangeReport` and every handle
   comes with diagnostics. Read them; an `error` diagnostic means the write
   was refused or the book is broken. `CK-I002` means nothing changed.
7. Prefer params to literals for anything that might be swept (rates,
   volumes, prices), and reference them (`p.<key>` in formulas, a param key as
   an escalation or VAT rate).
8. Commit only when asked (`kit.commit(message)`), after `validate()` is
   clean.
9. One-off cash movements are **events**, not items. Items need a recurrence.
10. Actuals (`status="actual"` events) are immutable: correct them with
    `correct_event`, never void them, never override them in a scenario.

## Imports

```python
import tempfile
from datetime import date
from decimal import Decimal

from cashkit.sdk import CashKit, create_book, ScaleItems, ShiftItems, RetagItems
from cashkit.model import (
    Amount, CalendarSpec, DueTerm, Escalation, Event, Grain, Item, PeriodRange,
    Recurrence, Segment, Settlement, TaxRegime, VatSpec,
)

ROOT = tempfile.mkdtemp() + "/book"   # in your task, ROOT is given to you
```

## Opening or creating a book

```python
# create_book(root, *, id, horizon, opening_balance, grain=Grain.DAY,
#             calendar=None, cutover=None, params=None) -> (kit | None, diagnostics)
kit, diagnostics = create_book(
    ROOT,
    id="demo_2026",                                   # [a-z][a-z0-9_]*
    horizon=PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)),  # end EXCLUSIVE
    opening_balance=Decimal("25000"),
    grain=Grain.MONTH,                                # DAY | WEEK | MONTH | QUARTER | YEAR
    params={"vat_standard": Decimal("0.22")},
)
assert kit is not None, diagnostics

# A second create_book at the same root is refused with CK-E031: open it instead.
again, diagnostics = create_book(ROOT, id="demo_2026",
                                 horizon=PeriodRange(start=date(2026, 1, 1), end=date(2027, 1, 1)),
                                 opening_balance=Decimal("25000"))
assert again is None and diagnostics[0].code == "CK-E031"
kit, diagnostics = CashKit.open(ROOT)                 # (kit | None, diagnostics); CK-E029 if no book
```

- `calendar` accepts a country code (`"IT"`) or `CalendarSpec(weekend={5, 6}, holidays=[...])`.
- `cutover` (default: horizon start) is the last reconciled boundary; generative
  items are suppressed before it, where the ledger's actuals are authoritative.
- Book-level fields change with `kit.set_book(cutover=..., opening_balance=...,
  horizon=..., calendar=..., tax_regimes=[...])`. `id` and grain are fixed.

## Items

```python
Item(
    id="rent",                       # [a-z][a-z0-9_]*, unique in the book
    name="Office rent",
    kind="flow",                     # "flow" (generative, has segments) | "derived" (formula) | "stock" (derived only)
    direction="out",                 # "in" | "out" — must agree with the amount's sign
    tags={"cat": "premises"},        # dimensional: key -> value, used by selectors
    flags={"committed"},             # boolean markers, used by selectors as flag:<name>
    segments=[...],                  # flows only
    formula=None,                    # derived/stock only
    settlement=None,                 # when cash moves; None = same day as the accrual
    vat=None,                        # VatSpec or None
    agg_rule="sum",                  # "sum" | "last" | "mean" when rolling up to a coarser grain
)
```

A **flow** generates amounts from **segments**:

```python
Segment(
    start=date(2026, 1, 1),
    end=None,                        # EXCLUSIVE; None = open-ended (to the horizon end)
    recurrence=Recurrence(every=1, unit=Grain.MONTH),
    amount=Amount(constant=Decimal("-1200")),
    escalation=None,                 # Escalation(rate=..., every_years=1)
    probability=Decimal("1"),        # pipeline weighting: cash and accrual are multiplied by it
)
```

- `end` is exclusive: "January to June inclusive" is
  `start=date(2026, 1, 1), end=date(2026, 7, 1)`.
- `Recurrence(every, unit, anchor="period_start", day=None, business_day_adjust="none")`:
  `every=3, unit=Grain.MONTH` is quarterly. `anchor="day_of_month", day=27`
  pays on the 27th (clamped to the month's last day); `"eom"` is end of month;
  `business_day_adjust="prev" | "next"` moves weekend/holiday dates.
- `Amount(constant=x)` or `Amount(schedule=[(date, x), ...])` — exactly one.
  A schedule gives the amount for the occurrence on each listed date; an
  occurrence with no entry is zero.
- `Escalation(rate="rent_escalation")` reads a param (or a `Decimal` literal):
  the amount is multiplied by `(1 + rate)^n` after every `every_years` full
  years from the segment start.
- Several segments on one item model phases (ramp-up, then run-rate); the
  list is replaced whole on every write.

```python
monthly = Recurrence(every=1, unit=Grain.MONTH)
report = kit.set_item(Item(
    id="rent", name="Office rent", kind="flow", direction="out", tags={"cat": "premises"},
    segments=[Segment(start=date(2026, 1, 1), recurrence=monthly,
                      amount=Amount(constant=Decimal("-1200")))],
))
print(report.created, report.changed, report.diagnostics)   # ('rent',) () ()

kit.set_param("price", Decimal("40"))
kit.set_param("units", Decimal("120"))
kit.set_item(Item(id="product", name="Product sales", kind="derived", direction="in",
                  tags={"cat": "revenue"}, formula="p.price * p.units"))
```

Writing an item with an existing id **re-authors it** (by value, whole item).
To change one field, copy and update: `kit.set_item(kit.book.items["rent"].model_copy(update={...}))`.
`kit.remove_item(id, scenario=...)` removes an item.

## Settlement — when cash moves

Accrual is when the amount is earned/incurred; cash is when it is paid.

```python
Settlement.immediate()                                   # cash on the accrual date
Settlement.net(60)                                       # all cash 60 days later
Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.7"), "2m")])   # shares sum to exactly 1
Settlement(due=[
    DueTerm(amount=Decimal("1000"), offset="0d"),        # fixed deposit first
    DueTerm(remainder=True, offset="45d"),               # whatever is left
])
Settlement(due=[DueTerm(share=Decimal("1"), offset="0d", withholding=Decimal("0.2"))])  # ritenuta
Settlement(due=[])                                       # never settles (accrual only)
```

Durations are `"<n>d" | "<n>w" | "<n>m" | "<n>y"`, calendar-semantic. `DueTerm`
also takes `basis="accrual" | "period_end" | "month_end"` and `adjust`.
Withholding reduces the cash moved; the remittance to the state is **not**
generated — add it as its own item tagged `cat: tax` (else `CK-W004`).

## VAT and tax

```python
kit.set_item(Item(
    id="consulting", name="Consulting", kind="flow", direction="in",
    segments=[Segment(start=date(2026, 1, 1), recurrence=monthly,
                      amount=Amount(constant=Decimal("1000")))],   # NET of VAT
    vat=VatSpec(rate="vat_standard"),        # param key (default) or Decimal; treatment="standard"
))
kit.set_book(tax_regimes=[TaxRegime(
    id="iva",
    accumulates="",                 # "" = every item carrying a VatSpec; else a selector
    measure="accrual",              # tax point: "accrual" (invoice date) | "cash" (IVA per cassa)
    periodicity="quarterly",        # "monthly" | "quarterly" | "annual"
    payment_offset="16d",           # after each period ends
)])
```

- The engine grosses up the cash leg (1,000 net at 22% collects 1,220) and
  routes the VAT through the regime: net liability paid after each period, a
  credit carried forward as a stock.
- `VatSpec(treatment=...)`: `standard`, `exempt`, `reverse_charge`,
  `out_of_scope`, `export`, `split_payment`; `recoverable=Decimal("0.4")` for
  partially deductible input VAT.
- Taxes the engine does not model (IRES/IRAP advances, INPS, TFR, December VAT
  advance, credits) are manual items or events tagged `cat: tax`, with
  `flags={"manual_tax"}`.

## Formulas (derived and stock items)

A formula is a restricted Python expression, evaluated per period:

| Symbol | Meaning |
|---|---|
| `it("sales")` | another item's value this period |
| `prev("cash", n=1, init=0)` | value `n` periods back (`n` literal; `init` a literal or `p.<key>`); the only way to break a cycle |
| `p.vat_standard` | a param |
| `agg(tag="cat:revenue")` | sum over items matching a selector, this period |
| `cum("sales")` | running total since the horizon start |
| `t.index`, `t.month`, `t.is_quarter_end`, `t.is_business_day` | period metadata |
| `where(cond, a, b)` | elementwise choice; **both branches are evaluated**; there is no `if` |
| `min`, `max`, `clip`, `round_`, `abs_` | safe builtins |

Bare names are not values (write `it("sales")`, not `sales`). Division by zero
yields 0 (with `CK-W005` when that branch is selected). Numbers inside a
formula string are exact decimals.

```python
kit.set_param("commission_rate", Decimal("0.05"))
kit.set_item(Item(id="commission", name="Commission", kind="derived", direction="out",
                  formula='-p.commission_rate * it("product")'))
kit.set_item(Item(id="ytd_revenue", name="YTD revenue", kind="stock", agg_rule="last",
                  formula='cum("product")'))      # a stock is a tracker: it does not move cash
```

A derived item's value is both its accrual and its cash; sign it like a flow
(negative for outflows).

**Selectors** (in `agg()`, macros, `TaxRegime.accumulates`, `frame(where=)`):
space-separated terms, all must match. A term is `key:value` (tag equality) or
`flag:name`. No OR, no negation, no wildcards: `"customer:acme cat:revenue"`.

## Scenarios

Every write takes `scenario=` (default `"base"`). Base is the authored book;
any other scenario stores only what differs from its parent, so base is never
touched by a what-if.

```python
kit.fork("downside", parent="base", note="lower volume")
kit.set_param("units", Decimal("80"), scenario="downside")
kit.apply_macro(ScaleItems(selector="cat:revenue", factor=Decimal("0.9")), scenario="downside")
kit.apply_macro(ShiftItems(selector="cat:premises", by="2m"), scenario="downside")
kit.apply_macro(RetagItems(selector="cat:premises", tags={"owner": "ops"}), scenario="downside")
kit.set_param("opening_balance", Decimal("40000"), scenario="downside")   # reserved key
kit.remove_item("rent", scenario="downside")         # gone in downside and its children only
kit.fork("downside_late", parent="downside")         # scenarios chain; fields fall through
print(kit.diff("base", "downside"))
print(kit.provenance("product", scenario="downside"))
```

- `kit.unset(item_id, scenario=...)` drops a scenario's override (back to the parent).
- `kit.resolve(scenario).book` is the materialised book; `kit.scenarios` maps ids to scenarios.
- Macros expand immediately into concrete per-item writes.

## The ledger — events

```python
kit.add_event(Event(id="laptops", date=date(2026, 3, 12), amount=Decimal("-2400"),
                    status="forecast", tags={"cat": "capex"}))   # "actual" | "committed" | "forecast"
report = kit.import_events(
    [Event(id="tx1001", date=date(2026, 1, 5), amount=Decimal("-49.90"), status="actual",
           source="bank:main", ext_id="TX1001")],
    source="bank:main",
)   # idempotent on (source, ext_id): re-importing the same rows inserts nothing
print(report.inserted, report.skipped, report.conflicted)
kit.void_event("laptops", note="cancelled")                       # forecast/committed only
old = next(e for e in kit.events_for("base")[0] if e.id == "tx1001")
kit.correct_event("tx1001", old.model_copy(update={"id": "tx1001_fix", "amount": Decimal("-59.90"),
                                                 "corrects": "tx1001"}), note="keying error")
events, diagnostics = kit.events_for("base")                      # live events as a scenario sees them
```

`Event` fields — there are no others (unknown fields are refused):

```python
Event(
    id="inv_2026_014",              # unique ledger id
    date=date(2026, 4, 2),
    amount=Decimal("1500"),         # signed: + in, - out; net of VAT
    status="committed",             # "actual" | "committed" | "forecast"
    item=None,                      # an item id to inherit tags / VAT / settlement from
    tags={},                        # merged over the item's tags
    vat=None, settlement=None,      # override the item's
    currency="EUR",
    source=None, ext_id=None,       # idempotency key for imports: (source, ext_id)
    note=None,                      # free text (memo, description)
    corrects=None,                  # the id this event corrects
)
```

`settlement=None` on an item or event means "cash moves on the accrual date";
it behaves like `Settlement.immediate()` but is a different value, so compare
behaviour (the run), not the objects.

An event may reference an item (`item="consulting"`) to inherit its tags, VAT
and settlement; its amount then lands in **that item's** accrual and cash
columns. An unattached event lands in a synthetic `_event:…` column. A conflicting re-import (same `(source, ext_id)`, different
payload) aborts the whole batch with `CK-E010`.

## Running and reading results

```python
run = kit.run("base")                       # RunRef; deterministic
print([d.code for d in run.diagnostics])
s = run.summary()                           # RunSummary, all Decimal at 4 dp
print(s.opening_balance, s.closing_balance, s.min_cash, s.min_cash_period,
      s.runway_end, s.breakeven_period, s.total_inflow, s.total_outflow, s.net_cash)

table = run.frame(measures=["cash"], where="cat:revenue", include_synthetic=False)
for row in table.to_dicts():                # columns: period_start, period_end, item_id,
    pass                                    #   measure, value (Decimal), currency, status
wide = run.pivot(index="period", columns="tag:cat", values="cash")
explanation = run.trace("product", date(2026, 3, 1), measure="cash")   # why a cell is what it is
print(kit.validate("base"))                 # list[Diagnostic], errors first; [] when clean
print(kit.describe_book())                  # items, tags, params, measures
```

- `summary().runway_end` is the start date of the first period whose balance
  is negative (`None` if it never happens inside the horizon);
  `total_outflow` is negative.
- `run.result.cash[item_id]` / `run.result.accrual[item_id]` are numpy int64
  arrays in minor units of 1/10000 (divide a `Decimal` of the value by
  `Decimal(10000)`); `run.result.periods.starts` lists the period start dates.
  Prefer `summary()` and `frame()`.
- Measures are `accrual` and `cash`. Cash is what hits the bank.

## Version control

```python
errors = [d for d in kit.validate() if d.severity == "error"]
assert not errors, errors
commit = kit.commit("Q1 plan as presented")   # CommitReport; .revision is None if nothing changed
print(kit.status().clean)                     # True right after a commit
print([r.message for r in kit.history(limit=5)])
past, diagnostics = kit.at("HEAD")            # a read-only kit at a revision
print(past.run("base").summary().closing_balance)
```

## ChangeReport and diagnostics

Every write returns a `ChangeReport(target, created, changed, diagnostics)`.
Every `Diagnostic` has `severity`, `code`, `item_id`, `field`, `message`,
`suggested_fix`. Codes:

| Code | Meaning |
|---|---|
| CK-E001 | Unknown item id in `it()`/`prev()`/`cum()`, or a selector matching nothing |
| CK-E002 | Circular dependency without `prev()` |
| CK-E003 | Formula or selector rejected (disallowed syntax, bare name, non-literal `n`), or a formula on a flow |
| CK-E004 | Settlement shares do not sum to exactly 1 |
| CK-E005 | Settlement mixes `share` and `amount`, or has more than one `remainder` |
| CK-E006 | Scenario overlay touches an actual event |
| CK-E007 | Invalid param key |
| CK-E008 | Unknown param referenced (formula, escalation or VAT rate) — define it with `set_param` |
| CK-E009 | Invalid recurrence |
| CK-E010 | Import conflict on `(source, ext_id)` — batch aborted |
| CK-E011 | Amount sign contradicts `direction` |
| CK-E012 | A generative (segments) item with `kind="stock"` |
| CK-E013 | Another writer holds the lock |
| CK-E014 | Ledger event not found |
| CK-E015 | Ledger event cannot be changed that way (append-only) |
| CK-E016 | `void_event` on an actual — use `correct_event` |
| CK-E017 | Imported row without `ext_id` |
| CK-E018 | Event cannot attach to that item |
| CK-E019 | Tax regime misconfigured, or its selector matches nothing |
| CK-E020 | Cross-currency aggregation |
| CK-E021 | Unknown scenario |
| CK-E022 | Scenario already exists |
| CK-E023 | Scenario overrides an item its parent chain does not define |
| CK-E024 | `opening_balance` param is not valid money |
| CK-E025 | Stored state unreadable |
| CK-E026 | Stored state from a newer schema |
| CK-E027 | Revision ref does not resolve |
| CK-E028 | Historical reproduction mismatch |
| CK-E029 | No book at that path |
| CK-E031 | A book already exists at that path — open it |
| CK-E032 | Book creation refused (bad id, horizon, money) |
| CK-E033 | Frame store unavailable |
| CK-W001 | Settlement remainder clamped to zero |
| CK-W002 | Negative accrual routed through the remainder |
| CK-W003 | Actual event dated on/after cutover |
| CK-W004 | Withholding with no `cat:tax` remittance item |
| CK-W005 | Division by zero in a selected branch |
| CK-W006 | Cutover outside the horizon |
| CK-W010 | Stale writer lock reclaimed |
| CK-W011 | Engine version moved since a revision |
| CK-I001 | A tax regime exists but no non-VAT `cat:tax` items |
| CK-I002 | The write recorded nothing (value already so) |
