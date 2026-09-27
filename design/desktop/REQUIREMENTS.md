# CashKit Desktop — UI and functional requirements

**Status** revision 2 after critique, for Luca's review · **Date** 2026-09-28 · **Demo book** `design/desktop/DATASET.md` (every figure on every screen comes from it; nothing is typed by hand) · **Style** S1b Swiss Ledger Soft Corners, unchanged tokens

The desktop app is a window onto the same engine as the mobile app. It shows only figures the engine computed, with their provenance, and writes only through SDK verbs behind a confirmation card. Anything the host computes (a percentile, a MAPE, a bank-vs-book delta) is labelled `host statistic` and never looks like an engine receipt. The app adds what the enterprise persona needs and the consumer app does not have: the Monday close (import, tie-out, match, roll the cutover), the 13-week grid, receivables and payables, calibration, variance, backtest and reproduction, tax calendar and coverage, history and diff.

What changed in this revision, in one paragraph: the dataset was rebuilt so the engine can produce every figure in it (undotted params, literal settlement offsets, net-authored events, monthly VAT with the engine's own `payment_offset`, business-day adjust on every inflow, a Monday as-of with a weekly cutover); the rail follows the controller's week instead of the engine's objects; every screen has all four states; alerts, revert, matching and sign-off are reduced to what an SDK verb or a host table can back; a table of host statistics, a per-row source map for the grid and a list of SDK feature requests were added.

---

## 1. Purpose and users

The app answers one question for a small or mid-size company: *how much cash do we have, when does it run short, and why.* It does not keep books, pay anyone, connect to a bank, or give advice.

| Persona | What they do in the app | Rhythm |
|---|---|---|
| **Controller** (M. Conti in the demo) | Runs the Monday close: import, tie-out, match, roll the cutover, review variance, commit. Records corrections, maintains items, signs the coverage statement. The only regular writer. | Daily import check (5 min). Monday close (40 min). Month end: F24 preview on the 10th. |
| **CFO / founder** (L. Ferri) | Reads the position and the 13-week grid after the Monday commit, forks and compares scenarios, sweeps a customer's terms, adopts calibrations, exports the board pack. Writes rarely (params, items, scenarios). | Weekly review. Monthly board pack. Quarterly recalibration. |
| **Treasurer** (the controller in a 25-person firm; a separate role above ~100 people) | Reads the daily position: bank balance, next 10 bank days, payables due this week, receivables expected this week, alerts. Reads only. | Daily, 10 minutes. |
| **External accountant** (commercialista, Studio Bassi) | Reads the tax calendar and the coverage statement; supplies the IRES/IRAP acconti as a schedule that the controller enters with `source=commercialista`. Read-only session. | Quarterly and at year end. |

Single writer per book stays (ADR-0027, PRD §6.6). The app ships one writer plus read-only viewer sessions. Four-eyes review stays an open question (§8).

### 1.1 Workflows, step to screen

Every step names the screen and the SDK call. Figures are from the dataset (as-of Mon 28 Sep 2026, cutover Sun 20 Sep, statement to Fri 25 Sep, r51).

**Controller — Monday close**

1. **Position (D02)**. Bank statement balance 25 Sep `€ 114,592.10` (fact) beside book balance 25 Sep `€ 109,712.10` (engine), Δ `+ € 4,880.00`, "1 unmatched line → Close". `frame()`; statement lines are staged host evidence.
2. **Close › Import (D09)**. erp:ar and erp:ap ran at 07:10: eight gates pass, inserted 0, skipped 7 and 10. `import_events` (idempotent on source + ext_id).
3. **Close › Reconcile (D11)**. Tie-out at the top. Seven proposed matches covering 18 statement lines (1:1 and N:1, incl. Borghi Jul `+ € 31,720.00` on 21 Sep and the Jul support invoices `+ € 42,090.00` expected 9 Sep, received 24 Sep). One card: **Confirm 7 matches**. Writes: `void_event(committed, note)` + the actual row with ext_id `‹doc›-S` for committed rows; `add_event(actual, item=…)` for generated occurrences. One exception: `+ € 4,880.00` Studio Rossi → annotate as uncovered (`add_event`, no item, `cat:uncovered`) or add an item (D06).
4. **Set cutover to 25 Sep** card (D19). `set_cutover(2026-09-25, note)`; the CK-W003 check lists actual rows dated after 25 Sep (none). The gate that enables the card (exceptions cleared or annotated) is a host check, not a book rule.
5. **Variance (D12)**. Window W26–W38, plan r38. The `software` row shows `− € 2,400.00` two months running with the note "update the item" → `set_item(software, amount 2,150 → 2,400 from 1 Sep)` card.
6. **Save** (D19 commit modal). Message "Reconcile W39; set_cutover 2026-09-25; software 2,150 → 2,400" → `commit()` as r52. Alerts re-evaluate in the host after the commit.

**CFO — weekly review and board pack**

1. **Position (D02)** after the commit: LOWEST POINT `€ 56,685.00 · 5 Nov 2026`, first day below floor 4 Nov, 5 days, back above 9 Nov. `summary()` + host statistics.
2. **Forecast grid (D03)**: W45 closes at `€ 56,685.00`; Headroom row `− € 3,315.00`; W44 headroom `+ € 1,369.00`. Click W45 → **Trace (D04)**: no receipts in the week, `cat:opex − € 4,684.00`. `frame(grain=week)`, `trace()`.
3. **Scenarios (D13)**: compare base and downside (WHAT-IF): horizon close `€ 313,129.08` vs `− € 88,334.92`. `compare()`.
4. **Book › Params & calibration (D14)**: Borghi median delay +23 d vs book 52 d (net 30 + 22) → proposed 53 d; trend +2 d widening. Adopt → `set_item(ret_borghi, due[0].offset "53d")` card, then Save.
5. Monthly: **Report & History › Report (D25)**: export xlsx and pdf at r52 with the stamp on every sheet and page. `export()`.

**Treasurer — daily position (10 minutes)**

1. **Position (D02)**: bank balance at the statement date, Δ to book, available liquidity `€ 264,592.10` (bank + undrawn line), buffer 19 days, next 10 bank days with C/G/F glyphs (30 Sep: `− € 6,120.00` freelance C, `+ € 31,110.00` Veltro G, `− € 9,760.00` subcontract C).
2. **Receivables & Payables (D24)**: AR expected this week, AP due this week (`− € 6,120.00`, `− € 9,760.00` on 30 Sep). `frame(status=committed)`.
3. **Alerts (D17)** from the bell: `floor` fired for r51; `source_stale` quiet.

**Commercialista — quarterly (read-only session)**

1. **Position › Tax calendar (D15)**: the F24 of 16 Oct: IVA `− € 38,903.10` + INPS/IRPEF `− € 42,000.00` = `− € 80,903.10`; ritenute NOT MODELLED.
2. **Book › Coverage & sources (D21)**: IRES/IRAP NOT MODELLED (CK-I001), acconto IVA NOT MODELLED; sends the schedule by email.
3. The controller enters it: item `tax_ires_irap` (cat:tax, schedule 30 Nov, 30 Jun) or committed events with `source=commercialista` (D06/D07 → D19); CK-I001 clears; the coverage chip flips to `checklist changed since r46 · re-sign`; the controller re-signs (commit).

## 2. Design principles

1. **Position first, then the grid, then the reasons.** The landing screen is the bank fact and the book beside it; one click reaches the 13-week grid; one more reaches the trace. *(AFP; J.P. Morgan 13-week guidance; Intuit cash-position report; pilot guide §7 Week 1.)*
2. **The rail follows the week, not the engine.** Position · Close · Forecast · Receivables & Payables · Variance · Scenarios · Report & History · Book. Items are reached from the grid and from Book. *(Practitioner critique; treasury cadence research.)*
3. **The grid is the working surface.** 13 weekly columns, receipts by customer, disbursements by category, VAT & tax as its own block, fixed frame rows that are engine rows. *(Wall Street Prep; Atlar; pilot §3.7; Kyriba and Planful reviews.)*
4. **Actual and forecast never blend.** A vertical cutover rule; a cell is one of actual, committed, forecast or generated and shows which; an aggregated column that hides a day-grain floor crossing marks it. *(PRD D6; ADR-0013; Float reviews.)*
5. **Every figure is a receipt, or says it is not.** Engine figures carry the stamp (as-of, revision, scenario, engine, item ids). Host statistics carry `host statistic · definition`. The client never computes a money number. *(ADR-0023, ADR-0024, D-MLP-06/41/50.)*
6. **Click a cell, get the arithmetic, then the edit menu.** `trace()` to depth 3 is the interaction primitive; the tree it returns is the edit menu; empty cells explain themselves through `why_zero()`. *(ADR-0013.)*
7. **Detail lives in a side panel; modals only for the irreversible.** Commit, apply a scenario, record a correction, import conflict. *(Cloudscape; Atlassian; NN/g.)*
8. **A diagnostic, never a guess.** Engine text verbatim; app-layer codes in the CK-E9xx band (D-MLP-19); the Italian checklist lives in the coverage block, never inside a diagnostic row. Imports fail closed. *(PRD §6.5, §10.1; ADR-0021; pilot §4.)*
9. **Corrections leave a scar.** Actuals are immutable; a correction is an appended row with a mandatory note, shown with the original struck through. *(ADR-0012, D-MLP-61.)*
10. **Coverage before calibration.** What is *not* modelled is shown before any chart, and the controller signs it; the sign-off is valid while the checklist is unchanged. *(Pilot §1, §8.2, §11; PRD §9.5.)*
11. **Keyboard and command palette.** One tab stop per grid, `Cmd+K` for every action and the 21-intent grammar, shortcuts beside commands. *(WAI-ARIA grid; Superhuman, Retool; Bloomberg behaviours, not its look.)*
12. **Density without noise.** Two row heights, Plex Mono right-aligned figures, hairline dividers, colour only above a threshold. *(Carbon, Fluent, Few, Tufte, S1b spec.)*
13. **Uncertainty as separate lines, never a band.** Scenarios and sweeps are distinct stamped series. *(PRD §7.3; pilot §5.3.)*

## 3. Information architecture

### 3.1 Shell

Three panes from 1440 px: a 56 px left rail (icons, labels on hover, expandable to 200 px), the working area, a right detail panel of 440 px (resizable 360–560, collapsible with `Cmd+]`). Minimum viewport 1366 × 768; below 1440 px the right panel is an overlay. A 40 px status strip sits under the header on every screen.

### 3.2 Left rail (8 entries, in the order of the controller's week)

| # | Entry | Icon (Lucide, Pen-valid) | Screens |
|---|---|---|---|
| 1 | Position | `layout-dashboard` | D02 (tabs: Position · Tax calendar D15) |
| 2 | Close | `check-square` | D09 Import → D11 Reconcile & set cutover, as one three-step screen (Import · Reconcile · Set cutover) |
| 3 | Forecast | `table-2` | D03 (+ D04 panel); Items D05/D06 reachable from any row header |
| 4 | Receivables & Payables | `receipt` | D24 |
| 5 | Variance | `git-compare` | D12 |
| 6 | Scenarios | `git-branch` | D13 |
| 7 | Report & History | `history` | D16 History & diff · D25 Report view & export |
| 8 | Book | `settings` | D05 Items, D14 Params & calibration, D21 Coverage & sources, D23 Validation (backtest, reproduction), D20 Settings & export, D07 Ledger, as tabs |

Alerts open from the bell in the header (D17). Diagnostics open from the count badge in the status strip (D22). Ask and the command palette open from the header or `Cmd+K` (D18). `?` opens the shortcut sheet (D26).

### 3.3 Global header (48 px)

Left to right: **book switcher** (outlined 1 px ink control: book icon, name, chevron; lists books with currency and horizon) · **scenario switcher** (`BASE · PLAN OF RECORD` or `WHAT-IF · downside` in Warning; activation is app state, not a card, and supersedes pending cards) · **as-of** `as-of 28 Sep 2026` (host clock) · **cutover** `cutover 20 Sep 2026` · **revision** `r51 · 7c2e19b · engine 1.4.0` · **write state** from `status()`: `saved` when clean (the demo), or `2 changes · Save · Discard` when the overlay holds uncommitted changes · **search / palette** `Cmd+K` · **Ask** · **bell** with an 8 px Signal dot when an alert fired (the demo: `floor` fired for r51).

All figures and dates in the header are Plex Mono 12. The book's currency is named in the book switcher and in column headers; cells carry `€` only in tables where the column mixes figures and text (see UI-07).

### 3.4 Status strip (40 px, every screen; every value read from the source inventory, `status()` and `validate()`)

`cutover 20 Sep 2026 · statement 25 Sep · last commit r51 by M. Conti 21 Sep 09:40 · erp:ar 28 Sep 07:10 (7 rows) · erp:ap 28 Sep 07:10 (10 rows) · bank:intesa 25 Sep 18:30 (19 lines, 0 matched) · payroll:zucchetti 24 Sep · diagnostics 0 E 1 W 1 I · coverage 97.83 % · signed r46 · saved · density ◐`

A stale source is named, not coloured: `payroll:zucchetti 34 days old, cadence monthly`. A source not covered (commercialista, backlog) is named in Coverage & sources, not in the strip.

### 3.5 Working area

The main pane holds the screen's table or grid with a toolbar (filters, grain, measure, group-by, columns, saved views). The right panel holds the detail of the selected row or cell: Trace, Edit, Diagnostics, History tabs. It is the same component on every screen. A pending confirmation card docks at the bottom of the right panel (one pending-card region per screen, D-MLP-65).

## 4. Screen inventory

Dominant region: **main** = main pane, **panel** = right detail panel, **overlay** = modal or palette. Every row lists four states: empty · loading · error · permission (viewer = read-only session, lock = CK-E013, at-revision = FR-32).

| id | name | purpose (the one question) | dominant region | primary action | secondary actions | states |
|---|---|---|---|---|---|---|
| D01 | Sign in · Book picker | Which book am I working in? | main (centred list) | Open book | Create book, magic-link sign in, "check your email" | empty: "No books yet — create one or import" · loading: list skeleton · error: link expired · permission: viewer badge on read-only books |
| D02 | Position | How much cash is in the bank, how much does the book say, and when is it lowest? | main: tie-out row + KPI strip + balance chart + next 10 bank days | Open Close when Δ ≠ 0, else open the grid | Trace any KPI, open alert, open coverage chip, Tax calendar tab | empty (first run): tie-out reads "no statement imported", flat placeholder chart, "Import from your ERP" / "Add an item" · loading: skeleton tiles, chart shape · error: CK-E banner, last good figures dimmed with their stamp · permission: read-only, Save hidden |
| D03 | Forecast grid | Where does cash come from and go, week by week? | main: 13-week grid, frozen first column | Click cell → trace (D04) | Grain day/week/month/quarter, measure cash/accrual, status filter, committed-only line, group-by tag, hide empty rows, show ids, saved views, export | empty: "No items or events yet" with starters · loading: grid skeleton; on recompute figures dim to Tertiary with "recomputing" stamp, never blank · error: CK-E banner + offending row · permission: cells read-only, edit actions hidden |
| D04 | Trace panel | Why is this cell this number? | panel | Edit the input the tree points to (opens D19) | why_zero view, jump to item / event / param, copy receipt, Record correction (actuals only) | empty: no generator → why_zero text · loading: inline spinner in the panel only · error: trace diagnostic · permission: actions hidden, receipt visible |
| D05 | Items | Which generative lines make the forecast? | main: tree by tag dimension (cat, customer, supplier) | Open item (D06) | New item, shift / scale macro on a tag node, filter chips from describe_book | empty: "Add an item or import contracts" · loading: list skeleton · error: item-level diagnostics inline · permission: read-only |
| D06 | Item editor (drawer) | What does this line do, exactly? | panel (wide, 560 px) | Save → confirmation card (D19) | Add / split segment, settlement legs, VAT spec, escalation param, probability, tags; live preview of the next 12 periods stamped PREVIEW | empty (new): recurrence + amount first, Advanced collapsed; "Once" creates a forecast event and says so · loading: field skeleton while the single-item read returns (SR-6) · error: CK-E004/E005/E009/E011 inline on the field · permission: read-only; derived items always read-only with "defined in SDK" |
| D07 | Ledger | What actually happened, and what is committed? | main: event table | Record actual / add forecast event (D19) | Filter status, source, period, tag; include voided; column reorder; export; open correction (D08) | empty: "No events yet. Import AR/AP from your ERP export." · loading: table skeleton · error: CK-W003 rows flagged inline · permission: read-only |
| D08 | Correction dialog (modal) | Record a correction with a note. | overlay | Record correction | Cancel | empty: n/a (opens on a row) · loading: dry-run spinner on the button · error: note missing → refused; amount shape wrong (D-MLP-64) · permission: writers only, entry hidden for viewers |
| D09 | Close › Import | Did the extract pass the gates, and what will land? | main: gates table → report → preview | Commit import (all-or-nothing, D19) | Choose source (erp:ar, erp:ap, backlog, payroll, bank, commercialista, manual, csv), saved column mapping, target stamp (base ledger) | empty: file drop with target stamp shown first · loading: determinate progress bar with row count · error: any failed gate = ledger untouched, screen says so · permission: writers only |
| D10 | Import conflict dialog (modal) | Which rows collide with what the ledger has? | overlay: conflict table | Abort batch (default) | Keep existing (skip rows), open correct_event for a row | empty: n/a · loading: payload comparison spinner per row · error: CK-E010 per row with existing vs new payload · permission: writers only |
| D11 | Close › Reconcile & set cutover | Does the bank match the book, and can we roll the cutover? | main: tie-out row, two-column match view, exceptions list | Confirm ‹n› matches (one card), then Set cutover to ‹date› (D19), enabled when exceptions are cleared or annotated | Match by hand, correct (D08), annotate, reconciliation report | empty: "No statement lines in the open window." · loading: match proposals computing (spinner on the proposals column) · error: CK-W003 actual after the proposed cutover · permission: writers only |
| D12 | Variance | Where did the forecast miss, and was it timing or amount? | main: category × plan / actual / Δ / Δ % / moved / changed; receipts by customer | Open row → events and trace | Window picker, plan revision picker (default: revision current at the window's cutover), materiality filter, send to item (set_item) | empty: "Advance cutover after the first reconciliation to see plan vs actual." · loading: at() reproduction spinner on the plan column · error: CK-W011 rendered beside the table when the engine moved; host statistic unavailable → "not computed", never 0 · permission: read-only |
| D13 | Scenarios & compare | What if, and how far from base? | main: scenario list + compare table + small-multiple charts | Fork (D19) | Compare up to 5, config diff, work in this scenario, flatten / unset, Apply to base (withheld, SR-10) | empty: "Fork the base to try a change." · loading: per-column spinner · error: overlay touching an actual → CK-E006 · permission: read-only compare |
| D14 | Book › Params & calibration | What levers move the trough, and what did customers actually do? | main: params table + calibration table + sweep | Adopt → set_item card per customer (D19) | Sweep one item's offset over steps (throwaway scenarios), view dependents, segment fallback, trend | empty: "No settled invoices yet" for calibration · loading: sweep progress bar with run count · error: CK-E007/E008 on a param edit · permission: read-only |
| D15 | Position › Tax calendar | What do we pay on the 16th, section by section? | main: month strip of F24 dates + liquidation preview | Trace a liability cell | Filter by section, open coverage row for a NOT MODELLED section | empty: no regime → "Add a tax regime" · loading: month strip skeleton · error: CK-E band shown first · permission: everyone |
| D16 | Report & History › History & diff | What changed between two revisions, and can we reproduce a number? | main: revision list; panel: config diff + outcome diff | Open at revision (read-only, AT ‹sha› banner) | Select two → diff, Reproduce (string-equal re-ask), export at revision | empty: only the initial commit · loading: diff spinner · error: engine-version move shown as its own row type and CK-W011 · permission: everyone |
| D17 | Alerts (panel) | Which rules fired, on which run? | panel (from the bell) | Open the cell / trace | Edit rule (host table), mute | default and demo: `floor` fired for r51 (4 Nov 2026, € 59,308.00) · empty: "No rule fired for ‹rN›." · loading: last evaluation time shown, spinner · error: rule references a missing param · permission: rules editable by writers, list for everyone |
| D18 | Command palette & Ask | Do anything by name, or ask in plain words. | overlay (640 px, `Cmd+K`) with Ask docked as a panel | Run action / send | Scope pills Book › Scenario › Item; read intents answer inline as receipts; mutation intents produce D19 | empty: recent actions · loading: working steps with revision · error: refusal / clarify / needs-a-real-date (CK-E902) / offline · permission: mutation intents hidden for viewers |
| D19 | Confirmation card | What exactly will be written, and what does it change? | panel dock (modal for commit / apply / correction) | Verb button ("Confirm 7 matches", "Set cutover to 25 Sep", "Commit as r52") | Edit details, Keep editing, Discard | ready · stale (Refresh) · refused (diagnostics) · applied (ChangeReport); loading: dry-run spinner; permission: never shown to viewers |
| D20 | Book › Settings & export | What are the book-level values, and how do we get numbers out? | main: cards | Save card → D19 | Horizon, opening balance, accounting day, calendar, categories, VAT code mapping table, link to params (D14), export xlsx / pdf / parquet, viewers | empty: new book → horizon and opening balance cards first · loading: card skeleton · error: CK-E020 on currency mismatch; CK-E903 on an unmapped VAT code · permission: writers only |
| D21 | Book › Coverage & sources | What is not modelled, who signed, and where does each figure come from? | main: coverage checklist + uncovered rows + source inventory | Sign coverage statement (commit, D19) | Declare an omission, open the fix for a NOT MODELLED row, open a source's last gate report | empty: "No actual rows yet — coverage needs a first import." · loading: checklist skeleton · error: CK-I001 / CK-W004 rows shown first · permission: signing needs a writer |
| D22 | Diagnostics (panel) | What does validate() say? | panel (from the badge) | Fix button per diagnostic | Filter by severity, code, item | empty: "validate() returned nothing." · loading: spinner · error: CK-E banner mirrors here · permission: fix buttons hidden for viewers |
| D23 | Book › Validation | Does the model reproduce the past, and how did a past forecast score? | main: reproduction table; backtest runner and metrics | Run backtest (−3 / −6 / −12 months) | Reproduction window, per-discrepancy note, export | empty: "Needs 13 closed weeks." · loading: determinate progress bar per run · error: cutover before horizon start → "not run", never 0 · permission: read-only |
| D24 | Receivables & Payables | Who owes us, who do we owe, and when does the cash move? | main: AR / AP tabs with buckets | Open row → event and terms | Filter customer, supplier, bucket, source; tie-out line vs the AR control total | empty: "No open invoices." · loading: table skeleton · error: tie-out Δ ≠ 0 flagged with the gate link · permission: read-only |
| D25 | Report & History › Report view & export | What do the board and the bank see? | main: A4 preview | Export pdf / xlsx | Choose sheets (position, grid, aging, variance, coverage), grain, revision | empty: "Nothing to report until the first commit." · loading: rendering progress · error: WHAT-IF export needs the scenario named on every page; refused if not · permission: everyone |
| D26 | Shortcut sheet | Which key does what? | overlay (`?`) | — | — | empty: n/a · loading: n/a · error: n/a · permission: viewer sheet hides write shortcuts |

First-run empty states: D01, D02, D03, D05, D07, D13, D21. Each has a heading, one sentence, one primary action.

## 5. Functional requirements

| id | requirement | SDK verb / intent |
|---|---|---|
| FR-01 | Position shows, from `summary()`: horizon close, `min_cash` with its period over the whole horizon, `total_inflow`, `total_outflow`, `runway_end` ("not within horizon" when None). It adds, labelled `host statistic` with the definition beside: LOWEST POINT after cutover with its date, first day below `min_cash`, days below floor, back above the floor, below floor in actuals. Each figure has a stamp and a trace link. | `summary()`, `frame(grain=day)`, R2–R4 |
| FR-02 | Position opens with the bank fact: statement closing balance at the statement date (source, line count, import time), the book balance at the same date (engine), and Δ bank − book; a Δ above the materiality threshold links to the Close exceptions. The book balance at cutover is a fourth figure and names any committed leg dated on or before cutover that was not received. Never one "today" number. | `frame()` cash; statement lines are host evidence |
| FR-03 | The Forecast grid defaults to cash measure, weekly grain, 13 ISO weeks from the day after cutover; a partial first week is labelled "(partial)". Column headers carry `W39 · 21 Sep–27 Sep`. | `frame(run, grain=week)` |
| FR-04 | Grain switch (day / week / month / quarter) re-asks the engine; the client never re-sums. In month view the 13-week window is outlined. Day grain shows at most 28 columns; beyond that the cash calendar view takes over. | `frame(grain=…)` |
| FR-05 | Measure toggle Cash / Accrual; the active measure is named in every column header; the two are never mixed in one column. | `frame(measures=…)` |
| FR-06 | Rows: receipts by `tag:customer` (default), disbursements by `tag:cat`, VAT & tax as its own block (`cat:contributions`, `cat:tax`, the `_tax:*` rows). Fixed frame rows: Opening, Receipts, Disbursements, VAT & tax, Net, Closing, Headroom. Every frame row is an engine row: derived items `sub_receipts`, `sub_disbursements`, `sub_vat_tax`, `net_flow`, `headroom = it("cash") − p.min_cash` defined in the book through the SDK, `cash` for Opening/Closing (source map in DATASET.md). Until the derived items exist in a book, group rows read "engine total pending" (D-MLP-62). | `frame()`, `pivot(columns=tag:customer)`, derived items via SDK |
| FR-07 | Status filter actual / committed / forecast. A "committed only" line is a host run on a throwaway overlay with every generative segment stripped (the construction `reconcile()` uses), folded by the engine, stamped `PREVIEW · committed only`, drawn beside the base line, never instead of it. It is not a floor: generated payroll and tax are absent from it. | `run()` on a throwaway overlay |
| FR-08 | Clicking any cell opens the trace panel with formula, bindings and the arithmetic in canonical order (amount → escalation → probability → settlement share → withholding → VAT → cash date with the adjust step) to depth 3, each step a row with its binding and value. | `trace(depth=3)`, R7 |
| FR-09 | Empty cells open `why_zero()` naming one of the five causes and the nearest editable upstream. | `why_zero()`, R8 |
| FR-10 | A cell edit resolves to an Item, Event or param change by backing type: actual → correction only; forecast or committed event → edit in place; generated → choose which input to edit or convert to a point event; derived → read-only with upstream links. Typing into an empty or generated cell creates a forecast event. | ADR-0013; `set_item`, `add_event`, `correct_event`, `set_param` |
| FR-11 | Item editor authors segments (start, end, recurrence, amount constant or schedule, escalation param, probability), settlement legs (share / amount / remainder, literal offset such as `68d`, basis, adjust, withholding) with a live "shares sum to 1" check, VAT spec (rate, treatment, recoverable), tags. Every amount field is labelled "net (excl. VAT)". "Once" creates a forecast event and says so. Stock items aggregate with `last` and never get a sum row. The live preview is a dry run on a throwaway overlay and carries the PREVIEW stamp. Save is a confirmation card. | `set_item`, `add_event`, M1, M2 |
| FR-12 | Shift and scale macros act on a tag selector and list every touched item in the card before Apply. | `apply_macro` (ShiftItems, ScaleItems), M3, M4 |
| FR-13 | The Ledger lists events with date (invoice date for erp rows), amount (net), VAT rate, treatment, recoverable %, settlement (calibrated or the per-event override), cash date, status, item, source, ext_id, tags, note; two date columns for receivables: due (contractual terms) and expected (calibrated). Partial payments (`-P1` actual + `-R` committed) and credit notes (own row, opposite sign, `tags.doc`) render per pilot §3.6. Actuals are correctable only; forecast and committed rows edit in place or void with a note. | `query_events`, `void_event`, M5, M6 |
| FR-14 | A correction requires a note and a 4 dp amount; afterwards both rows show: the original struck through with `CORRECTED · SEE ‹id›`, the correction with `corrects ‹id› · was ‹amount›` and the note. | `correct_event`, M6 |
| FR-15 | Import: choose source (erp:ar, erp:ap, backlog, payroll, bank, commercialista, manual, csv), map columns (saved per source), run the eight pilot gates (completeness, AR tie-out, VAT code coverage, terms coverage, date sanity — incl. no row dated after cutover referencing a generative item —, currency, duplicate ext_id, sign), show the report (inserted / skipped / conflicted), preview the resulting rows, then commit all-or-nothing. A failed gate leaves the ledger untouched and the screen says so. Bank statement and payroll files are staged as match evidence, not imported as rows. | `import_events` |
| FR-16 | Import conflicts (CK-E010) abort the batch by default; the dialog shows existing vs new payload per row and offers `correct_event` for rows the user believes are wrong. | `import_events`, `correct_event` |
| FR-17 | The import target (base ledger; events are book-level and shared by every scenario) is shown before the file is chosen. | D-MLP-73/92, D-MLP-55 |
| FR-18 | Reconcile: window (cutover, statement date]; the tie-out row at the top. Matching targets are committed rows, forecast rows and generated occurrences; proposals by amount ± date window ± counterparty; 1:N and N:1 supported; one card "Confirm ‹n› matches". Writes on confirm: committed row → `void_event(row, note)` + the actual row (ext_id `‹doc›-S`, settlement override = observed offset); generated occurrence → `add_event(actual, item=…)`; unmatched line → `add_event(actual, no item, cat:uncovered)` or a new item. An annotation is the event note or the commit message. A committed row past its date with no line is "expected, not received" and stays committed (empty track in variance, never a zero). | `reconcile(until)`, `void_event`, `add_event`, `import_events` |
| FR-19 | Set cutover is a confirmation card with a mandatory note and the CK-W003 check listing any actual dated after the new cutover. The gate that enables it (exceptions cleared or annotated) is a host check. Applying the card writes `set_cutover`; Save stays the separate act (`commit`). | `set_cutover`, M8 |
| FR-20 | Variance: rows `tag:cat` (receipts also by customer), columns plan, actual, Δ, Δ %, moved (same amount, other period), changed (other amount). Default plan = the revision current at the cutover that opened the window ("plan as of r38 · cutover 21 Jun"); the picker overrides. The plan column is reproduced through `at(rN)`, which runs the engine recorded in that revision; its stamp carries its own triple, and CK-W011 renders beside the table when the engine moved. The actual side counts actual rows only; a committed leg expected in the window and not received shows as moved and an empty track. Materiality threshold: open question 14. A persistent one-sided marker after 3 periods links to the customer's calibration row. | `at()`, `frame()`, `reconcile()` |
| FR-21 | Backtest (Book › Validation): pick `cutover_override` −3 / −6 / −12 months or a revision; run; overlay forecast-then vs actual weekly closing balance with both troughs marked; six metrics (MAPE weeks 1–4 and 5–13, trough timing, trough depth, directional accuracy, coverage) as bullet graphs with target and pass / fail; labelled "not cached · excluded from snapshots"; a cutover before horizon start is "not run". Metrics are host statistics and say so. | `run(cutover_override)`, `at()` |
| FR-22 | Reproduction of the past (Book › Validation, same component): window = last 26 weeks to cutover; columns modelled closing (host run on a throwaway overlay: items + every erp, payroll and manual row, no bank-only rows), bank closing (statement), Δ, Δ % of balance, new discrepancy this week, % of weekly turnover with ▲ above 2 %, explanation (the bank-only rows in the week). The pilot's Week-1 test and criterion 1. | `run()` on a throwaway overlay; statement balances |
| FR-23 | Scenarios: list with parent, note, fork revision, overlay size; fork through a card; activate as app state; compare up to five with fixed rows (horizon close, LOWEST POINT after cutover, first day below floor, days below floor, first negative day, closing at chosen dates, total inflow) and a service-computed Δ column; every non-base column stamped WHAT-IF; LOWEST POINT and floor rows labelled host statistic. | `fork`, `compare`, `diff`, `provenance`, M7, R9 |
| FR-24 | Config diff of an overlay lists items changed (segments atomic, settlement offsets, probability) and params; events never appear (book-level). Apply to base is drawn as withheld with a note until SR-10 exists. | `diff`, `flatten`, `unset` |
| FR-25 | Params: table of key (undotted, `[a-z][a-z0-9_]*`), value (Decimal string), referenced by, last changed revision; edit is a card. Sensitivity sweep picks one item's settlement offset (or a param) and a value list, runs one throwaway scenario per value, shows LOWEST POINT, days below floor and horizon close per value plus small-multiple balance lines, all WHAT-IF stamped. | `set_param`, `set_item`, `dependents_of`, `run`, `compare` |
| FR-26 | Calibration: per customer, contractual terms, settled invoice count, median / p25 / p75 / p90 delay (nearest-rank), modal payment day, trend (last 6 vs previous 6, ▲ when widening by 2 d or more), book offset, proposed offset; segment fallback when count < 12. Adopt writes `set_item(‹every item tagged customer:x›, due[0].offset = "‹n›d")` through a card that lists the touched items. Statistics come from `query_events` on the host and are stamped `host statistic`. A param-referenced offset is SR-1. | `query_events`, `set_item` |
| FR-27 | Tax calendar: the regime as the engine holds it (`iva · monthly · accrual · payment_offset 16d · no surcharge · credit carried`); one row per F24 month with sections Erario IVA (`_tax:iva:liability`), INPS + IRPEF (+ INAIL in February) (`cat:contributions` items), Erario ritenute (NOT MODELLED → CK-W004); the F24 total when the sections share a date; two dates shown, not merged, when the 16th is a weekend (the engine applies no adjust to a tax payment, SR-2). A payment after horizon end is marked "beyond horizon end — in no frame". Trace on a liability cell shows output − recoverable input = payable. The monthly liquidation preview for the month being closed is the month-end artefact for the 10th. VAT credit is the stock `_tax:iva:credit`, never an inflow. | `frame(where item in _tax:*)`, `trace()` |
| FR-28 | Coverage statement (Book › Coverage & sources): checklist of mechanics (VAT, INPS/IRPEF, INAIL, 13th month, ritenuta remittance, IRES/IRAP, acconto IVA, TFR, credits, instalments, declared omissions) marked present / NOT MODELLED / declared from tags, flags and the uncovered rows; validate() list with fix buttons; coverage % from the ledger with the uncovered rows listed; the standing sentence on understatement; sign-off = commit with message. Validity: the chip reads `signed r46 · checklist unchanged` while tax regimes, cat:tax items, manual_tax flags and declared omissions equal those at the signing revision, else `checklist changed since r46 · re-sign`. The source inventory (owner, method, cadence, last run, rows, stale, not covered) sits on the same screen. | `validate()`, `describe_book()`, `commit`, R10 |
| FR-29 | Diagnostics: one renderer everywhere (severity, code in mono, item / field links, message and suggested_fix verbatim from the engine); grouped E / W / I; CK-E blocks compute and shows a banner; app-layer codes use the CK-E9xx band (D-MLP-19: E901 host rule, E902 payload, E903 needs something the book does not have) and render the same. Domain content (which Italian mechanic, which date, a computed remittance) never appears inside a diagnostic row. | `validate()`, D-MLP-19 |
| FR-30 | Alerts: rule kinds `min_cash_below` (day-grain `cash` < `p.min_cash` after cutover) and `source_stale` (a source older than its cadence). Rules live in a host table stamped `app config · unversioned`; `min_cash` and `credit_line` stay plain params. Each fired alert states the facts only (date, figure, threshold, days, back-above date, run stamp) and links to the cell; no recommended action. The historic floor breach is shown in the Position summary as a fact, not as an alert. Evaluation runs in the host after every commit or import, never on the engine clock. Further kinds wait for open question 6. | host over `summary()` / `frame()` |
| FR-31 | History: revisions with short-sha, message, author, time, engine version, diagnostics count; select two → config diff (items, events, params, cutover, horizon) beside outcome diff (horizon close, LOWEST POINT, closing at dates, days below floor); an engine-version move is its own row type; CK-W011 renders whenever `at()` reproduces a revision recorded under an older engine (variance plan, history diff, backtest). | `history()`, `diff_revisions()`, `blame()`, R12 |
| FR-32 | Open at revision: the whole app turns read-only with an `AT ‹sha› · READ ONLY` banner; Reproduce re-asks the engine at that triple and reports byte-identical or lists the delta (string-equal until SR-7). | `at()`, D-MLP-46 |
| FR-33 | Commit = Save with a required message. The header shows the change count from `status()` and a read-only per-change list; Discard reverts the whole overlay to HEAD after a confirm step. There is no per-change revert and no Undo (D-MLP-58): the overlay is the safety net until commit. A Save proposed in an Ask turn is reported as an info diagnostic pointing at the header button, never executed. | `commit`, `status`, `discard`, M9, D-MLP-18/28 |
| FR-34 | Ask and the palette accept the 21 intents. Read intents answer inline as receipts with stamps. Mutation intents (M1–M9) always produce a typed confirmation card with the operation, Assumed badges, dry-run deltas (horizon close before → after, LOWEST POINT, first negative), the SDK call, and "Nothing is saved until you confirm." A question turn can never write; held mutations show in the card, never apply. | ADR-0029, intent schema R1–R12, M1–M9 |
| FR-35 | Cards go stale on any Save, Discard, scenario activation or applied card; a stale card loses its Apply button and offers Refresh; a refused card shows the diagnostics. | D-MLP-16/17/57/65 |
| FR-36 | Exports: xlsx (values only, floats once at the boundary, a provenance sheet, no formulas, no round-trip import), pdf report (A4: position, grid at chosen grain, aging, variance, coverage appendices), parquet; every sheet and page carries the stamp; WHAT-IF exports stamp every page. | `export`, D-MLP-13 |
| FR-37 | Book settings: horizon `[start, end)` shown with the explicit end, opening balance, cutover as cards with dry-run deltas; accounting day 1–28 or end of month with the sentence on what it touches (only a line's start); calendar (fiscal year start, holiday set resolved at creation); categories; the VAT code mapping table (code → rate, treatment, recoverable; an unmapped code is CK-E903 with a fix button); a link to params (edited in D14 only); viewers list. | `set_horizon` (host op), `set_opening_balance` (host op), `set_cutover`, D-MLP-137–143 |
| FR-38 | Cash position: bank statement balance at the statement date (fact) and the book balance beside it (FR-02); available liquidity = bank balance + undrawn credit line (`credit_line` param; a drawn amount is a manual stock item, none in the demo); headroom vs `min_cash`; buffer days with the window stated (trailing 90 days to cutover); next 10 bank days as a running table from the engine's day-grain `cash` with C / G / F glyphs per row, committed and generated never summed into one figure without both visible; receivables expected and payables due this week. | `frame(grain=day)`, `summary()`, params, host statistics |
| FR-39 | Receivables & Payables: tabs AR / AP; buckets current / 1–30 / 31–60 / 61–90 / 90+ on the contractual due date; columns document, counterparty, net, VAT, gross (cash), due (contractual terms), expected (calibrated), expected week, overdue vs terms, bucket, source, ext_id; a row whose statement line is already staged says "on statement ‹date› — confirm in Close"; tie-out line Σ open AR gross vs the AR control total from the last gate. Payables show "due this week"; there is no payment-run view. | `frame(status=committed)`, `query_events` |
| FR-40 | Single-writer lock: on CK-E013 the app shows "Book locked by ‹holder› since ‹time›" and falls back to read-only; CK-W010 on reclaim. | PRD §6.6 |
| FR-41 | Authoring convention for ledger rows (DATASET.md §Authoring convention) is enforced by the import mappings: erp rows dated at the invoice date with net amounts and the line's VatSpec; the settlement override recorded at match time; bank and payroll files as evidence; `-S` / `-P1` / `-R` suffixes. The Ledger shows the convention in a one-line legend. | `import_events`, `add_event` |

## 6. UI requirements

| id | requirement |
|---|---|
| UI-01 | Tokens are the S1b spec exactly: Ink #0A0A0A, Secondary #5C5C5C, Tertiary #767676, Hairline #E2E2E2, Surface #F5F5F5, Paper #FFFFFF, Signal #E4002B, Positive #0A7A3E, Warning #B26B00 (line, badge outline) with #8F5600 for warning text; radius 4 / 3 / 2; 1 px ink borders on controls; no shadows, gradients or blur; Lucide icons 16 / 18 / 20 from `design/desktop/lucide-valid.txt`. |
| UI-02 | Type: IBM Plex Sans for labels and prose; IBM Plex Mono for every figure, date, id, code and stamp. Sizes: 11 tracked uppercase labels and stamps, 12 / 13 table body, 14 default, 16–17 titles, 28–32 KPI figures. Mono Medium for totals. |
| UI-03 | Density: two modes, Comfortable 40 px rows (default) and Compact 32 px, toggled in the status strip; header, toolbar and panel rows follow. Pointer targets stay ≥ 24 × 24 px in Compact (WCAG 2.5.8). |
| UI-04 | Grid: frozen first column (name + small mono id stamp + source of the row) and frozen period header; money and % columns right-aligned including headers; text and ids left; 1 px Hairline horizontal dividers only; frame rows (Receipts, Disbursements, VAT & tax) with a 1 px ink top rule; Net and Closing 600 weight with a double rule; zebra off (on in Compact). A split settlement (Veltro 30/70) is one cell per leg in its own week; the row header carries a split glyph. |
| UI-05 | Cutover: a 2 px ink vertical rule; period columns left of it on Surface, right of it on Paper; columns beyond week 4 shaded one step lighter. **Crossing marker:** every aggregated column (week, month, quarter) whose day-grain minimum is below `min_cash` while its closing is not carries a ◆ marker in the Closing cell and a hatched band in the Headroom cell, sourced from `frame(grain=day)`; hover shows the day and the figure (`◆ € 56,685.00 · 5 Nov`). The demo: Nov 2026 closes at `€ 231,805.90`, dips to `€ 56,685.00` on 5 Nov. |
| UI-06 | Cell backing glyphs: A actual (lock on hover), C committed and F forecast (both with a 1 px ink underline = editable in place), G generated, Σ frame row, ƒ derived. |
| UI-07 | Numbers: always 2 dp; thousands `,`; flows signed `+ € 31,720.00` / `− € 5,856.00` (U+2212); balances unsigned unless negative; percentages `−11.6 %` (U+2212). The currency sits in the column header in all-money grids; cells carry `€` in mixed tables (ledger, receipts, cards) so a figure quoted alone still names its unit. |
| UI-08 | Zero and empty: computed zero `—`; blank on Surface for a period no segment covers; hatched for a cell masked by cutover or scenario; an empty track (no bar, no figure) for a reconciliation line with no actual (D-MLP-63). All open why_zero. `0.00` never appears in a money cell. |
| UI-09 | Dates: `27 Nov 2026` in tables and stamps; `27 Nov` where the header carries the year; ISO weeks `W47 · 16 Nov–22 Nov`, Monday start, in every week header and stamp; a partial first column says "(partial)"; month-end lines say "each month end"; the horizon is shown half-open with the explicit end, `[2026-01-01, 2027-07-01) — last day in frame 30 Jun 2027`. |
| UI-10 | Colour semantics: one Signal red, for the minimum marker, blocking errors and the primary text action; Positive only on figures that already carry `+`; Warning for threshold lines and WHAT-IF; colour never the only cue. A colour-vision setting swaps Positive to #0072B2. |
| UI-11 | Contrast: text ≥ 4.5:1, chart lines, control borders and focus rings ≥ 3:1 (Tertiary or Ink); Hairline is decorative only; focus ring 2 px ink outside the control. |
| UI-12 | Provenance stamp: a component in Mono 11, +1.2 tracking, uppercase, Tertiary, dot-separated `AS-OF 28 SEP 2026 · R51 · 7C2E19B · BASE · ENGINE 1.4.0 · RET_ACME`, under every KPI, chart, table and card total. A host statistic uses the same component with `HOST STATISTIC · ‹definition›` instead of the engine triple; a dry run uses `PREVIEW · ‹what›`. |
| UI-13 | WHAT-IF stamp: same component with `WHAT-IF · ‹scenario›` in Warning; on every column, chart series, answer card, sweep row and export page computed on a non-base overlay; base figures stay neutral ink. |
| UI-14 | Correction scar: original row struck through in Tertiary with `CORRECTED · SEE ‹id›`; correction row directly beneath, linked by a left bracket glyph, with the was-amount and the note; both keep source, ext_id and revision. |
| UI-15 | Diagnostic row: severity bar (Signal for E, Warning for W, Tertiary for I), plain message first, suggested_fix as a text button, code as a mono tag after the message, links to item / field. Inline on the field it names; also in the Diagnostics panel. |
| UI-16 | Charts: balance line in Ink with light area fill, ZERO line with mono label, threshold line in Warning with dash legend, white ink-stroked dots at marked highs, Signal dot and label at the LOWEST POINT, cutover as a Tertiary vertical rule; axis marks are the series' own figures (high, low, ZERO), never round ticks; scenario series dashed Tertiary with WHAT-IF; a waterfall (opening → receipts by customer → disbursements by cat → VAT & tax → closing) for period detail; stacked in/out bars with net line as a toggle; no pie, gauge, fan or sparkline in this round. |
| UI-17 | Bullet graphs for the six backtest metrics: Ink bar, Tertiary target tick, Surface band, pass / fail in text. |
| UI-18 | Keyboard: the grid is one tab stop with roving focus; arrows move, Home/End row ends, Ctrl+Home/End corners, PageUp/Down scroll, Enter opens the panel, typing a digit on a forecast or committed event edits in place, on any other cell opens the create-event card; Space selects a row, Shift+arrows extend. Single-letter mnemonics T trace, E edit, C correct, F fork, D diff; `Cmd+S` commit; `Cmd+Z` is not bound (no Undo, FR-33); `?` opens the shortcut sheet (D26). Every menu row shows its shortcut in Mono, Tertiary, right-aligned. |
| UI-19 | Command palette: `Cmd+K` everywhere, 640 px, Mono input, scope pills Book › Scenario › Item, sections Top result / Actions / Items / Events / Params / Recent, fuzzy match, usage order, shortcuts beside rows. |
| UI-20 | Right detail panel for all reading and editing of a selected row or cell; modals only for Commit, Apply scenario, Record correction, Import conflict; compare limited to five columns. |
| UI-21 | Confirmation card anatomy (from the mobile card): user words as ink quote when from Ask; a 1 px ink card with the operation, prop rows with hairlines, Assumed badges (Warning outline, #8F5600 text) on inferred fields; mono impact lines; verb buttons named for the act and its count ("Confirm 7 matches"); the note "Nothing is saved until you confirm."; states ready / stale / refused / applied. |
| UI-22 | Loading: full-screen skeletons with row and column shapes for book open; inline spinner in the panel for trace and why_zero; determinate progress bar with counts for import, backtest and sweeps; during recompute figures dim to Tertiary with a "recomputing" stamp and are never blanked. |
| UI-23 | Empty states: heading, one sentence, one primary action, plain tone; empty cells never use an empty-state card. |
| UI-24 | Error states: blocking CK-E in a top banner and on the offending row; the grid stays visible; the last good figures keep their stamp. |
| UI-25 | Read-only mode (viewer, lock, at-revision): a persistent banner naming the reason; write actions hidden, not disabled. |
| UI-26 | Accessibility: WCAG 2.2 AA; every chart has a data-table toggle reusing the grid component; icons carry labels; row and cell state is announced (actual, committed, forecast, generated, corrected, not received). |
| UI-27 | Minimum viewport 1366 × 768 with the right panel as an overlay; three panes side by side from 1440 px; the grid scrolls horizontally inside its own container. Light theme only in this round. |
| UI-28 | Copy: short declarative sentences; the system states what it will not do; buttons are verbs with counts ("Record 3 actuals", "Confirm 7 matches"); section labels tracked uppercase; assumptions labelled, never hidden; no marketing words. |
| UI-29 | Reusable components defined once in the .pen before any screen: Stamp (engine / host statistic / preview / WHAT-IF variants), KPI tile, Grid cell, Table row, Ledger row, Diagnostic row, Confirmation card, Balance chart, Panel header, Status strip, Header, Tie-out row, Bullet graph. |

## 7. Out of scope for this design round (each a decision, not a gap)

- Bank aggregation and open-banking sync (ingestion is ERP or CSV export; a statement CSV import as match evidence is in scope).
- Multi-currency, general ledger, invoicing, dunning, tax filing, e-invoicing.
- Formula authoring in the UI; derived items are read-only with a "defined in SDK" stamp (the frame-row derived items are created through the SDK once per book).
- Advice of any kind, including recommended actions on alerts.
- Multi-entity consolidation; one legal entity and one bank account per book, a book switcher only (several accounts: open question 15).
- Multi-user roles, four-eyes review queue, RBAC (see §8).
- Dark theme.
- Payment execution and payment-run views (payables "due this week" covers the pilot); factoring / anticipo fatture / RiBa presentation and value dates (items and events model them; no facility object); covenant ratios that need EBITDA and a covenant test-date line (the `min_cash` floor is the only line).
- Countdown accuracy (forecast made 1/2/4/8 weeks earlier): needs `at()` on every weekly revision; deferred to the second round with the accuracy panel.
- Per-row sparklines and fan charts.
- XLSX round-trip import (export is values only; imports come from the ERP or CSV mapping).
- Alert kinds beyond `min_cash_below` and `source_stale` (see open question 6).
- Mobile layouts; the mobile app stays as designed in `app.pen`.

## 8. Open questions for Luca

1. **Apply a scenario to base.** No SDK verb writes a fork's overlay into base (SR-10). Draw "Apply to base" as withheld, or define a host op now?
2. **Roles.** ADR-0027 sets one user per book. Are read-only viewer sessions enough for the pilot, or does the design need a proposer / reviewer split?
3. **Authored Item on the wire.** D06 needs the single-item read and blame (SR-6). Design assuming it ships, or design the "not exposed" state as the mobile client does?
4. **Frame rows.** The draft defines them as derived items in the book (`agg(tag=…)`), so every subtotal is an engine row. Alternative: a `group_by` verb (SR-4). Which one ships first?
5. **Reproduce endpoint** (SR-7). Show a Reproduce action with the string-equal stand-in, or hide it until the endpoint exists?
6. **Alert rules.** Reduced to `min_cash_below` and `source_stale` in a host table stamped unversioned. Confirm, or ask for a versioned store (SR-5) and re-admit variance and crossing rules?
7. **Buffer-days window.** Trailing 90 days of outflows to cutover, integer days, bank balance in the numerator. Confirm the one canonical definition.
8. **Number locale.** The pilot customer reads 1.400,00; the S1b spec shows 1,400. Per-book or per-user setting, and which default?
9. **Minus sign spacing.** S1b renders `− € 1,400`; accessibility guidance prefers `−1,400`. Keep the spaced form on desktop and align mobile later, or change now?
10. **Week and day frames on the service** (SR-9). Confirm before the grid is built.
11. **Coverage sign-off storage.** The draft uses the commit message plus a host digest of the checklist for the validity rule. Confirm, or a book flag (SR-12)?
12. **Host statistics boundary.** Calibration percentiles, coverage %, MAPE, buffer days, moved / changed, bank-vs-book Δ are computed server-side and stamped `host statistic`. Confirm the boundary (list in DATASET.md §Non-engine figures).
13. **Match-pair storage.** The draft records a match as the `-S` ext_id suffix plus a note naming the statement line. A link field on Event (SR-11) would make it queryable. Which?
14. **Materiality threshold** for variance and for the tie-out Δ: absolute, % of weekly turnover (pilot uses 2 %), or both? Where stored (param vs host setting)?
15. **Bank accounts per book.** One in this round. Several accounts would need an `account:` tag on events and a per-account tie-out. In or out for the pilot?
16. **Notes on items and segments.** Only events and commits carry notes today. Add a note field on items (SDK change), or keep notes on events and commits?
17. **Plan baseline default.** The draft uses the rolling revision (current at the window's cutover). A locked budget revision is the picker's override. Confirm.
18. **Day-column cap.** 28 day columns at desktop width, then the cash calendar view. Confirm.
19. **Engine version format.** The dataset assumes a semver (1.3.2 → 1.4.0) so History can show a move and CK-W011; `ENGINE_VERSION` is `"1"` today. Adopt semver, or show the string as is?

### Critique points not applied, with the reason

- *Redefine CK-W003 to fire only after the proposed cutover* (practitioner). CK-W003 is an engine diagnostic; the app never redefines engine codes (ADR-0021). Under the authoring convention (erp rows dated at invoice date; statement lines are evidence, not rows) the warning fires only for an actual invoiced after the cutover, which is rare and correct.
- *Strip should read `0 E 2 W 1 I`* (completeness, practitioner). The strip reads whatever `validate()` returns for the dataset; after the convention change no CK-W003 fires, so it is `0 E 1 W 1 I`.
- *Alert rules as versioned book params* (product-patterns research, FR-30 v1). `Book.params` is `dict[str, Decimal]`; a rule is not a scalar. Kept as a host table, stamped unversioned, with SR-5 as the path to versioning.
- *Per-change Revert and Cmd+Z* (product-patterns research). No SDK verb backs it; D-MLP-58 says Discard, not Undo. Reduced to Discard-all plus a read-only change list.
- *dso.* params as the calibration store* (pilot guide §5.4, FR-26 v1). `DueTerm.offset` is a literal Duration and param keys cannot carry dots (CK-E007). Calibration writes `set_item`; the param form is SR-1.
- *One F24 date for IVA and contributions* (practitioner). The engine adds `payment_offset` to the period end with no business-day adjust (`engine/tax.py`); the calendar shows two dates when the 16th is a weekend and files SR-2. Merging them in the UI would print a date the engine did not compute.
- *Coverage around 96–97 %* (practitioner). The rebuilt book lands at 97.83 % with 12 uncovered rows; close enough to keep, and the rows are the ones the Coverage screen exists to list.
- *Reproduction table with every discrepancy explained* (completeness, enterprise-needs). Kept, with the modelled side defined as "items + every ERP/payroll/manual row, no bank-only rows": the ERP GL one-offs (van, dividend, acconto) are in both columns, so they are coverage gaps but not reproduction gaps. 26 of 26 weeks within 3 %.
- *Four-eyes review and roles* (enterprise-needs). Still out of scope (ADR-0027); open question 2.

## 9. SDK and service feature requests the desktop depends on

| id | request | blocks | today |
|---|---|---|---|
| SR-1 | `DueTerm.offset` may reference a param (`p.dso_acme`) | calibration as a param table (D14), param sweeps of DSO | literal `"68d"`; calibration writes `set_item` per customer; sweep = one throwaway scenario per value |
| SR-2 | `TaxRegime.payment_offset` with a day anchor and `adjust` | one F24 date per month (D15, FR-27) | `16d` from month end, no adjust; weekend 16ths shown as two dates |
| SR-3 | `it("_tax:‹id›:liability")` referenceable in a formula (or SR-4) | `sub_vat_tax` as one engine row (FR-06) | the `_tax` frame row shown beside the derived contributions row |
| SR-4 | `group_by` on `frame()` / `reconcile()` (D-MLP-62, R5) | category subtotals without derived items | derived items `agg(tag=…)` created through the SDK |
| SR-5 | alert-rule store versioned with the book | FR-30 versioned rules, more kinds | host table stamped `app config · unversioned` |
| SR-6 | `GET /book/items/{id}` + per-item blame (D-MLP-66/67) | D06 direct segment editing, item provenance | rule reconstructed from `trace()`, revision not attributed |
| SR-7 | `reproduce()` endpoint (D-MLP-46) | FR-32 Reproduce across engine versions | string-equal re-ask |
| SR-8 | `ReconciliationLine.actual` nullable (D-MLP-63) | empty track for unsettled lines (FR-18, FR-20) | ledger read beside the report |
| SR-9 | week and day frames on the service (D-MLP-43) | D03 grid, D02 next 10 bank days | monthly payloads only |
| SR-10 | Apply a scenario's overlay to base | FR-24 Apply to base | withheld with a note |
| SR-11 | match link field on Event (or the `-S` / `tags.doc` convention as canon) | FR-18 match storage, open question 13 | ext_id suffix + note |
| SR-12 | coverage sign-off storage (flag or table) | FR-28 validity rule | commit message + host checklist digest |

## 10. Sources

Repo files: `PRD-cashkit.md` §1, §2, §4, §6, §7, §10 · `ERP-pilot-guide.md` §1–§11 · `km/adr/0012`, `0013`, `0021`, `0022`, `0023`, `0024`, `0027`, `0029` · `km/notes/intent-schema-draft.md` · `DECISIONS.md` App track D-MLP-05, 06, 13–19, 23–28, 41–46, 50, 53–58, 60–70, 73–75, 87–95, 137–143 · `cashkit/model/primitives.py`, `settlement.py`, `tax.py`, `diagnostics.py`, `reports.py` · `cashkit/engine/tax.py`, `numeric.py`, `formula.py`, `facts.py`, `__init__.py` · `design/style-explorations/s1b-swiss-ledger-soft-corners/*.png` · `design/desktop/lucide-valid.txt` · `design/desktop/DATASET.md` · `design/desktop/dataset.py`.

Practice and product research (read 2026-09-26; items marked † were reached through search abstracts only):

- https://www.financialprofessionals.org/topics/treasury/cashforecasting
- https://www.jpmorgan.com/insights/treasury/forecasting-planning/cash-forecasting-tips-for-your-business
- https://www.jpmorgan.com/insights/treasury/receivables/dso-and-dpo-how-they-can-improve-your-cash-flow
- https://www.jpmorganchase.com/institute/all-topics/business-growth-and-entrepreneurship/report-cash-flows-balances-and-buffer-days
- https://treasury.ripple.com/posts/set-up-13-week-cash-flow-forecast · /cash-flow-forecasting-best-practices · /what-is-13-week-cash-flow-forecasting · /top-methods-of-measuring-cash-forecasting-accuracy · /covenant-forecasting
- https://www.atlar.com/learn/what-is-the-13-week-cash-flow-forecast
- https://www.wallstreetprep.com/knowledge/demystifying-the-13-week-cash-flow-model-in-excel/
- https://www.stampli.com/resources/13-week-cash-flow-forecast/ · /ap-payment-runs/
- https://erp.intuit.com/blog/financials/cash-position-reporting/
- https://umbrex.com/resources/chief-financial-officer-handbook/treasury-cash-management-checklist/
- https://www.nilus.com/blog/covenant-compliance-monitoring-the-pe-backed-cfos-complete-playbook/
- https://www.deloitte.com/us/en/services/consulting/articles/working-capital-management-report.html
- https://www.equilityhq.com/blog/month-end-bank-reconciliation-control-driven-workflow
- https://docs.causal.app/charts-and-dashboards/comparing-versions-and-scenarios-on-visuals
- https://docs.runway.com/concepts/scenarios · https://runway.com/product-updates/lock-scenarios-for-bva-analysis
- https://help-center.trovata.io/en/articles/9648869-how-to-forecast-cash-in-trovata
- https://www.kyriba.com/resources/fact-sheets/unified-worksheet-liquidity-planning-fact-sheet/
- https://support.fathomhq.com/en/articles/4602989-the-forecast-main-grid
- https://help.planful.com/v1/docs/drill-through-grid-interaction-in-spotlight
- http://help.jirav.com/reporting/variance-analysis
- https://www.tesorio.com/product/forecasting
- https://agicap.com/en-us/article/erp-bank-reconciliation-automation/
- https://www.creditpulse.com/blog/ar-aging-report-guide
- https://docs.findock.com/docs/reconciliation/processing-camt-053-files
- G2 / Capterra / TrustRadius reviews: Kyriba, Planful, HighRadius, Fathom, Agicap, Float, Trovata
- https://www.nngroup.com/articles/data-tables/ · /confirmation-dialog/ · /error-message-guidelines/ · /skeleton-screens/ · /flexibility-efficiency-heuristic/
- https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html · /non-text-contrast.html · https://www.w3.org/WAI/ARIA/apg/patterns/grid/
- https://carbondesignsystem.com/components/data-table/usage/ · https://learn.microsoft.com/en-us/windows/apps/design/style/spacing · https://m2.material.io/develop/web/supporting/density
- https://cloudscape.design/patterns/resource-management/view/split-view/ · https://atlassian.design/components/modal-dialog · https://polaris.shopify.com/components/layout-and-structure/empty-state
- https://www.deque.com/blog/ensuring-negative-numbers-are-available-for-everyone/ · https://www.datawrapper.de/blog/colorblindness-part2
- https://blog.superhuman.com/how-to-build-a-remarkable-command-palette/ · https://retool.com/blog/designing-the-command-palette
- https://www.perceptualedge.com/articles/Whitepapers/Communicating_Numbers.pdf · https://en.wikipedia.org/wiki/Bullet_graph
- https://inforiver.com/insights/waterfall-charts-finance-professionals-best-friend/ · https://www.bis.org/ifc/events/ifc_8thconf/ifc_8thconf_62pap.pdf
- https://www.hubifi.com/blog/immutable-audit-log-basics · https://community.sap.com/t5/financial-management-blog-posts-by-sap/4-eyes-principle-core-principles-for-effective-supervision-in-finance/ba-p/13556965
- https://sibill.com/strumenti/scadenze-fiscali/ · https://4planning.it/factoring-vs-anticipo-fatture-gestione-contabile-e-finanziaria/
- https://www.ibm.com/plex/
- † https://bprglobal.co/resources/financial-planning-analysis/13-week-cash-flow-forecasting-guide/ · † Accordion 13-week guide · † HighRadius variance guidebook · † CTMfile · † GFOA
