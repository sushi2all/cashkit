# CashKit — ERP Pilot Guide

**Purpose** Validate CashKit against a real manufacturer's cash position using their ERP as the data source, and produce a repeatable mapping specification for building the ingestion pipeline.

**Audience** Implementation engineer running the pilot; the customer's controller and IT contact.

---

## 1. What the pilot is actually testing

Three things, in order of how likely they are to be the reason the pilot fails:

1. **Is the payment behaviour model right?** The ERP records contractual terms. Reality is different. A forecast built on terms rather than observed behaviour will be systematically early on inflows and roughly correct on outflows, which is the worst combination — it makes the trough look shallower and later than it is. **This is where pilots die.** Calibrating it is §5.
2. **Is the data complete?** Payroll, contributions, tax advances and financing flows frequently live outside the ERP. A forecast missing them is not slightly optimistic; it is missing the outflows that cause the crisis.
3. **Is the model structure right?** Do the generative/literal split, settlement terms and VAT handling reproduce the customer's actual cash movements? This is the easiest of the three and the one everyone worries about first.

Do not start by building the forecast. Start by **reproducing the last six months** from ERP data and checking it against the bank statement. If you cannot reproduce the past, the future is decoration.

---

## 2. Data source inventory

Cash-relevant data is never all in one place. Map every source before writing any pipeline code.

| Source | Typically holds | Usually available as |
|---|---|---|
| ERP — AR module | Open customer invoices, credit notes, payment terms, dunning status | SQL view, OData, REST, CSV export |
| ERP — AP module | Open supplier invoices, terms, approval status | Same |
| ERP — Sales | Order backlog not yet invoiced, contracts, delivery schedules | Same |
| ERP — Purchasing | Open POs, delivery schedules, blanket orders | Same |
| ERP — GL | Historical cash movements, tax accounts, bank accounts | Same |
| ERP — Fixed assets / leasing | Depreciation (not cash), lease payment schedules (cash) | Often a separate module |
| Payroll / HR system | Salaries, contributions, TFR, bonuses, 13th/14th month | Separate system, often only a monthly total |
| Banking | Balances, uncleared items, credit lines, factoring | Bank portal, CBI, PSD2, or a manual export |
| Commercialista | Tax advance schedule, IRES/IRAP projections, credits | Spreadsheet or email |
| Treasury / finance | Loan amortization, leases, factoring, revolving facilities | Contracts, often not systematized |
| Sales pipeline / CRM | Weighted opportunities not yet ordered | CRM |

**Assume the last four are not systematized.** Budget time to reconstruct them by interview and enter them as manual items. This is normal and is not a failure of the tool.

---

## 3. Canonical input mapping

The pipeline produces two kinds of CashKit input: **Events** (facts) and **Items** (patterns). Map ERP entities to the right one.

### 3.1 Decision rule

| ERP entity | CashKit input | Why |
|---|---|---|
| Posted invoice (AR/AP) | `Event`, `status="actual"` if paid, `"committed"` if open | It happened; it is a fact with a date and an amount |
| Confirmed sales/purchase order not yet invoiced | `Event`, `status="committed"` | Fact with a planned date, not yet a pattern |
| Recurring contract (maintenance, subscription, rent, lease) | `Item` with segments | A pattern; editing it once should change all future periods |
| Payroll | `Item` with segments, one per cohort | Pattern, changes by hiring plan |
| Weighted CRM pipeline | `Item` with segments and `probability < 1`, or `Event` with `status="forecast"` | Depends on whether it recurs |
| Loan / lease amortization | `Item` with an explicit schedule amount | Pattern with known irregular amounts |
| Tax advances | `Event`, `status="committed"` | Known dates, known-ish amounts |

### 3.2 Event mapping — AR (customer invoices)

| CashKit field | ERP source | Notes |
|---|---|---|
| `ext_id` | Invoice document number + line number | **Must be stable across re-extraction.** Composite key if line-level. |
| `source` | `"erp:ar"` | Fixed per pipeline |
| `date` | Invoice date (document date, not posting date) | The accrual date. Drives VAT tax point. |
| `amount` | Line net amount, signed positive for inflow | Net of VAT — VAT is computed by the engine |
| `currency` | Document currency | |
| `status` | `"actual"` if fully settled, else `"committed"` | Partial payments: see §3.6 |
| `item` | Mapped from customer + revenue category | See §3.7 for the mapping strategy |
| `tags.customer` | Customer code | Use the code, not the name — names change |
| `tags.customer_name` | Customer name | For display only |
| `tags.cat` | `"revenue"` or a finer category from the revenue account | |
| `tags.order` | Sales order number | Groups mixed-VAT lines |
| `tags.doc` | Invoice number | |
| `tags.cost_center` | Cost centre / profit centre | If used |
| `vat.rate` | Line VAT code → rate lookup | **Per line, not per document** |
| `vat.treatment` | Line VAT code → treatment class | Map exempt / reverse charge / split payment / export |
| `settlement.due` | Payment terms code → `DueTerm` list | **Adjusted by §5 calibration** |
| `settlement.due[].withholding` | Withholding code, if any | Ritenuta d'acconto |
| `note` | Free text | Optional |

**Line level, not document level.** A mixed-VAT invoice must produce one event per VAT rate. Aggregating to document level loses the rate and forces an average, which is wrong and hard to detect.

### 3.3 Event mapping — AP (supplier invoices)

Identical structure, with:

- `amount` **negative** (outflow). Store signed; `direction` is display only.
- `source = "erp:ap"`
- `tags.supplier`, `tags.supplier_name`
- `tags.cat` from the expense account: `"cogs"`, `"opex"`, `"capex"`, `"services"`
- `vat.recoverable` from the deductibility rule on the account — **critical for Italy**: auto 40%, telefonia 50%, rappresentanza. Get this from the account master, do not assume 100%.
- `status`: `"actual"` if paid, `"committed"` if approved and open, `"forecast"` if received but not approved

### 3.4 Event mapping — order backlog

Orders confirmed but not invoiced. This is where forecast inflow quality is made or lost.

| CashKit field | ERP source | Notes |
|---|---|---|
| `date` | **Planned invoice date**, not order date | Derive: planned delivery date + invoicing lag. If milestone-billed, use the milestone schedule. |
| `amount` | Open order value (ordered − delivered − invoiced) | Watch for partial deliveries |
| `status` | `"committed"` | |
| `tags.order`, `tags.customer` | | |
| `settlement` | Customer's terms, calibrated | |

**The planned invoice date is the hard part.** ERPs record delivery dates that slip. Measure the historical distribution of (actual invoice date − originally planned delivery date) and apply the median lag. Record the assumption explicitly as a param so it can be swept.

### 3.5 Item mapping — recurring contracts and payroll

| CashKit field | Source | Notes |
|---|---|---|
| `id` | Stable slug: `contract_<customer>_<type>` | |
| `segments[].start` / `.end` | Contract start / end date | Phased contracts → one segment per phase |
| `segments[].recurrence` | Billing frequency | `every` + `unit` |
| `segments[].amount` | Contract value per period | |
| `segments[].escalation` | Indexation clause (often ISTAT) | Reference a param: `p.istat_index` |
| `settlement` | Contract payment terms | |
| `vat` | Contract VAT treatment | |
| `tags` | customer, cat, contract number | |

Payroll: one item per cohort (direct labour, indirect, management), not per employee, unless headcount planning demands otherwise. Model 13th/14th month as separate segments with annual recurrence on the correct months — a flat monthly average will misplace ~15% of annual labour cost by up to six months.

### 3.6 Partial payments and credit notes

- **Partial payment**: split into one `"actual"` event for the settled portion and one `"committed"` event for the residual, sharing `tags.doc` and with `ext_id` suffixed (`INV-123-L1-P1`, `INV-123-L1-R`). Do not mutate the original — the ledger is append-only.
- **Credit note**: a separate event with opposite sign, `tags.doc` referencing the original, `ext_id` from the credit note number.
- **Write-off**: an event with `settlement.due = []` (accrual, never settles).

### 3.7 Item reference strategy for events

Events reference an `item` to inherit tags, VAT and settlement. For ERP-sourced events, create a **synthetic item per (customer × revenue category)** or (supplier × expense category), generated by the pipeline. This keeps the item count manageable (tens to low hundreds) and gives scenario overlays something meaningful to target — "cut all Acme revenue 30%" works because there is an Acme item.

Do not create one item per invoice. Do not create a single catch-all item.

---

## 4. Data quality gates

Run before every ingestion. Fail loudly; do not ingest partial data.

| Gate | Check | Action on failure |
|---|---|---|
| Completeness | Row count and sum vs the ERP's own AR/AP ageing report | Stop. Investigate the filter. |
| Balance tie-out | Sum of open AR events = AR control account balance in GL | Stop. Usually a missing document type. |
| VAT code coverage | Every line's VAT code exists in the rate map | Stop. Unmapped code silently becomes 0%. |
| Terms coverage | Every customer/supplier has a mapped payment term | Default to the most common term and emit a warning; count them. |
| Date sanity | No invoice dates in the future, none before the horizon start | Quarantine and report |
| Currency | All currencies present in the FX param table | Stop |
| Duplicate `ext_id` | Uniqueness within the batch | Stop. Indicates a broken composite key. |
| Sign convention | AR positive, AP negative, no exceptions | Stop |

The `import_events` all-or-nothing guarantee makes these safe: a failed gate leaves the ledger untouched.

---

## 5. Payment behaviour calibration

**The single most important step. Do it before building any forecast.**

ERP payment terms are what the contract says. Cash arrives when the customer decides. The gap is routinely 20–40 days and is not random — it clusters by customer, by size, and by whether the customer runs payment runs on fixed dates.

### 5.1 Extract the history

For every settled AR invoice in the last 24 months:

```
customer_code | invoice_date | due_date_per_terms | payment_date
             | amount | delay_days = payment_date - due_date_per_terms
```

### 5.2 Segment and summarize

Compute per customer (and, for customers with too few invoices, per segment — size band, country, channel):

- median delay, p25, p75, p90
- payment-run signature: are payment dates clustered on specific days of month? Extract the modal day.
- variance trend: is the delay growing? A widening delay is an early distress signal and belongs in the pilot report regardless of the forecast.

Aim for ≥12 settled invoices before trusting a per-customer figure; otherwise fall back to the segment.

### 5.3 Translate into settlement terms

```python
# contractual: net 60
# observed:    median delay +27d, payment run on the 10th
Settlement(due=[DueTerm(
    share=Decimal(1),
    offset="87d",
    basis="accrual",
    adjust="next",
)])
```

For customers with a clear payment-run day, use `basis="month_end"` with the appropriate offset rather than a raw day count — it reproduces the clustering, which matters when the forecast is used for weekly liquidity decisions.

For customers with wide dispersion, split the settlement to represent it:

```python
Settlement(due=[
    DueTerm(share=Decimal("0.6"), offset="75d"),
    DueTerm(share=Decimal("0.4"), offset="110d"),
])
```

This is not a probability distribution — it is a deterministic approximation of one. Say so in the report. Full stochastic treatment is deferred (PRD §7.3).

### 5.4 Record it as data

Store calibrated terms as a **param table**, not as literals inside items:

```yaml
# params.yaml
dso.acme: "87"
dso.default: "68"
dso.segment.small: "52"
```

So recalibration is one operation and the sensitivity of the forecast to payment behaviour is directly sweepable — which is often the most useful output of the whole exercise.

### 5.5 Do the same for AP

Less critical (you control when you pay) but still needed, because *actual* payment behaviour reveals the customer's real policy, including whether they stretch payables when cash is tight. If they do, the historical AP delay is endogenous to their cash position and will mislead the forecast. Flag it if you see it.

---

## 6. Pipeline architecture

```
ERP (SQL / OData / REST)
      │  scheduled extract, incremental by modified-date
      ▼
  Raw landing  (Parquet, immutable, one file per extract)
      │  validation gates (§4) — fail closed
      ▼
  Canonical staging  (typed, one row per future CashKit event)
      │  mapping: VAT codes, terms → calibrated settlement, item assignment
      ▼
  kit.import_events(source="erp:ar", rows=...)   ← idempotent on (source, ext_id)
      │
      ▼
  CashKit ledger.sqlite
```

**Design notes**

- **Land raw and keep it.** When numbers are disputed, you need the extract as it was, not a re-query against a database that has moved.
- **Incremental by modified-date, full reconciliation weekly.** Incremental extracts miss backdated corrections; a weekly full pull catches them. Because import is idempotent, a full pull is cheap and safe.
- **The pipeline never writes CashKit files directly.** It calls `import_events`. Same rule as for agents, same reason.
- **Master data — items, contracts, payroll, tax — is not pipelined initially.** Curate it by hand for the pilot. Automate only what proves stable.
- **Schedule**: AR/AP daily; backlog daily; payroll monthly; bank balance daily if available. `set_cutover` weekly or monthly, manually, after reconciliation.

Orchestration: Prefect fits, and gives retries and observability for free. Keep the extract, validate, map and load steps as separate tasks so a failure is diagnosable.

---

## 7. Pilot protocol

### Week 0 — Scoping

- Identify the ERP, modules in use, and extraction method
- Confirm access: read-only DB user or API credentials
- Inventory sources (§2), naming who owns each
- Agree the pilot's success metric up front (§8)
- Confirm entity scope: one legal entity, one set of bank accounts

### Week 1 — Extract and reproduce the past

- Build extracts for AR, AP, backlog, GL cash movements — 24 months of history
- Run quality gates
- **Reproduce the last 6 months of bank movements from ERP data alone.** Compare weekly closing balance to the bank statement.
- Explain every discrepancy above 2% of weekly turnover. Discrepancies are findings, not noise: they usually reveal a whole category of flows nobody mentioned.

**Do not proceed until the reproduction holds.**

### Week 2 — Calibrate and complete

- Payment behaviour calibration (§5)
- Interview for the non-ERP flows: payroll, contributions, tax advances, loans, leases, factoring
- Build the tax coverage statement (PRD §9.5). Present it to the controller and get explicit confirmation of what is missing.
- Enter manual items for everything the ERP does not carry

### Week 3 — Build the forward model

- Generate synthetic items from the ERP dimensions
- Enter contracts and payroll as generative items
- Configure `TaxRegime` for VAT; confirm periodicity and tax point with the commercialista
- Set `cutover` to the last reconciled month end
- Run, review, and iterate against the controller's intuition — where the model and the controller disagree, one of them is learning something

### Week 4 — Scenarios and handover

- Build 3 scenarios: base, downside (revenue haircut + DSO stretch), upside
- Run the backtest (§8)
- Hand over: SDK access, the skill package, the pipeline, and the calibration methodology
- Agree the recalibration cadence — quarterly is usually right

---

## 8. Validation and backtesting

### 8.1 Backtest protocol

The strongest possible evidence, and it requires no waiting:

1. Set `cutover` to a date 3 months in the past. Commit.
2. Run the model. This is the forecast you *would have made* then, using only data available then.
3. Compare against what actually happened.
4. Repeat at 6 and 12 months back.

This works only because `cutover` is a stored field and nothing reads the clock. Use `kit.at()` if the model itself existed at those dates; otherwise simulate by truncating the ledger to the watermark.

### 8.2 Metrics

| Metric | Definition | Target |
|---|---|---|
| Weekly closing balance MAPE | Mean absolute % error, weeks 1–4 | < 5% |
| Same, weeks 5–13 | | < 12% |
| Trough timing error | Days between forecast and actual minimum-cash date | < 7 days |
| Trough depth error | % error on the minimum balance | < 10% |
| Directional accuracy | % of weeks where the sign of the change is right | > 85% |
| Coverage | % of actual cash movements attributable to a modelled item | > 95% |

**Coverage is the one to watch.** High MAPE with high coverage means calibration; low coverage means the model is missing whole categories, and no amount of tuning fixes that.

### 8.3 Variance analysis as a standing practice

After go-live, every reconciliation produces a variance report: group the frame by `status` and `tags.cat` over the same window, comparing forecast to actual. Persistent one-sided variance in a category is a calibration signal. Feed it back into §5 quarterly.

---

## 9. ERP-specific notes

General patterns, to be confirmed per installation.

| ERP | Extraction | Watch for |
|---|---|---|
| SAP S/4HANA | CDS views, OData; `BSID`/`BSAD`/`BSIK`/`BSAK` classically | Document splitting; special GL indicators (down payments) sit outside normal AR; `ZLSCH` payment method drives real timing |
| Dynamics 365 BC / NAV | OData v4, AL API | Dimension usage varies wildly between installs; posting groups drive account mapping |
| Zucchetti (Ad Hoc, Infinity) | SQL Server direct, or their web services | Italian VAT registers are well modelled — use them rather than re-deriving; scadenzario is the AR/AP schedule and is authoritative |
| TeamSystem (Alyante, Gamma) | SQL, or REST on newer versions | `scadenze` table is the right source for expected dates; effetti (RiBa) have distinct timing |
| Panthera / Mago | SQL direct | Sparse standard API; expect custom views |
| Odoo | XML-RPC / REST, or direct Postgres | `account.move.line` is the workhorse; watch analytic accounts for cost centres |

**Italy-specific across all of them:**

- **RiBa / SDD**: presented effects have a value date distinct from the invoice due date, and may be presented to a bank *salvo buon fine* — creating a financing inflow at presentation and a contingent liability. Model as a separate financing item, not as early collection.
- **Split payment (PA customers)**: VAT is not collected. `treatment="split_payment"`. Common for anyone selling to public entities.
- **Scadenzario**: most Italian ERPs maintain an explicit due-date schedule. Prefer it over deriving dates from terms codes — it already reflects manual adjustments.
- **Factoring / anticipo fatture**: very common and almost never in the ERP. Ask directly. It transforms timing completely and its absence is a classic reason reproduction fails in Week 1.

---

## 10. Deliverables

| Artifact | Content |
|---|---|
| `mapping-spec.yaml` | Field-level mapping, VAT code table, terms table, item generation rules |
| `calibration-report.md` | Per-customer DSO analysis, segment fallbacks, dispersion, trend |
| `coverage-statement.md` | What is modelled, what is not, signed off by the controller |
| `reproduction-report.md` | Week 1 past-reproduction results and every explained discrepancy |
| `backtest-report.md` | §8 metrics at 3, 6, 12 months |
| Pipeline code | Extract, validate, map, load — with the quality gates as tests |
| CashKit book | Committed repo with base + scenarios |

## 11. Pilot success criteria

The pilot succeeds when:

1. Past reproduction holds: 6 months of weekly closing balances within 3% of bank, all larger discrepancies explained.
2. Backtest at 3 months meets the §8.2 targets.
3. Coverage > 95%.
4. The controller signs the coverage statement and agrees the omissions are understood.
5. The customer can run a scenario unaided using the SDK or the agent skill.

Criterion 4 matters as much as the numeric ones. A forecast whose gaps are known and quantified is useful. A forecast whose gaps are invisible is worse than none — it produces confident decisions on incomplete information, which is precisely the failure mode a cash forecast exists to prevent.
