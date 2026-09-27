# DATASET — Delta Ingegneria 2026

The one demo book every CashKit desktop screen uses. Every figure below is printed by `design/desktop/dataset.py` (integer minor units at 4 dp, Decimal factors, 4 dp steps HALF_UP as the engine's default policy, display 2 dp HALF_EVEN, no float). Regenerate with `python3 design/desktop/dataset.py > design/desktop/DATASET.md`. Do not edit figures by hand.

## The company

Delta Ingegneria S.r.l., Bergamo. 25 people, engineering and automation services, CCNL metalmeccanico (13th month in December, no 14th). One bank account (Intesa, IT60 X054 2811 1010 0000 0123 456) and one credit line (fido di cassa € 150,000.00, undrawn). Six customers: two retainers, small support contracts, one PA customer paying at 120 days under split payment, one robotics customer paying 30/70, and a probability-weighted framework agreement from November. Outflows: net payroll on the 27th, one F24 on the 16th (IVA, INPS and IRPEF, INAIL in February), monthly VAT, a term loan, vehicle leases with 40 % VAT recoverability, utilities, telco, buoni pasto, expense reimbursements, professional fees, fuel cards, the fido commission, freelancers with 20 % ritenuta.

The controller (M. Conti) rolls the cutover every Monday. The last roll was Mon 21 Sep (r51, cutover Sun 20 Sep 2026); the bank statement to Fri 25 Sep 2026 was imported Friday evening and waits for the Monday close. The CFO (L. Ferri) recalibrated Acme and Borghi terms in May after Q1 payments came in late. IRES/IRAP acconti and the ritenuta remittance are not modelled, which is what the diagnostics say; the June acconto sits in the ledger as an uncovered bank movement.

## Book identity

| field | value |
|---|---|
| book id | `delta-ingegneria` |
| name | Delta Ingegneria 2026 |
| currency | EUR (single currency per book) |
| horizon | [2026-01-01 , 2027-07-01) — half-open; the last day in the frame is 30 Jun 2027 |
| base grain | day (views aggregate to week / month / quarter) |
| opening balance (1 Jan 2026) | € 196,350.00 |
| cutover | 20 Sep 2026 (Sun; last reconciled date, committed at r51) |
| bank statement imported to | 25 Sep 2026 (Fri) — open window (20 Sep, 25 Sep] |
| as-of (host clock) | 28 Sep 2026 (Mon) |
| revision | r51 · 7c2e19b |
| engine | 1.4.0 — **assumption**: `ENGINE_VERSION` is the string `"1"` today; the demo assumes a future semver so the history can show a version move (1.3.2 → 1.4.0) and CK-W011 |
| rounding policy | 4 dp HALF_UP in the engine (default `RoundingPolicy`); 2 dp HALF_EVEN at display |
| calendar | IT holidays 2026–2027 resolved at book creation; weekend Sat/Sun |
| accounting day | end of month (lines that name no day) |
| VAT regime | `iva` · monthly · accrual tax point · `payment_offset: 16d` (month end + 16 d = the 16th) · no surcharge · credit carried. The engine applies **no business-day adjust** to a tax payment (SDK request SR-2): a 16th that is a Saturday stays a Saturday in the frame |
| pre-horizon lines | AR retainers start 1 Sep 2024 (24 months of settled invoices for calibration); other ERP lines start 1 Oct 2025 so the receivables and payables open at 1 Jan 2026 exist in the ledger. Their December 2025 VAT is paid 16 Jan 2026 |

## Authoring convention for ledger rows (applies to every table)

- `erp:ar` / `erp:ap` rows: `date` = invoice date (accrual, drives the VAT tax point) · `amount` = **net** · `VatSpec` from the invoice line (rate, treatment, recoverable) · `settlement` = the item's calibrated terms while open (status committed); once the bank line is matched, the row carries a per-event settlement override equal to the observed offset (e.g. `95d`), so the cash leg lands on the bank date (status actual).
- `payroll:zucchetti` rows: `date` = pay date, `amount` = net pay or F24 total, VAT `out_of_scope`, settlement `0d`.
- `bank:intesa` statement lines are **match evidence**, not events. A line matched to an erp or payroll row confirms its cash date. A line matched to a generated occurrence of a no-VAT item (loan, fido commission, reimbursements, fuel-card SDD, bank charges) becomes an actual event that references the item, `date` = value date, `amount` = the line. A line matching nothing becomes an actual event with no item, tag `cat:uncovered`, VAT `out_of_scope`.
- A row dated after the cutover that references an item with segments would be counted twice (the engine generates after cutover and includes every ledger row). The import gate `date sanity` holds such rows in staging until the cutover passes them; payroll and bank figures for dates after cutover are evidence for the next match, never rows.
- `manual` rows: forecast events typed by the controller (net, VatSpec explicit). `commercialista` rows: committed tax events from the accountant's schedule (none yet — see coverage).
- A settled committed row is recorded as `void_event(committed, note)` + the actual row (`ext_id` suffixed `-S`, pilot §3.6 convention); a partial payment as `-P1` actual + `-R` committed; a credit note as its own row.

## Formatting rules used in every table

- Money: IBM Plex Mono, always 2 dp, thousands `,`, decimal `.`: `€ 18,420.00`.
- Sign: flows carry a sign, `+ € 31,720.00` (inflow) and `− € 5,856.00` (outflow, U+2212). Balances and stock figures carry no sign unless negative.
- Zero: a computed zero is `—`. A blank cell means no generator covers the period. `0.00` never appears in a money cell (asserted by the script).
- Dates: `27 Nov 2026` in tables; `27 Nov` inside a column whose header carries the year; ISO weeks Monday start, `W47 · 16 Nov–22 Nov`.
- Every engine figure carries a stamp `as-of 2026-09-28 · r51 · 7c2e19b · base · engine 1.4.0`; hypothetical figures add `WHAT-IF · downside`; figures the host computes carry `host statistic · <definition>` instead of an engine stamp.
- Percentages: 1 dp, `−11.6 %` (U+2212). `not within horizon` where the engine returns None.

## Non-engine figures (host statistics) and their definitions

| figure | definition | where |
|---|---|---|
| LOWEST POINT after cutover | min of the day-grain `cash` series over (cutover, end) | Position, compare |
| first day below floor · days below floor | first / count of days after cutover with `cash` < `min_cash` | Position, alert |
| below floor in actuals | days ≤ cutover with `cash` < `min_cash` | Position |
| headroom | derived item `headroom = it("cash") − p.min_cash` (engine row, stamped) | grid |
| available liquidity | balance + `credit_line` undrawn (host; drawn is a manual stock item, none yet) | Position |
| buffer days | balance ÷ (Σ outflows over the trailing 90 days to cutover ÷ 90), integer | Position |
| coverage % | Σ\|actual movements with an item\| ÷ Σ\|actual movements\| in the horizon to cutover | Coverage |
| bank-vs-book Δ | statement closing balance − engine `cash` at the statement date | Position, Close |
| moved / changed | per occurrence: same amount other period vs different amount (plan run vs actual run) | Variance |
| MAPE, trough errors, directional accuracy | pilot §8.2 over weekly closing balances (forecast-then vs actual) | Validation |
| calibration percentiles | nearest-rank percentiles of (observed offset − contractual days) per customer | Calibration |
| receipts / disbursements / VAT & tax / net rows | derived items `agg(tag=…)` in the book (engine rows), see grid source map | grid |

---

# Computed tables

book `delta-ingegneria` · currency EUR · horizon [2026-01-01 , 2027-07-01) · opening € 196,350.00 · cutover 20 Sep 2026 · statement to 25 Sep 2026 · as-of 28 Sep 2026 · revision r51 · 7c2e19b · engine 1.4.0

## status() — uncommitted changes

No uncommitted changes. HEAD = r51 · 7c2e19b. The header reads `r51 · 7c2e19b · saved`.

## Params (base) — the whole lever surface

| param | value | referenced by | last changed |
|---|---:|---:|---:|
| `vat_standard` | 0.22 | every VatSpec with rate `p.vat_standard` | r1 |
| `istat_index` | 0.02 | ret_acme, rent (escalation) | r1 |
| `min_cash` | € 60,000.00 | derived item `headroom`, alert rule | r12 |
| `credit_line` | € 150,000.00 | Position (available liquidity) | r12 |

Settlement offsets are literal Durations on each item (`"68d"`); a DueTerm cannot reference a param (SDK request SR-1). Calibration therefore writes `set_item` on every item of the customer, not a param. The downside fork differs from base by items only.

## Items

| id | name | dir | tags | segments | recurrence | amount (net, excl. VAT) | settlement | VAT | source |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `ret_acme` | Retainer — Acme Automation | in | cat:revenue customer:acme | 1 Sep 2024 → open (2 segments, escalation from 1 Jan 2026) | every 1 month · each month end | € 60,000.00 · escalation p.istat_index | 1.0 @ 68d next | standard | erp:ar |
| `ret_borghi` | Retainer — Borghi Impianti | in | cat:revenue customer:borghi | 1 Sep 2024 → open | every 1 month · each month end | € 26,000.00 | 1.0 @ 52d next | standard | erp:ar |
| `support_misc` | Support contracts — small customers | in | cat:revenue customer:various | 1 Sep 2024 → open | every 1 month · each month end | € 34,500.00 | 1.0 @ 40d next | standard | erp:ar |
| `ms_comune` | Milestones — Comune di Monza (PA) | in | cat:revenue customer:comune_monza | 1 Jan 2026 → open | every 1 month · each month end | schedule: 28 Feb 2026 € 75,000.00; 31 Jul 2026 € 110,000.00; 30 Nov 2026 € 110,000.00; 31 Mar 2027 € 95,000.00 | 1.0 @ 120d next | split_payment | erp:ar |
| `ms_veltro` | Milestones — Veltro Robotics | in | cat:revenue customer:veltro | 1 Jan 2026 → open | every 1 month · each month end | schedule: 31 May 2026 € 75,000.00; 30 Sep 2026 € 85,000.00; 31 Jan 2027 € 70,000.00 | 0.3 @ 0d next + 0.7 @ 60d next | standard | erp:ar |
| `pipe_nord` | Pipeline — Nord Energia framework | in | cat:revenue customer:nord_energia | 1 Nov 2026 → open | every 1 month · each month end | € 30,000.00 · probability 0.6 | 1.0 @ 40d next | standard | manual |
| `payroll` | Payroll — net salaries (25 FTE) | out | cat:payroll | 1 Jan 2026 → open | every 1 month · day 27 · bank day prev | € 52,000.00 | 1.0 @ 0d | out_of_scope | payroll:zucchetti |
| `payroll_13` | Payroll — 13th month (CCNL metalmeccanico) | out | cat:payroll | 15 Dec 2026 → open | every 1 year · day 15 · bank day prev | € 52,000.00 | 1.0 @ 0d | out_of_scope | payroll:zucchetti |
| `f24_contrib` | F24 — INPS contributions and IRPEF withholding | out | cat:contributions | 1 Jan 2026 → open | every 1 month · day 16 · bank day next | € 42,000.00 | 1.0 @ 0d | out_of_scope | payroll:zucchetti |
| `f24_contrib_13` | F24 — contributions and IRPEF on the 13th month | out | cat:contributions | 16 Jan 2026 → open | every 1 year · day 16 · bank day next | € 21,000.00 | 1.0 @ 0d | out_of_scope | payroll:zucchetti |
| `inail` | INAIL — autoliquidazione (F24) | out | cat:contributions | 16 Feb 2026 → open | every 1 year · day 16 · bank day next | € 4,300.00 | 1.0 @ 0d | out_of_scope | payroll:zucchetti |
| `rent` | Office rent — Via Zanica 12 | out | cat:opex | 1 Oct 2025 → open (2 segments, escalation from 1 Jan 2026) | every 1 month · day 1 | € 4,800.00 · escalation p.istat_index | 1.0 @ 13d next | standard | erp:ap |
| `software` | Software subscriptions (3 vendors) | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 5 | € 2,150.00 | 1.0 @ 0d next | standard | erp:ap |
| `lease_vehicles` | Vehicle leases (4 cars, 40 % recoverable) | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 10 | € 1,900.00 | 1.0 @ 0d next | standard · recoverable 0.4 | erp:ap |
| `utilities` | Utilities — electricity, gas, water | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 20 | € 1,650.00 | 1.0 @ 15d next | standard | erp:ap |
| `telco` | Telco — mobile and fibre (50 % recoverable) | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 24 | € 1,250.00 | 1.0 @ 0d next | standard · recoverable 0.5 | erp:ap |
| `buoni_pasto` | Buoni pasto (VAT 4 %) | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 23 | € 3,150.00 | 1.0 @ 0d next | standard 0.04 | erp:ap |
| `prof_fees` | Professional fees — commercialista, consulente del lavoro | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 10 | € 2,900.00 | 1.0 @ 30d next | standard | erp:ap |
| `fuel_tolls` | Fuel cards and tolls (40 % recoverable) | out | cat:opex | 1 Oct 2025 → open | every 1 month · day 22 | € 2,400.00 | 1.0 @ 0d next | standard · recoverable 0.4 | bank:intesa |
| `expense_reimb` | Expense reimbursements — engineers on site | out | cat:opex | 1 Jan 2026 → open | every 1 month · day 27 · bank day prev | € 3,600.00 | 1.0 @ 0d | out_of_scope | bank:intesa |
| `bank_charges` | Bank charges — Intesa account fees | out | cat:opex | 1 Sep 2026 → open | every 1 month · day 1 · bank day next | € 48.00 | 1.0 @ 0d | exempt | bank:intesa |
| `cciaa` | Diritto annuale CCIAA | out | cat:opex | 30 Jun 2026 → open | every 1 year · day 30 · bank day prev | € 220.00 | 1.0 @ 0d | out_of_scope | bank:intesa |
| `insurance` | Insurance — RC professionale and property | out | cat:insurance | 1 Apr 2026 → open | every 1 year · day 1 · bank day next | € 7,200.00 | 1.0 @ 0d | exempt | erp:ap |
| `subcontract` | Subcontractors — engineering firms (2) | out | cat:subcontract | 1 Oct 2025 → open | every 1 month · each month end | € 8,000.00 | 1.0 @ 30d next | standard | erp:ap |
| `freelance` | Freelancers — ritenuta 20 % (3) | out | cat:subcontract | 1 Oct 2025 → open | every 1 month · each month end | € 6,000.00 | 1.0 @ 30d next · withholding 0.20 | standard | erp:ap |
| `loan_intesa` | Term loan — Intesa (instalment) | out | cat:financing | 1 Jan 2026 → open | every 1 month · day 15 · bank day next | € 6,250.00 | 1.0 @ 0d | out_of_scope | bank:intesa |
| `fido_commission` | Fido di cassa — availability commission (0.25 %/quarter) | out | cat:financing | 1 Jan 2026 → open | every 3 month · day 1 · bank day next | € 375.00 | 1.0 @ 0d | exempt | bank:intesa |

Derived items defined in the book through the SDK (read-only in the UI, `defined in SDK` stamp), so every frame row is an engine row with a stamp:

| id | formula | grid row |
|---|---|---|
| `sub_receipts` | `agg(tag="cat:revenue")` | Receipts |
| `sub_disbursements` | `agg(tag="cat:payroll") + agg(tag="cat:opex") + agg(tag="cat:subcontract") + agg(tag="cat:financing") + agg(tag="cat:insurance") + agg(tag="cat:capex") + agg(tag="cat:uncovered")` | Disbursements |
| `sub_vat_tax` | `agg(tag="cat:contributions") + agg(tag="cat:tax")` + the `_tax:iva:liability` frame row (whether `it("_tax:iva:liability")` is referenceable from a formula is SDK request SR-3) | VAT & tax |
| `net_flow` | `sub_receipts + sub_disbursements + sub_vat_tax` | Net |
| `cash` | the engine's balance fold (`balance_source`) | Opening / Closing |
| `headroom` | `it("cash") − p.min_cash` | Headroom |

## VAT regime `iva` — monthly, accrual tax point, `payment_offset 16d`, no surcharge, credit carried

| month | output VAT | input VAT (recoverable) | credit carried in | net | payment date | paid |
|---|---:|---:|---:|---:|---:|---:|
| Dec 2025 | € 26,510.00 | € 6,251.90 | — | € 20,258.10 | 16 Jan 2026 | € 20,258.10 |
| Jan 2026 | € 26,510.00 | € 6,251.90 | — | € 20,258.10 | 16 Feb 2026 | € 20,258.10 |
| Feb 2026 | € 26,510.00 | € 6,251.90 | — | € 20,258.10 | 16 Mar 2026 | € 20,258.10 |
| Mar 2026 | € 26,510.00 | € 6,251.90 | — | € 20,258.10 | 16 Apr 2026 | € 20,258.10 |
| Apr 2026 | € 26,510.00 | € 6,251.90 | — | € 20,258.10 | 16 May 2026 (Sat; no adjust in the engine) | € 20,258.10 |
| May 2026 | € 43,010.00 | € 6,251.90 | — | € 36,758.10 | 16 Jun 2026 | € 36,758.10 |
| Jun 2026 | € 26,510.00 | € 6,515.90 | — | € 19,994.10 | 16 Jul 2026 | € 19,994.10 |
| Jul 2026 | € 26,510.00 | € 6,581.90 | — | € 19,928.10 | 16 Aug 2026 (Sun; no adjust in the engine) | € 19,928.10 |
| Aug 2026 | € 26,510.00 | € 6,414.70 | — | € 20,095.30 | 16 Sep 2026 | € 20,095.30 |
| Sep 2026 | € 45,210.00 | € 6,306.90 | — | € 38,903.10 | 16 Oct 2026 | € 38,903.10 |
| Oct 2026 | € 26,510.00 | € 11,201.90 | — | € 15,308.10 | 16 Nov 2026 | € 15,308.10 |
| Nov 2026 | € 30,470.00 | € 6,251.90 | — | € 24,218.10 | 16 Dec 2026 | € 24,218.10 |
| Dec 2026 | € 30,470.00 | € 6,251.90 | — | € 24,218.10 | 16 Jan 2027 (Sat; no adjust in the engine) | € 24,218.10 |
| Jan 2027 | € 46,134.00 | € 6,273.02 | — | € 39,860.98 | 16 Feb 2027 | € 39,860.98 |
| Feb 2027 | € 30,734.00 | € 6,273.02 | — | € 24,460.98 | 16 Mar 2027 | € 24,460.98 |
| Mar 2027 | € 30,734.00 | € 6,273.02 | — | € 24,460.98 | 16 Apr 2027 | € 24,460.98 |
| Apr 2027 | € 30,734.00 | € 6,273.02 | — | € 24,460.98 | 16 May 2027 (Sun; no adjust in the engine) | € 24,460.98 |
| May 2027 | € 30,734.00 | € 6,273.02 | — | € 24,460.98 | 16 Jun 2027 | € 24,460.98 |
| Jun 2027 | € 30,734.00 | € 6,273.02 | — | € 24,460.98 | 16 Jul 2027 · **beyond horizon end — in no frame** | € 24,460.98 |

The VAT credit, when it exists, is the stock `_tax:iva:credit` (aggregation `last`, no sum row). It is never an inflow.

## Tax calendar — the F24 of the 16th, by section (Oct 2026 → Jun 2027)

| F24 month | Erario IVA · date | Erario IVA | INPS + IRPEF (+ INAIL Feb) · date | INPS + IRPEF | Erario ritenute | F24 total |
|---|---:|---:|---:|---:|---:|---:|
| Oct 2026 | 16 Oct 2026 | − € 38,903.10 | 16 Oct 2026 | − € 42,000.00 | NOT MODELLED (CK-W004) | − € 80,903.10 |
| Nov 2026 | 16 Nov 2026 | − € 15,308.10 | 16 Nov 2026 | − € 42,000.00 | NOT MODELLED (CK-W004) | − € 57,308.10 |
| Dec 2026 | 16 Dec 2026 | − € 24,218.10 | 16 Dec 2026 | − € 42,000.00 | NOT MODELLED (CK-W004) | − € 66,218.10 |
| Jan 2027 | 16 Jan 2027 (Sat — engine, no adjust) | − € 24,218.10 | 18 Jan 2027 | − € 63,000.00 | NOT MODELLED (CK-W004) | two dates — see note |
| Feb 2027 | 16 Feb 2027 | − € 39,860.98 | 16 Feb 2027 | − € 46,300.00 | NOT MODELLED (CK-W004) | − € 86,160.98 |
| Mar 2027 | 16 Mar 2027 | − € 24,460.98 | 16 Mar 2027 | − € 42,000.00 | NOT MODELLED (CK-W004) | − € 66,460.98 |
| Apr 2027 | 16 Apr 2027 | − € 24,460.98 | 16 Apr 2027 | − € 42,000.00 | NOT MODELLED (CK-W004) | − € 66,460.98 |
| May 2027 | 16 May 2027 (Sun — engine, no adjust) | − € 24,460.98 | 17 May 2027 | − € 42,000.00 | NOT MODELLED (CK-W004) | two dates — see note |
| Jun 2027 | 16 Jun 2027 | − € 24,460.98 | 16 Jun 2027 | − € 42,000.00 | NOT MODELLED (CK-W004) | − € 66,460.98 |

Where the 16th is a weekend the controller pays one F24 on the Monday; the engine dates the IVA section on the Saturday and the contributions (item `f24_contrib`, `bank day next`) on the Monday. The two dates are shown, not merged. The ritenute section is € 1,200.00/month (20 % of the freelance net — host arithmetic shown in the coverage checklist, never in the diagnostic row) and is not modelled.

## Monthly series — cash and accrual measures, month-end closing balance, day-grain minimum

| month | status | accrual (net) | cash in | cash out | cash net | closing · base | day-grain min (◆ = below floor inside the month) | closing · downside (WHAT-IF) | Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Jan 2026 | actual | − € 38,925.00 | € 147,010.00 | − € 185,488.10 | − € 38,478.10 | € 157,871.90 | € 157,871.90 · 30 Jan | = base | — |
| Feb 2026 | actual | + € 53,150.00 | € 147,010.00 | − € 152,533.10 | − € 5,523.10 | € 152,348.80 | € 147,331.90 · 10 Feb | = base | — |
| Mar 2026 | actual | − € 17,550.00 | € 147,010.00 | − € 201,493.10 | − € 54,483.10 | € 97,865.70 | € 82,025.70 · 27 Mar | = base | — |
| Apr 2026 | actual | − € 25,125.00 | € 147,010.00 | − € 171,688.10 | − € 24,678.10 | € 73,187.60 | € 73,187.60 · 30 Apr | = base | — |
| May 2026 | actual | + € 57,450.00 | € 147,010.00 | − € 166,233.10 | − € 19,223.10 | € 53,964.50 | € 53,964.50 · 27 May | = base | — |
| Jun 2026 | actual | − € 18,970.00 | € 249,460.00 | − € 219,313.10 | + € 30,146.90 | € 84,111.40 | € 60,850.50 · 5 Jun | = base | — |
| Jul 2026 | actual | + € 90,575.00 | € 212,300.00 | − € 165,448.10 | + € 46,851.90 | € 130,963.30 | ◆ € 52,297.30 · 27 Jul | = base | — |
| Aug 2026 | actual | − € 18,290.00 | € 73,810.00 | − € 165,918.10 | − € 92,108.10 | € 38,855.20 | € 38,855.20 · 31 Aug | = base | — |
| Sep 2026 | open | + € 66,772.00 | € 251,320.00 | − € 165,233.10 | + € 86,086.90 | € 124,942.10 | ◆ € 38,807.20 · 1 Sep | € 46,465.60 | − € 78,476.50 |
| Oct 2026 | forecast | − € 40,473.00 | € 147,010.00 | − € 210,583.10 | − € 63,573.10 | € 61,369.00 | € 61,369.00 · 30 Oct | − € 83,526.00 | − € 144,895.00 |
| Nov 2026 | forecast | + € 110,402.00 | € 329,600.00 | − € 159,163.10 | + € 170,436.90 | € 231,805.90 | ◆ € 56,685.00 · 5 Nov | − € 30,001.10 | − € 261,807.00 |
| Dec 2026 | forecast | − € 51,598.00 | € 147,010.00 | − € 220,073.10 | − € 73,063.10 | € 158,742.80 | € 158,742.80 · 30 Dec | € 3,800.80 | − € 154,942.00 |
| Jan 2027 | forecast | + € 50,131.00 | € 168,970.00 | − € 173,685.22 | − € 4,715.22 | € 154,027.58 | € 153,683.80 · 5 Jan | − € 36,989.42 | − € 191,017.00 |
| Feb 2027 | forecast | − € 2,794.00 | € 194,590.00 | − € 188,133.10 | + € 6,456.90 | € 160,484.48 | € 159,083.58 · 5 Feb | − € 67,876.52 | − € 228,361.00 |
| Mar 2027 | forecast | + € 96,506.00 | € 278,970.00 | − € 184,313.10 | + € 94,656.90 | € 255,141.38 | € 139,920.48 · 8 Mar | − € 119,030.62 | − € 374,172.00 |
| Apr 2027 | forecast | − € 6,069.00 | € 230,214.00 | − € 176,008.10 | + € 54,205.90 | € 309,347.28 | € 302,662.38 · 5 Apr | − € 17,566.72 | − € 326,914.00 |
| May 2027 | forecast | + € 1,506.00 | € 170,434.00 | − € 168,433.10 | + € 2,000.90 | € 311,348.18 | € 304,663.28 · 5 May | − € 52,840.82 | − € 364,189.00 |
| Jun 2027 | forecast | + € 1,286.00 | € 170,434.00 | − € 168,653.10 | + € 1,780.90 | € 313,129.08 | € 309,287.18 · 4 Jun | − € 88,334.92 | − € 401,464.00 |

◆ marks a month whose closing is at or above `min_cash` while a day inside it is below: the aggregated column must show the crossing (UI-05). Accrual is the net amount recognised on the invoice date; cash is what moved. They are separate measures on every run and never mixed in one column.

## 13-week series — weeks from the day after cutover, closing balance on Sunday

| # | week | inflows | outflows | net | closing · base | day-grain min | closing · downside (WHAT-IF) | Δ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | W39 · 21 Sep–27 Sep | € 31,720.00 | − € 63,329.00 | − € 31,609.00 | € 109,712.10 | € 109,712.10 · 25 Sep | € 35,902.10 | − € 73,810.00 |
| 2 | W40 · 28 Sep–4 Oct | € 31,110.00 | − € 16,303.00 | + € 14,807.00 | € 124,519.10 | € 109,712.10 · 28 Sep | € 46,042.60 | − € 78,476.50 |
| 3 | W41 · 5 Oct–11 Oct | € 73,200.00 | − € 4,636.00 | + € 68,564.00 | € 193,083.10 | € 119,883.10 · 5 Oct | € 83,496.60 | − € 109,586.50 |
| 4 | W42 · 12 Oct–18 Oct | € 42,090.00 | − € 126,315.10 | − € 84,225.10 | € 108,858.00 | € 108,858.00 · 16 Oct | − € 36,037.00 | − € 144,895.00 |
| 5 | W43 · 19 Oct–25 Oct | € 31,720.00 | − € 6,204.00 | + € 25,516.00 | € 134,374.00 | € 108,858.00 · 19 Oct | − € 10,521.00 | − € 144,895.00 |
| 6 | W44 · 26 Oct–1 Nov | — | − € 73,005.00 | − € 73,005.00 | € 61,369.00 | € 61,369.00 · 30 Oct | − € 83,526.00 | − € 144,895.00 |
| 7 | W45 · 2 Nov–8 Nov | — | − € 4,684.00 | − € 4,684.00 | € 56,685.00 | € 56,685.00 · 5 Nov | − € 15,010.00 | − € 71,695.00 |
| 8 | W46 · 9 Nov–15 Nov | € 115,290.00 | − € 5,856.00 | + € 109,434.00 | € 166,119.00 | € 166,119.00 · 10 Nov | € 21,224.00 | − € 144,895.00 |
| 9 | W47 · 16 Nov–22 Nov | — | − € 69,414.10 | − € 69,414.10 | € 96,704.90 | € 96,704.90 · 16 Nov | − € 44,213.60 | − € 140,918.50 |
| 10 | W48 · 23 Nov–29 Nov | € 31,720.00 | − € 63,329.00 | − € 31,609.00 | € 65,095.90 | € 65,095.90 · 27 Nov | − € 75,822.60 | − € 140,918.50 |
| 11 | W49 · 30 Nov–6 Dec | € 182,590.00 | − € 15,928.00 | + € 166,662.00 | € 231,757.90 | € 231,757.90 · 1 Dec | − € 30,049.10 | − € 261,807.00 |
| 12 | W50 · 7 Dec–13 Dec | € 115,290.00 | − € 10,492.00 | + € 104,798.00 | € 336,555.90 | € 300,321.90 · 7 Dec | € 68,435.40 | − € 268,120.50 |
| 13 | W51 · 14 Dec–20 Dec | — | − € 130,324.10 | − € 130,324.10 | € 206,231.80 | € 206,231.80 · 16 Dec | − € 53,952.20 | − € 260,184.00 |

## 13-week grid — base, cash measure; receipts by customer, disbursements by category

| row · source | W39<br>21 Sep–27 Sep | W40<br>28 Sep–4 Oct | W41<br>5 Oct–11 Oct | W42<br>12 Oct–18 Oct | W43<br>19 Oct–25 Oct | W44<br>26 Oct–1 Nov | W45<br>2 Nov–8 Nov | W46<br>9 Nov–15 Nov | W47<br>16 Nov–22 Nov | W48<br>23 Nov–29 Nov | W49<br>30 Nov–6 Dec | W50<br>7 Dec–13 Dec | W51<br>14 Dec–20 Dec |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **Opening balance** · `cash` | € 141,321.10 | € 109,712.10 | € 124,519.10 | € 193,083.10 | € 108,858.00 | € 134,374.00 | € 61,369.00 | € 56,685.00 | € 166,119.00 | € 96,704.90 | € 65,095.90 | € 231,757.90 | € 336,555.90 |
| customer:acme · Acme Automation · `pivot(columns=tag:customer)` | — | — | + € 73,200.00 | — | — | — | — | + € 73,200.00 | — | — | — | + € 73,200.00 | — |
| customer:borghi · Borghi Impianti · `pivot(columns=tag:customer)` | + € 31,720.00 | — | — | — | + € 31,720.00 | — | — | — | — | + € 31,720.00 | — | — | — |
| customer:various · Small customers · `pivot(columns=tag:customer)` | — | — | — | + € 42,090.00 | — | — | — | + € 42,090.00 | — | — | — | + € 42,090.00 | — |
| customer:comune_monza · Comune di Monza (PA) · `pivot(columns=tag:customer)` | — | — | — | — | — | — | — | — | — | — | + € 110,000.00 | — | — |
| customer:veltro · Veltro Robotics · `pivot(columns=tag:customer)` | — | + € 31,110.00 | — | — | — | — | — | — | — | — | + € 72,590.00 | — | — |
| customer:nord_energia · Nord Energia (p 0.6) · `pivot(columns=tag:customer)` | — | — | — | — | — | — | — | — | — | — | — | — | — |
| **Receipts** · `sub_receipts` | + € 31,720.00 | + € 31,110.00 | + € 73,200.00 | + € 42,090.00 | + € 31,720.00 | — | — | + € 115,290.00 | — | + € 31,720.00 | + € 182,590.00 | + € 115,290.00 | — |
| cat:payroll | − € 52,000.00 | — | — | — | — | − € 52,000.00 | — | — | — | − € 52,000.00 | — | — | − € 52,000.00 |
| cat:opex | − € 11,329.00 | − € 48.00 | − € 4,636.00 | − € 11,712.00 | − € 6,204.00 | − € 5,125.00 | − € 4,684.00 | − € 5,856.00 | − € 5,856.00 | − € 11,329.00 | − € 48.00 | − € 10,492.00 | − € 5,856.00 |
| cat:subcontract | — | − € 15,880.00 | — | — | — | − € 15,880.00 | — | — | — | — | − € 15,880.00 | — | — |
| cat:financing | — | − € 375.00 | — | − € 6,250.00 | — | — | — | — | − € 6,250.00 | — | — | — | − € 6,250.00 |
| cat:insurance | — | — | — | — | — | — | — | — | — | — | — | — | — |
| cat:capex | — | — | — | − € 27,450.00 | — | — | — | — | — | — | — | — | — |
| cat:uncovered | — | — | — | — | — | — | — | — | — | — | — | — | — |
| **Disbursements** · `sub_disbursements` | − € 63,329.00 | − € 16,303.00 | − € 4,636.00 | − € 45,412.00 | − € 6,204.00 | − € 73,005.00 | − € 4,684.00 | − € 5,856.00 | − € 12,106.00 | − € 63,329.00 | − € 15,928.00 | − € 10,492.00 | − € 64,106.00 |
| cat:contributions · f24_contrib, inail | — | — | — | − € 42,000.00 | — | — | — | — | − € 42,000.00 | — | — | — | − € 42,000.00 |
| cat:tax · `_tax:iva:liability` | — | — | — | − € 38,903.10 | — | — | — | — | − € 15,308.10 | — | — | — | − € 24,218.10 |
| **VAT & tax** · `sub_vat_tax` | — | — | — | − € 80,903.10 | — | — | — | — | − € 57,308.10 | — | — | — | − € 66,218.10 |
| **Net** · `net_flow` | − € 31,609.00 | + € 14,807.00 | + € 68,564.00 | − € 84,225.10 | + € 25,516.00 | − € 73,005.00 | − € 4,684.00 | + € 109,434.00 | − € 69,414.10 | − € 31,609.00 | + € 166,662.00 | + € 104,798.00 | − € 130,324.10 |
| **Closing balance** · `cash` | € 109,712.10 | € 124,519.10 | € 193,083.10 | € 108,858.00 | € 134,374.00 | € 61,369.00 | € 56,685.00 | € 166,119.00 | € 96,704.90 | € 65,095.90 | € 231,757.90 | € 336,555.90 | € 206,231.80 |
| **Headroom** · `headroom` | + € 49,712.10 | + € 64,519.10 | + € 133,083.10 | + € 48,858.00 | + € 74,374.00 | + € 1,369.00 | − € 3,315.00 | + € 106,119.00 | + € 36,704.90 | + € 5,095.90 | + € 171,757.90 | + € 276,555.90 | + € 146,231.80 |
| day-grain min · ◆ crossing inside the week | € 109,712.10<br>25 Sep | € 109,712.10<br>28 Sep | € 119,883.10<br>5 Oct | € 108,858.00<br>16 Oct | € 108,858.00<br>19 Oct | € 61,369.00<br>30 Oct | € 56,685.00<br>5 Nov | € 166,119.00<br>10 Nov | € 96,704.90<br>16 Nov | € 65,095.90<br>27 Nov | € 231,757.90<br>1 Dec | € 300,321.90<br>7 Dec | € 206,231.80<br>16 Dec |

Source map: customer rows come from `pivot(index=period, columns=tag:customer, values=cash)`; `cat:` rows from `frame(where=cat:<x>)`; the bold rows are the derived items listed under Items; Opening and Closing are the `cash` item. Nothing is summed in the client.

_Self-check: every week and month satisfies opening + inflows + outflows = closing; customer and category rows sum to the net row._

## Committed-only closing line — 13 weeks (host run on a throwaway overlay with every generative segment stripped; the engine folds it)

| week | closing · base (all statuses) | closing · committed only | Δ |
|---|---:|---:|---:|
| W39 · 21 Sep–27 Sep | € 109,712.10 | € 173,041.10 | + € 63,329.00 |
| W40 · 28 Sep–4 Oct | € 124,519.10 | € 157,161.10 | + € 32,642.00 |
| W41 · 5 Oct–11 Oct | € 193,083.10 | € 228,348.10 | + € 35,265.00 |
| W42 · 12 Oct–18 Oct | € 108,858.00 | € 266,900.10 | + € 158,042.10 |
| W43 · 19 Oct–25 Oct | € 134,374.00 | € 298,620.10 | + € 164,246.10 |
| W44 · 26 Oct–1 Nov | € 61,369.00 | € 298,620.10 | + € 237,251.10 |
| W45 · 2 Nov–8 Nov | € 56,685.00 | € 298,620.10 | + € 241,935.10 |
| W46 · 9 Nov–15 Nov | € 166,119.00 | € 371,820.10 | + € 205,701.10 |
| W47 · 16 Nov–22 Nov | € 96,704.90 | € 371,820.10 | + € 275,115.20 |
| W48 · 23 Nov–29 Nov | € 65,095.90 | € 371,820.10 | + € 306,724.20 |
| W49 · 30 Nov–6 Dec | € 231,757.90 | € 481,820.10 | + € 250,062.20 |
| W50 · 7 Dec–13 Dec | € 336,555.90 | € 481,820.10 | + € 145,264.20 |
| W51 · 14 Dec–20 Dec | € 206,231.80 | € 481,820.10 | + € 275,588.30 |

Stamp on the committed-only line: `PREVIEW · committed only · r51 · engine 1.4.0`. It is the floor of certainty: only actual and committed rows, no generated occurrence, no forecast event.

## summary() and the Position figures

| scenario | book balance at cutover 20 Sep | book balance at 25 Sep (engine fold) | horizon close 30 Jun 2027 | min_cash · min_cash_period (whole horizon) | total_inflow | total_outflow | runway_end (first negative) |
|---|---:|---:|---:|---:|---:|---:|---:|
| base | € 141,321.10 | € 109,712.10 | € 313,129.08 | € 38,807.20 on 1 Sep 2026 | € 3,359,172.00 | − € 3,242,392.92 | not within horizon |
| downside (WHAT-IF) | € 99,231.10 | € 35,902.10 | − € 88,334.92 | − € 119,030.62 on 30 Mar 2027 | € 2,887,764.50 | − € 3,172,449.42 | 16 Oct 2026 |

The columns above are `RunSummary` fields (`opening_balance`, `closing_balance`, `min_cash`, `min_cash_period`, `total_inflow`, `total_outflow`, `runway_end`). The desktop adds these host statistics, each labelled as such on screen:

| scenario | LOWEST POINT after cutover | first day below min_cash | days below floor | back above the floor | below floor in actuals |
|---|---:|---:|---:|---:|---:|
| base | € 56,685.00 on 5 Nov 2026 | 4 Nov 2026 | 5 | 9 Nov 2026 | 1 Sep 2026 (€ 38,807.20), 13 day(s) |
| downside (WHAT-IF) | − € 119,030.62 on 30 Mar 2027 | 25 Sep 2026 | 249 | 9 Oct 2026 | 1 Sep 2026 (€ 38,807.20), 13 day(s) |

### Bank-vs-book tie-out at the statement date (Position, top of Close)

| figure | value | stamp |
|---|---:|---|
| bank statement closing balance 25 Sep 2026 | € 114,592.10 | fact · bank:intesa · 19 lines imported 25 Sep 18:30 |
| book balance 25 Sep 2026 | € 109,712.10 | as-of 2026-09-28 · r51 · 7c2e19b · base · engine 1.4.0 · cash |
| Δ bank − book | + € 4,880.00 | host statistic · explained by 1 unmatched line(s), see Close |
| book balance at cutover 20 Sep 2026 | € 141,321.10 | as-of 2026-09-28 · r51 · 7c2e19b · base · engine 1.4.0 · cash · includes 1 committed leg(s) dated on/before cutover and not received: support_misc + € 42,090.00 expected 9 Sep |
| bank balance at cutover 20 Sep 2026 | € 99,231.10 | fact · bank:intesa |

### Liquidity and buffer (host statistics)

- available liquidity at the statement date 25 Sep 2026: bank balance € 114,592.10 (fact) + credit line € 150,000.00 undrawn = € 264,592.10; headroom vs floor + € 54,592.10
- buffer days: trailing-90-day outflows (23 Jun–20 Sep) € 516,491.30 → € 5,738.79/day → the bank balance covers 19 days of outflow; with the credit line 46 days

## Next 10 bank days from as-of 28 Sep 2026 — running balance (Position)

| bank day | status | item | flow | running balance |
|---|---:|---:|---:|---:|
| 28 Sep 2026 Mon | — | — | — | € 109,712.10 |
| 29 Sep 2026 Tue | — | — | — | € 109,712.10 |
| 30 Sep 2026 Wed | C | freelance | − € 6,120.00 | € 103,592.10 |
| 30 Sep 2026 Wed | G | ms_veltro | + € 31,110.00 | € 134,702.10 |
| 30 Sep 2026 Wed | C | subcontract | − € 9,760.00 | € 124,942.10 |
| 1 Oct 2026 Thu | G | bank_charges | − € 48.00 | € 124,894.10 |
| 1 Oct 2026 Thu | G | fido_commission | − € 375.00 | € 124,519.10 |
| 2 Oct 2026 Fri | — | — | — | € 124,519.10 |
| 5 Oct 2026 Mon | G | software | − € 2,623.00 | € 121,896.10 |
| 5 Oct 2026 Mon | C | utilities | − € 2,013.00 | € 119,883.10 |
| 6 Oct 2026 Tue | — | — | — | € 119,883.10 |
| 7 Oct 2026 Wed | C | ret_acme | + € 73,200.00 | € 193,083.10 |
| 8 Oct 2026 Thu | — | — | — | € 193,083.10 |
| 9 Oct 2026 Fri | — | — | — | € 193,083.10 |

Opening of the table = book balance on 27 Sep 2026 (€ 109,712.10). C = committed row, G = generated occurrence, F = forecast event. Committed and generated are shown as separate glyphs; the running balance is the engine's `cash` series at day grain.

## Imports and sources (source inventory; status strip reads it)

| source | owner | method | cadence | last run | rows (last run) | stale |
|---|---:|---:|---:|---:|---:|---:|
| erp:ar | M. Conti | CSV export (TeamSystem) | each bank day 07:10 | 28 Sep 2026 07:10 | 7 rows · inserted 0 · skipped 7 · conflicted 0 | no |
| erp:ap | M. Conti | CSV export (TeamSystem) | each bank day 07:10 | 28 Sep 2026 07:10 | 10 rows · inserted 0 · skipped 10 · conflicted 0 | no |
| bank:intesa | M. Conti | statement CSV (CBI) | each bank day | 25 Sep 2026 18:30 | 19 lines staged (evidence) · matched 0 of 19 | no (no bank day since) |
| payroll:zucchetti | consulente del lavoro | monthly file | monthly, by the 24th | 24 Sep 2026 15:02 | 2 figures staged as evidence (net payroll 25 Sep, F24 16 Oct); rows are written at match time | no |
| manual | M. Conti | typed | — | 18 Sep 2026 (ev-0840) | 1 forecast event | — |
| commercialista | Studio Bassi | schedule by email | quarterly | — | **not covered** — IRES/IRAP acconti not in the book | n/a |
| backlog (ERP sales) | — | — | — | — | **not covered** — milestones are authored as items | n/a |

A source is stale when as-of − last run exceeds its cadence; the strip names it ('payroll:zucchetti 34 days old, cadence monthly'), never colours it.

## Import gate report — erp:ar, 28 Sep 2026 07:10 (D09)

| gate | check | result | Δ |
|---|---:|---:|---:|
| Completeness | 7 rows · Σ net = ageing report Σ | pass | — |
| AR tie-out | Σ open AR gross € 404,020.00 vs AR control account € 404,020.00 | pass | — |
| VAT code coverage | 7 of 7 codes mapped (22, N6.9) | pass | — |
| Terms coverage | 7 of 7 customers with mapped terms | pass | — |
| Date sanity | no invoice date after 28 Sep 2026, none before 1 Sep 2024; no row dated after cutover 20 Sep 2026 references a generative item (the engine would generate the same flow: PRD D6) | pass | — |
| Currency | 7 of 7 EUR | pass | — |
| Duplicate ext_id | 0 within the batch | pass | — |
| Sign convention | 7 of 7 positive | pass | — |

ImportReport: inserted 0 · skipped 7 (identical payload) · conflicted 0. Target: base ledger (events are book-level, shared by every scenario). A failed gate (result `stop`) leaves the ledger untouched and the screen says so. An unmapped VAT code renders as the app-layer diagnostic `CK-E903 · VAT code ‹code› has no mapping` (D-MLP-19 band: needs something the book does not have) with a fix button opening the mapping row (D20).

## VAT code mapping (Book › Settings, D20)

| ERP code | rate | treatment | recoverable | used by |
|---|---:|---:|---:|---:|
| 22 | p.vat_standard (0.22) | standard | 1.00 | retainers, subcontract, freelance, rent, software, utilities, professional fees |
| 22A40 | p.vat_standard | standard | 0.40 | lease_vehicles, fuel_tolls |
| 22T50 | p.vat_standard | standard | 0.50 | telco |
| 04 | 0.04 (literal) | standard | 1.00 | buoni_pasto |
| N6.9 | p.vat_standard | split_payment | — | ms_comune (PA: VAT never collected) |
| N4 | — | exempt | — | insurance, bank charges, fido commission |
| N2.2 | — | out_of_scope | — | payroll, F24, loan, reimbursements |

## Reconcile window (20 Sep, 25 Sep] — statement lines vs the book (D11)

| date | book row | status | book amount | statement line(s) | match | write on confirm |
|---|---:|---:|---:|---:|---:|---:|
| 9 Sep 2026 | support_misc | C | + € 42,090.00 | 1 line(s) · 24 Sep 2026 · Σ + € 42,090.00 | proposed (amount =, date moved +15 d; annotated 'not received' at r49) | committed row → void_event + actual row (`-S`) |
| 21 Sep 2026 | ret_borghi | C | + € 31,720.00 | 1 line(s) · 21 Sep 2026 · Σ + € 31,720.00 | proposed (amount =, date =) | committed row → void_event + actual row (`-S`) |
| 22 Sep 2026 | fuel_tolls | G | − € 2,928.00 | 4 line(s) · 22 Sep 2026 · Σ − € 2,928.00 | proposed (amount =, date =) | generated occurrence → add_event (actual, item ref) |
| 23 Sep 2026 | buoni_pasto | G | − € 3,276.00 | 1 line(s) · 23 Sep 2026 · Σ − € 3,276.00 | proposed (amount =, date =) | generated occurrence → add_event (actual, item ref) |
| 24 Sep 2026 | telco | G | − € 1,525.00 | 2 line(s) · 24 Sep 2026 · Σ − € 1,525.00 | proposed (amount =, date =) | generated occurrence → add_event (actual, item ref) |
| 25 Sep 2026 | expense_reimb | G | − € 3,600.00 | 8 line(s) · 25 Sep 2026 · Σ − € 3,600.00 | proposed (amount =, date =) | generated occurrence → add_event (actual, item ref) |
| 25 Sep 2026 | payroll | G | − € 52,000.00 | 1 line(s) · 25 Sep 2026 · Σ − € 52,000.00 | proposed (amount =, date =) | generated occurrence → add_event (actual, item ref) |
| 25 Sep 2026 | — | — | — | 1 line · + € 4,880.00 · Bonifico Studio Rossi — saldo consulenza | **exception** | no committed row, no item → annotate or add item |

Batch card: **Confirm 7 matches (18 statement lines → 7 book rows)**, 1:1 and N:1. Exceptions: 1 — each needs a match, a correction (D08) or an annotation before `Set cutover to 25 Sep 2026` enables (host check). Then `set_cutover(2026-09-25, note)` through the card; Save is the separate act (commit as r52).

Bank statement balance 25 Sep 2026 € 114,592.10 · book € 109,712.10 · Δ + € 4,880.00 = the exception line.

## Reproduction of the past — last 26 weeks to cutover (Validation, D23)

| week | modelled closing (items + erp/payroll rows) | bank closing (statement) | Δ | Δ % of balance | new discrepancy this week | % of weekly turnover (▲ > 2 %) | explanation (uncovered rows) |
|---|---:|---:|---:|---:|---:|---:|---:|
| W13 · 23 Mar–29 Mar | € 82,169.70 | € 82,025.70 | + € 144.00 | 0.2 % | + € 144.00 | 0.2 % | — |
| W14 · 30 Mar–5 Apr | € 90,434.70 | € 90,242.70 | + € 192.00 | 0.2 % | + € 48.00 | 0.1 % | Bank charges (item added 1 Sep, r48) − € 48.00 |
| W15 · 6 Apr–12 Apr | € 195,232.70 | € 195,040.70 | + € 192.00 | 0.1 % | — | — | — |
| W16 · 13 Apr–19 Apr | € 120,868.60 | € 120,676.60 | + € 192.00 | 0.2 % | — | — | — |
| W17 · 20 Apr–26 Apr | € 144,859.60 | € 144,667.60 | + € 192.00 | 0.1 % | — | — | — |
| W18 · 27 Apr–3 May | € 73,379.60 | € 73,139.60 | + € 240.00 | 0.3 % | + € 48.00 | 0.1 % | Bank charges (item added 1 Sep, r48) − € 48.00 |
| W19 · 4 May–10 May | € 141,943.60 | € 141,703.60 | + € 240.00 | 0.2 % | — | — | — |
| W20 · 11 May–17 May | € 127,813.50 | € 127,573.50 | + € 240.00 | 0.2 % | — | — | — |
| W21 · 18 May–24 May | € 82,885.50 | € 82,645.50 | + € 240.00 | 0.3 % | — | — | — |
| W22 · 25 May–31 May | € 54,204.50 | € 53,964.50 | + € 240.00 | 0.4 % | — | — | — |
| W23 · 1 Jun–7 Jun | € 61,138.50 | € 60,850.50 | + € 288.00 | 0.5 % | + € 48.00 | 0.1 % | Bank charges (item added 1 Sep, r48) − € 48.00 |
| W24 · 8 Jun–14 Jun | € 170,572.50 | € 170,284.50 | + € 288.00 | 0.2 % | — | — | — |
| W25 · 15 Jun–21 Jun | € 79,708.40 | € 79,420.40 | + € 288.00 | 0.4 % | — | — | — |
| W26 · 22 Jun–28 Jun | € 123,099.40 | € 122,811.40 | + € 288.00 | 0.2 % | — | — | — |
| W27 · 29 Jun–5 Jul | € 84,024.40 | € 83,688.40 | + € 336.00 | 0.4 % | + € 48.00 | 0.1 % | Bank charges (item added 1 Sep, r48) − € 48.00 |
| W28 · 6 Jul–12 Jul | € 188,822.40 | € 188,486.40 | + € 336.00 | 0.2 % | — | — | — |
| W29 · 13 Jul–19 Jul | € 114,722.30 | € 114,386.30 | + € 336.00 | 0.3 % | — | — | — |
| W30 · 20 Jul–26 Jul | € 106,993.30 | € 107,897.30 | − € 904.00 | −0.8 % | − € 1,240.00 | −13.8 % ▲ | Rimborso INAIL + € 1,240.00 |
| W31 · 27 Jul–2 Aug | € 130,059.30 | € 130,915.30 | − € 856.00 | −0.7 % | + € 48.00 | 0.0 % | Bank charges (item added 1 Sep, r48) − € 48.00 |
| W32 · 3 Aug–9 Aug | € 125,118.30 | € 125,974.30 | − € 856.00 | −0.7 % | — | — | — |
| W33 · 10 Aug–16 Aug | € 135,568.20 | € 136,424.20 | − € 856.00 | −0.6 % | — | — | — |
| W34 · 17 Aug–23 Aug | € 87,318.20 | € 88,174.20 | − € 856.00 | −1.0 % | — | — | — |
| W35 · 24 Aug–30 Aug | € 55,709.20 | € 56,565.20 | − € 856.00 | −1.5 % | — | — | — |
| W36 · 31 Aug–6 Sep | € 108,540.40 | € 109,396.40 | − € 856.00 | −0.8 % | — | — | — |
| W37 · 7 Sep–13 Sep | € 215,046.40 | € 215,902.40 | − € 856.00 | −0.4 % | — | — | — |
| W38 · 14 Sep–20 Sep | € 140,465.10 | € 141,321.10 | − € 856.00 | −0.6 % | — | — | — |

Host statistics. Modelled = a host run on a throwaway overlay holding the items and every erp:*, payroll and manual row (`from ERP data alone`, pilot §7 Week 1) and none of the bank-only rows; bank = the statement closing balance, which equals the book fold because every statement line to 20 Sep 2026 is in the ledger. Δ at the first week already holds the bank-only rows before the window (bank charges Jan–Mar). 26 of 26 weeks within 3 %; 1 new discrepancy(ies) above 2 % of weekly turnover, each explained by a listed bank-only row. The ERP GL one-offs (van, dividend, acconto) are in both columns: they are uncovered for the coverage metric (no item) but not a reproduction gap.

## Backtest — run(cutover_override) at −3 / −6 / −12 months, scored on weekly closing balances (Validation, D23)

| run | cutover_override | MAPE weeks 1–4 | MAPE weeks 5–13 | trough timing | trough depth | directional accuracy | coverage in window | label |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| −3 months | 21 Jun 2026 | 29.9 % (target < 5 %) fail | 64.2 % (< 12 %) fail | 63 d (< 7) fail · forecast 28 Jun vs actual 30 Aug | 15.5 % (< 10 %) fail · € 47,811.40 vs € 56,565.20 | 7 of 13 = 53.8 % (> 85 %) fail | 97.7 % (> 95 %) pass | not cached · excluded from snapshots |
| −6 months | 22 Mar 2026 | 9.7 % (target < 5 %) fail | 19.0 % (< 12 %) fail | 0 d (< 7) pass · forecast 31 May vs actual 31 May | 33.5 % (< 10 %) fail · € 72,060.50 vs € 53,964.50 | 11 of 13 = 84.6 % (> 85 %) fail | 98.2 % (> 95 %) pass | not cached · excluded from snapshots |
| −12 months | 21 Sep 2025 | not run: cutover before horizon start 1 Jan 2026 | — | — | — | — | — | — |

Host statistics (pilot §8.2), each stamped `host statistic · backtest · r51 · engine 1.4.0`. `run(cutover_override)` uses the current items and terms; the model as it was at that date is `at(r)`, which reports CK-W011 when the engine version moved.

## Plan vs actual — 13 closed weeks W26–W38 (22 Jun–20 Sep 2026), cash by category (Variance, D12)

Plan = revision current at the cutover that opened the window: r38 · e07a552 · cutover 21 Jun 2026 · engine 1.3.2, reproduced through `at(r38)` (the at() kit runs the engine recorded in that revision). Actual = r51 · engine 1.4.0. The table stamps both triples and renders **CK-W011** (`Engine version moved since revision r38: snapshot recorded 1.3.2, this build is 1.4.0`) beside it.

| category | plan (r38 · 1.3.2) | actual (r51 · 1.4.0) | Δ | Δ % | moved (same amount, other period) | changed (other amount) |
|---|---:|---:|---:|---:|---:|---:|
| cat:revenue | + € 580,080.00 | + € 537,990.00 | − € 42,090.00 | −7.3 % | − € 42,090.00 | — |
| cat:payroll | − € 156,000.00 | − € 156,000.00 | — | — | — | — |
| cat:contributions | − € 126,000.00 | − € 126,380.00 | − € 380.00 | −0.3 % | — | − € 380.00 |
| cat:opex | − € 83,251.00 | − € 84,506.80 | − € 1,255.80 | −1.5 % | — | − € 1,255.80 |
| cat:subcontract | − € 47,640.00 | − € 50,694.00 | − € 3,054.00 | −6.4 % | — | − € 3,054.00 |
| cat:financing | − € 19,125.00 | − € 19,125.00 | — | — | — | — |
| cat:insurance | — | — | — | — | — | — |
| cat:tax | − € 60,774.30 | − € 60,017.50 | + € 756.80 | 1.2 % | — | + € 756.80 |
| cat:uncovered | — | − € 21,456.00 | − € 21,456.00 | — | — | − € 21,456.00 |
| **Net** | + € 87,289.70 | + € 19,810.70 | − € 67,479.00 | — | − € 42,090.00 | − € 25,389.00 |

Receipts by customer inside the same window:

| customer | plan | actual | Δ | Δ % |
|---|---:|---:|---:|---:|
| customer:acme | + € 219,600.00 | + € 219,600.00 | — | — |
| customer:borghi | + € 95,160.00 | + € 95,160.00 | — | — |
| customer:various | + € 126,270.00 | + € 84,180.00 | − € 42,090.00 | −33.3 % |
| customer:comune_monza | + € 75,000.00 | + € 75,000.00 | — | — |
| customer:veltro | + € 64,050.00 | + € 64,050.00 | — | — |

Variance explanations (event notes on the actual rows):

- `freelance` invoice 30 Jun 2026: extra designer, 8 days → net − € 7,200.00 (changed)
- `subcontract` invoice 31 Jul 2026: extra hours on Veltro line 2 → net − € 9,500.00 (changed)
- `software` invoice 5 Aug 2026: new licence, 5 seats → net − € 2,400.00 (changed)
- `software` invoice 5 Sep 2026: new licence, 5 seats — second month: update the item → net − € 2,400.00 (changed)
- `utilities` invoice 20 Aug 2026: summer air conditioning → net − € 2,140.00 (changed)
- `f24_contrib` invoice 16 Sep 2026: new hire from 1 Aug → net − € 42,380.00 (changed)
- `ret_acme` invoice 31 May 2026: paid at 95 d on 3 Sep 2026 instead of 68 d (7 Aug): moved into the window, not changed
- `ret_borghi` invoice 30 Jun 2026: paid at 55 d (24 Aug) instead of 52 d (21 Aug): moved inside the window, no variance
- `support_misc` invoice 31 Jul 2026: expected 9 Sep (40 d), not received at cutover — the committed leg stays on 9 Sep in the book, the actual column shows nothing (empty track, never a zero bar); the statement line of 24 Sep is matched in Close
- `cat:uncovered`: rows with no item in the window (see Uncovered movements)

## Uncovered movements (actual rows with no item) and coverage (host statistics)

| date | amount | source | ext_id | description |
|---|---:|---:|---:|---:|
| 1 Jan 2026 | − € 48.00 | bank:intesa | 2026-01-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 Feb 2026 | − € 48.00 | bank:intesa | 2026-02-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 Mar 2026 | − € 48.00 | bank:intesa | 2026-03-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 Apr 2026 | − € 48.00 | bank:intesa | 2026-04-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 May 2026 | − € 48.00 | bank:intesa | 2026-05-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 Jun 2026 | − € 48.00 | bank:intesa | 2026-06-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 Jul 2026 | − € 48.00 | bank:intesa | 2026-07-01-0002 | Bank charges (item added 1 Sep, r48) |
| 1 Aug 2026 | − € 48.00 | bank:intesa | 2026-08-01-0002 | Bank charges (item added 1 Sep, r48) |
| 18 Mar 2026 | − € 21,500.00 | erp:gl | GL-2026-0412 | Used van for site work — one-off purchase, declared omission |
| 12 May 2026 | − € 18,000.00 | erp:gl | GL-2026-0655 | Dividend to shareholders — assembly decision, declared omission |
| 30 Jun 2026 | − € 22,600.00 | erp:gl | GL-2026-0810 | F24 acconto IRES/IRAP — NOT MODELLED (CK-I001) |
| 22 Jul 2026 | + € 1,240.00 | bank:intesa | 2026-07-22-0017 | Rimborso INAIL |

- actual ledger rows dated in the horizon to cutover: 360 (+1 correction pair) · pre-horizon rows (calibration history): 78 · committed open rows: 17 · forecast rows: 1
- actual movements € 2,931,158.90 · attributable to a modelled item € 2,867,434.90 · uncovered € 63,724.00 (12 rows) → **coverage 97.83 %** (target > 95 %)
- committed open receivables (gross): € 404,020.00 · committed open payables (gross): − € 21,431.00
- the bank charges were uncovered Jan–Aug (8 × − € 48.00 = − € 384.00); item `bank_charges` added at r48 covers them from 1 Sep — the coverage loop closing

### Coverage checklist (app layer, PRD §9.5) — signed r46 · 10 Aug 2026 · M. Conti · checklist unchanged since

| mechanic | state | evidence in the book |
|---|---:|---:|
| VAT (output, input, recoverable %, split payment) | present | TaxRegime `iva` monthly; VatSpec per item |
| INPS contributions and IRPEF withholding on payroll | present | `f24_contrib`, `f24_contrib_13` (cat:contributions) |
| INAIL | present | `inail` 16 Feb (cat:contributions) |
| 13th month | present | `payroll_13` 15 Dec; no 14th under CCNL metalmeccanico |
| Ritenuta d'acconto remittance (F24 Erario) | **NOT MODELLED** | CK-W004 on `freelance`; € 1,200.00/month = 20 % × € 6,000.00 (host arithmetic) |
| IRES / IRAP saldo and acconti (30 Jun, 30 Nov) | **NOT MODELLED** | CK-I001; the 30 Jun 2026 acconto is uncovered row 2026-06-30-0027 |
| Acconto IVA (27 Dec) | **NOT MODELLED** | declared omission; commercialista to supply the figure in December |
| TFR (severance on exit) | **NOT MODELLED** | declared omission: no exits planned |
| Tax credits | n/a | no credit carried; `_tax:iva:credit` is — |
| Loan and lease instalments | present | `loan_intesa`, `lease_vehicles` |
| Dividends, one-off capex | **declared omissions** | uncovered rows 2026-05-12-0014, 2026-03-18-0009 |

Standing sentence on screen: *The forecast understates outflows by the items marked NOT MODELLED.* Sign-off validity: the chip reads `signed r46 · checklist unchanged` while the tax regimes, cat:tax items, manual_tax flags and declared omissions are the same as at r46; any change flips it to `checklist changed since r46 · re-sign`.

## Receivables and payables — open committed rows at as-of 28 Sep 2026 (D24)

| document | customer | net | VAT | gross (cash) | due (contractual terms) | expected (calibrated) | expected week | overdue vs terms | bucket |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| INV · 31 Jul 2026 | Small customers | + € 34,500.00 | + € 7,590.00 | + € 42,090.00 | 31 Aug 2026 (net 30) | 9 Sep 2026 (40d next) · **on statement 24 Sep — confirm in Close** | W37 | 28 d | 1–30 |
| INV · 31 Jul 2026 | Borghi Impianti | + € 26,000.00 | + € 5,720.00 | + € 31,720.00 | 31 Aug 2026 (net 30) | 21 Sep 2026 (52d next) · **on statement 21 Sep — confirm in Close** | W39 | 28 d | 1–30 |
| INV · 31 Jul 2026 | Acme Automation | + € 60,000.00 | + € 13,200.00 | + € 73,200.00 | 29 Sep 2026 (net 60) | 7 Oct 2026 (68d next) | W41 | — | current |
| INV · 31 Aug 2026 | Small customers | + € 34,500.00 | + € 7,590.00 | + € 42,090.00 | 30 Sep 2026 (net 30) | 12 Oct 2026 (40d next) | W42 | — | current |
| INV · 31 Aug 2026 | Borghi Impianti | + € 26,000.00 | + € 5,720.00 | + € 31,720.00 | 30 Sep 2026 (net 30) | 22 Oct 2026 (52d next) | W43 | — | current |
| INV · 31 Aug 2026 | Acme Automation | + € 60,000.00 | + € 13,200.00 | + € 73,200.00 | 30 Oct 2026 (net 60) | 9 Nov 2026 (68d next) | W46 | — | current |
| INV · 31 Jul 2026 | Comune di Monza (PA) | + € 110,000.00 | — (split payment) | + € 110,000.00 | 31 Aug 2026 (net 30 (PA)) | 30 Nov 2026 (120d next) | W49 | 28 d | 1–30 |

Tie-out: Σ open AR gross € 404,020.00 = AR control total from the erp:ar gate € 404,020.00 · Δ —

| supplier · document | net | VAT | withholding kept | cash | due (terms, calibrated = contractual for AP) | due this week? |
|---|---:|---:|---:|---:|---:|---:|
| Freelancers — ritenuta 20 % (3) · inv 31 Aug 2026 (3 doc) | − € 6,000.00 | − € 1,320.00 | + € 1,200.00 | − € 6,120.00 | 30 Sep 2026 (30d next) | this week |
| Subcontractors — engineering firms (2) · inv 31 Aug 2026 (2 doc) | − € 8,000.00 | − € 1,760.00 | — | − € 9,760.00 | 30 Sep 2026 (30d next) | this week |
| Utilities — electricity, gas, water · inv 20 Sep 2026 (3 doc) | − € 1,650.00 | − € 363.00 | — | − € 2,013.00 | 5 Oct 2026 (15d next) | later |
| Professional fees — commercialista, consulente del lavoro · inv 10 Sep 2026 (2 doc) | − € 2,900.00 | − € 638.00 | — | − € 3,538.00 | 12 Oct 2026 (30d next) | later |

Payables due this week replace a payment-run view: CashKit executes nothing.

## Calibration — observed payment behaviour per customer (host statistics from `query_events`) (D14)

| customer | contractual | n settled | median delay | p25 / p75 / p90 | modal pay day | trend | book offset | proposed |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Acme Automation | net 60 | 22 | +8 d | +7 / +11 / +13 d | day 10 | 0 d (last 6 vs previous 6) | 68d | 68d (= book) |
| Borghi Impianti | net 30 | 22 | +23 d | +21 / +25 / +26 d | day 23 | +2 d (last 6 vs previous 6) ▲ widening | 52d | 53d → set_item ret_borghi due[0].offset |
| Small customers | net 30 | 22 | +10 d | +9 / +11 / +12 d | day 9 | 0 d (last 6 vs previous 6) | 40d | 40d (= book) |
| Comune di Monza (PA) | net 30 (PA) | 1 | +88 d (one invoice) | — | — | — | 120d | n < 12 → segment fallback `PA` 120d in use |
| Veltro Robotics | 30 % at invoice, 70 % net 60 | 2 legs | +0 d | — | — | — | 0d / 60d | n < 12 → contract terms in use |

Percentiles: nearest-rank. Delay = observed offset − contractual days. `Adopt` opens one card per customer: `set_item(<every item tagged customer:x>, settlement.due[0].offset = "<n>d")`, listing the touched items; recalibration is one commit.

## Sensitivity sweep — Acme settlement offset ±10 / ±20 d (one throwaway scenario per value, WHAT-IF)

| ret_acme due[0].offset | LOWEST POINT after cutover | days below floor | horizon close | Δ close vs book | stamp |
|---|---:|---:|---:|---:|---:|
| 48d | € 96,704.90 · 16 Nov 2026 | 0 | € 387,793.08 | + € 74,664.00 | WHAT-IF · sweep ret_acme 48d |
| 58d | € 77,249.00 · 27 Oct 2026 | 0 | € 387,793.08 | + € 74,664.00 | WHAT-IF · sweep ret_acme 58d |
| 68d (book) | € 56,685.00 · 5 Nov 2026 | 5 | € 313,129.08 | — | BASE |
| 78d | € 23,504.90 · 16 Nov 2026 | 9 | € 313,129.08 | — | WHAT-IF · sweep ret_acme 78d |
| 88d | € 23,504.90 · 16 Nov 2026 | 23 | € 313,129.08 | — | WHAT-IF · sweep ret_acme 88d |

## Featured ledger events

| id | date (invoice) | status | item | amount (net) | VAT code · treatment · recov. | settlement | cash leg | cash date | source | ext_id | note / stamp |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ev-0811 | 1 Sep 2026 | actual | rent | − € 4,080.00 | 22 · std · 1.00 | 13d next | − € 4,977.60 | 14 Sep 2026 | erp:ap | 2026-09-RENT-0009 | CORRECTED · SEE ev-0812 (struck through) |
| ev-0812 | 1 Sep 2026 | actual | rent | − € 4,800.00 | 22 · std · 1.00 | 13d next | − € 5,856.00 | 14 Sep 2026 | erp:ap | 2026-09-RENT-0009-c1 | corrects ev-0811 · was − € 4,080.00 · note: “Digits transposed in the CSV import (4,080.00). Invoice shows net 4,800.00; escalation applies from Jan 2027.” |
| ev-0798 | 30 Jun 2026 | actual | ret_borghi | + € 26,000.00 | 22 · std | 55d next (observed; book 52d) | + € 31,720.00 | 24 Aug 2026 | erp:ar | INV-2026-0171-L1-S | Jun retainer, contractual due 30 Jul, paid 24 Aug: moved 3 d vs calibrated 21 Aug, same month |
| ev-0834 | 31 May 2026 | actual | ret_acme | + € 60,000.00 | 22 · std | 95d next (observed; book 68d) | + € 73,200.00 | 3 Sep 2026 | erp:ar | INV-2026-0142-L1-S | May retainer, contractual due 30 Jul, calibrated 7 Aug, paid 3 Sep (moved, not changed). VAT sat in the May return. |
| (generated) | 25 Sep 2026 | generated | payroll | − € 52,000.00 | N2.2 · out_of_scope | 0d | − € 52,000.00 | 25 Sep 2026 | — (payroll:zucchetti file 24 Sep: net € 52,000.00, evidence) | 2026-09 on confirm | Sep net salaries, 25 FTE: a generated occurrence, not a row. The payroll file and the statement line of 25 Sep both match it; Confirm writes the actual row (source payroll:zucchetti, ext_id 2026-09) |
| ev-0802 | 31 Jul 2026 | committed | ms_comune | + € 110,000.00 | N6.9 · split payment | 120d next | + € 110,000.00 | 30 Nov 2026 | erp:ar | INV-2026-0188-L1 | Milestone 2; contractual due 30 Aug (net 30 PA); expected 30 Nov 2026 (28 Nov Sat → next bank day) |
| ev-0829 | 31 Aug 2026 | committed | ret_acme | + € 60,000.00 | 22 · std | 68d next | + € 73,200.00 | 9 Nov 2026 | erp:ar | INV-2026-0203-L1 | Aug retainer; contractual due 30 Oct; calibrated 7 Nov 2026 (Sat) → 9 Nov 2026 |
| ev-0830 | 31 Jul 2026 | committed | ret_borghi | + € 26,000.00 | 22 · std | 52d next | + € 31,720.00 | 21 Sep 2026 | erp:ar | INV-2026-0187-L1 | Jul retainer; expected 21 Sep; statement line 21 Sep found → match proposed (void + `-S` actual) |
| ev-0831 | 31 Jul 2026 | committed | support_misc | + € 34,500.00 | 22 · std | 40d next | + € 42,090.00 | 9 Sep 2026 | erp:ar | INV-2026-0189-L1 | Jul support invoices (one row per line); expected 9 Sep, not received at cutover — annotated 'not received' at r49; statement line 24 Sep found → match proposed, date moved +15 d |
| ev-0840 | 15 Oct 2026 | forecast | — (cat:capex) | − € 22,500.00 | 22 · std · 1.00 | 0d | − € 27,450.00 | 15 Oct 2026 | manual | — | Server refresh, Dell quote, net; input VAT € 4,950.00 enters the Oct 2026 return · editable in place |
| ev-0761 | 22 Jul 2026 | actual | — (no item) | + € 1,240.00 | N2.2 · out_of_scope | 0d | + € 1,240.00 | 22 Jul 2026 | bank:intesa | 2026-07-22-0017 | Rimborso INAIL · uncovered (cat:uncovered) |

## Trace examples

### trace(run, item=ret_acme, period=2027-04, measure=cash, depth=3) → € 74,664.00

| step | binding | value |
|---|---|---:|
| segment amount | segments[1].amount.constant (segment 1 Jan 2026 → open) · net, excl. VAT | € 60,000.00 |
| × escalation | p.istat_index = 0.02 · anchor segment_start 1 Jan 2026 · year 1 → (1 + 0.02)^1 = 1.02 | € 61,200.00 |
| × probability | segments[1].probability = 1 | € 61,200.00 |
| × settlement share | due[0].share = 1.0 · due[0].offset = "68d" · basis accrual | € 61,200.00 |
| − withholding | due[0].withholding = 0 | € 61,200.00 |
| + VAT | vat.rate = p.vat_standard = 0.22 · treatment standard | € 13,464.00 |
| → cash date | accrual 31 Jan 2027 + 68 d = 9 Apr 2027 (Fri) → adjust next → **9 Apr 2027** | |
| **= cash leg** | | **€ 74,664.00** |

Stamp: `as-of 2026-09-28 · r51 · 7c2e19b · base · engine 1.4.0 · ret_acme`

### trace(run, item=pipe_nord, period=2027-01, measure=cash) → € 21,960.00

€ 30,000.00 × escalation 1 × probability 0.6 = € 18,000.00 · share 1.0 @ 40d · withholding 0 · + VAT € 3,960.00 = **€ 21,960.00** · accrual 30 Nov 2026 + 40 d = 9 Jan 2027 (Sat) → next bank day 11 Jan 2027

### trace(run, item=freelance, period=2026-10, measure=cash) → − € 6,120.00

net − € 6,000.00 × escalation 1 × probability 1 = − € 6,000.00 · share 1.0 @ 30d → leg − € 6,000.00 · withholding 0.20 keeps back € 1,200.00 → − € 4,800.00 · VAT 22 % adds € 1,320.00 → cash paid to the freelancers **− € 6,120.00** on 30 Oct 2026 (30 Oct 2026 Fri → next). The € 1,200.00 kept back is owed to the state on the 16th of the next month; no item generates it → CK-W004.

### trace(run, item=ms_veltro, period=2026-09 and 2026-11, measure=cash) — a split settlement

- leg 0: share 0.3 of € 85,000.00 = € 25,500.00 + VAT € 5,610.00 = **€ 31,110.00** · 30 Sep 2026 + 0 d = 30 Sep 2026 (Wed) → 30 Sep 2026
- leg 1: share 0.7 of € 85,000.00 = € 59,500.00 + VAT € 13,090.00 = **€ 72,590.00** · 30 Sep 2026 + 60 d = 29 Nov 2026 (Sun) → 30 Nov 2026
In the grid each leg is its own cell in its own week; the row header carries a split glyph; the trace lists both legs.

### why_zero examples — the five causes

- (1) `why_zero(pipe_nord, 2026-10)` → period outside every segment: the only segment starts 1 Nov 2026.
- (2) `why_zero(pipe_nord, 2027-01, scenario=downside)` → probability 0: the overlay sets segments[0].probability = 0 (WHAT-IF · downside).
- (3) `why_zero(sub_vat_tax, 2026-W40)` → upstream zero propagated through the formula: every input of the derived item (`cat:contributions`, `cat:tax`) is zero in W40; no F24 section and no VAT payment fall in that week.
- (4) `why_zero(payroll, 2026-09)` → generation suppressed by cutover: the ledger row ev-0821 (committed, 25 Sep) carries September.
- (5) `why_zero(ms_comune, 2026-10)` → settlement produced no cash leg this period: the schedule lists no amount for 31 Oct 2026; next accrual 30 Nov 2026 → cash 30 Mar 2027 (120 d, next).
- also (1): `why_zero(insurance, 2026-10)` → the annual recurrence yields no occurrence in October; the segment covers the period but generates on 1 Apr only.

## Diagnostics (validate()) — engine text verbatim

| severity | code | item / event | field | message | suggested_fix |
|---|---:|---:|---:|---:|---:|
| warning | CK-W004 | freelance | settlement.due[0].withholding | Withholding in use but no cat:tax item covers the counter-leg | Withholding reduces one cash leg only; the engine does not generate the other side. Model the counter-leg — the remittance when you withhold, the credit when someone withholds from you — as an item tagged cat:tax. |
| info | CK-I001 | — | tax_regimes[iva] | A TaxRegime is present but no non-VAT cat:tax items exist | The engine schedules only what a TaxRegime accumulates. Any other obligation is modelled as an ordinary item tagged cat:tax; which ones apply is a question about the entity, not about the engine. |

Status strip: `diagnostics 0 E 1 W 1 I`. No CK-W003: every actual row is dated (invoice date) on or before the cutover. The Italian checklist (which mechanics, which dates, the € 1,200.00 remittance) lives in the coverage block above, never inside a diagnostic row (ADR-0021).

## Alert rules (host table, stamped `app config · unversioned`) and fired alerts

| rule | kind | over | state |
|---|---:|---:|---:|
| floor | `min_cash_below` | day-grain `cash` < `p.min_cash` after cutover, evaluated by the host after every commit or import | fired for r51, see below |
| stale-bank | `source_stale` | bank:intesa older than its cadence (each bank day) | quiet |
| stale-payroll | `source_stale` | payroll:zucchetti older than 31 days | quiet |

- fired `floor`: first day below € 60,000.00 is **4 Nov 2026** · closing balance that day € 59,308.00 · 5 day(s) below the floor in the horizon · lowest € 56,685.00 on 5 Nov 2026 · back above on 9 Nov 2026 · run r51 · 7c2e19b · base · engine 1.4.0
- historic (actuals): below the floor on 13 day(s) before cutover, lowest € 38,807.20 on 1 Sep 2026 — shown in the Position summary, not as an alert
- no recommended action text on any alert (ADR-0021)

## Scenario compare — compare([base, downside], metric=cash)

| metric | BASE · r51 | WHAT-IF · downside | Δ vs base (service-computed) |
|---|---:|---:|---:|
| Horizon close 30 Jun 2027 | € 313,129.08 | − € 88,334.92 | − € 401,464.00 |
| LOWEST POINT after cutover | € 56,685.00 · 5 Nov 2026 | − € 119,030.62 · 30 Mar 2027 | − € 175,715.62 |
| First day below min_cash | 4 Nov 2026 | 25 Sep 2026 | — |
| Days below floor | 5 | 249 | 244 |
| First negative day (runway_end) | not within horizon | 16 Oct 2026 | — |
| Closing 31 Dec 2026 | € 158,742.80 | € 3,800.80 | − € 154,942.00 |
| Total inflow | € 3,359,172.00 | € 2,887,764.50 | − € 471,407.50 |

Config diff (overlay `downside`, parent base, forked at r51) — items only, events are book-level:

- `ret_acme`, `ret_borghi`, `support_misc`: segments split at 1 Sep 2026, amount × 0.85 from 1 Sep (segments atomic); due[0].offset 68d → 98d, 52d → 82d, 40d → 70d
- `ms_comune`: schedule amounts on/after 1 Sep × 0.85; due[0].offset 120d → 150d · `ms_veltro`: schedule amounts on/after 1 Sep × 0.85
- `pipe_nord`: segments[0].probability 0.6 → 0 (why_zero cause 2)
- params: none · events: none (committed open invoices keep their amounts; their cash dates follow the overlay's terms until settled)

## Revision diffs — diff_revisions(r50, r51) and diff_revisions(r49, r51)

**r50 · b91d3a4 → r51 · 7c2e19b** — config diff: cutover 2026-09-13 → 2026-09-20 · events: +14 actual rows (W38 matches), +1 void, +1 correction pair (ev-0811/0812) · items: none · params: none

| outcome | at old revision | at r51 | Δ |
|---|---:|---:|---:|
| Horizon close 30 Jun 2027 | € 313,509.08 | € 313,129.08 | − € 380.00 |
| LOWEST POINT after (old) cutover | € 57,065.00 · 5 Nov 2026 | € 56,685.00 · 5 Nov 2026 | − € 380.00 |
| Closing 31 Dec 2026 | € 159,122.80 | € 158,742.80 | − € 380.00 |
| Days below floor | 5 | 5 | 0 |

**r49 · 41f0c8e → r51 · 7c2e19b** — config diff: cutover 2026-09-06 → 2026-09-20 · events: +31 actual rows, +2 void, +1 correction pair, +1 forecast event (ev-0840, r50) · items: none · params: none

| outcome | at old revision | at r51 | Δ |
|---|---:|---:|---:|
| Horizon close 30 Jun 2027 | € 336,009.08 | € 313,129.08 | − € 22,880.00 |
| LOWEST POINT after (old) cutover | € 84,515.00 · 5 Nov 2026 | € 56,685.00 · 5 Nov 2026 | − € 27,830.00 |
| Closing 31 Dec 2026 | € 181,622.80 | € 158,742.80 | − € 22,880.00 |
| Days below floor | 0 | 5 | 5 |

## History

| revision | when | author | message | engine |
|---|---:|---:|---:|---:|
| r51 · 7c2e19b | 21 Sep 2026 09:40 | M. Conti (controller) | Reconcile W38; set_cutover 2026-09-20; correct ev-0811 (rent Sep, digits transposed) | 1.4.0 |
| r50 · b91d3a4 | 18 Sep 2026 16:12 | M. Conti | Add forecast event ev-0840: server refresh Oct (Dell quote, € 22,500.00 net) | 1.4.0 |
| r49 · 41f0c8e | 14 Sep 2026 09:35 | M. Conti | Reconcile W37; set_cutover 2026-09-13 | 1.4.0 |
| r48 · 5d0e7a1 | 7 Sep 2026 09:50 | M. Conti | Reconcile W36; set_cutover 2026-09-06; add item bank_charges (uncovered Jan–Aug) | 1.4.0 |
| r47 · c3a9f02 | 31 Aug 2026 09:20 | M. Conti | Reconcile W35; set_cutover 2026-08-30 | 1.4.0 |

Referenced elsewhere:

| revision | when | author | message | engine |
|---|---:|---:|---:|---:|
| r46 · 8b4c210 | 10 Aug 2026 11:05 | M. Conti | Sign coverage statement (checklist v3: ritenute, IRES/IRAP, acconto IVA, TFR, dividends, capex declared) | 1.4.0 |
| r38 · e07a552 | 22 Jun 2026 09:30 | M. Conti | Reconcile W25; set_cutover 2026-06-21 | 1.3.2 |
| r37 · 9ac41d0 | 28 May 2026 11:03 | L. Ferri (CFO) | Recalibrate Acme 60d → 68d, Borghi 30d → 52d (set_item ret_acme, ret_borghi; Q1 calibration) | 1.3.2 |
| r12 · 2f7e0c9 | 9 Jan 2026 15:20 | L. Ferri (CFO) | set_param min_cash 60000, credit_line 150000 | 1.3.0 |

Engine moved 1.3.2 → 1.4.0 between r38 and r39 (own row type in the History list). `at(r38)` runs the engine recorded in r38 and reports CK-W011 when compared with r51.

