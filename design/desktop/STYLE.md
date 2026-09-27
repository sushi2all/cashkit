# CashKit desktop — style spec

**Direction** Swiss Ledger Desk. Judges' totals: Swiss Ledger Desk 123 · Trading Desk 95 · Calm Fintech Grid 87.
**Date** 2026-09-26 · **Status** final for drawing · **Parent** S1b Swiss Ledger Soft Corners (mobile, `app.pen`)
**Scope** Enterprise / SME cash management on the CashKit engine. Users: controller, CFO, treasurer, founder. Reference customer: the ERP pilot (`ERP-pilot-guide.md`).
**Demo book** Delta Ingegneria 2026 (`design/desktop/DATASET.md`, printed by `dataset.py`). Every figure in this spec is a row of that file. Cutover 20 Sep 2026 · statement to 25 Sep 2026 · as-of 28 Sep 2026 · revision r51 · 7c2e19b · engine 1.4.0 (assumed semver, see DATASET.md) · `p.min_cash` € 60 000.00.

Grafts from the runners-up are folded in and marked `[graft B]` (Trading Desk) or `[graft C]` (Calm Fintech Grid). No graft adds a colour, a shadow, a tint or a second typeface. Where a graft and the thesis disagreed, the thesis won and the graft is listed in §9 as rejected.

---

## 1. Thesis

The receipt is the identity. On desktop the receipt is the grid.

The person who buys this product signs the pilot's coverage statement. That person trusts a page that looks like an audited ledger: a strict grid, one typeface family, every figure in a monospace column, and under every number a stamp that names the revision that produced it. That is ADR-0023's computed-receipt language at desktop scale.

The style is International Typographic Style. It fits the engine's own claims: objective, content-free, no ornament. Meaning is carried by position, weight and type, not by hue. Colour is reserved for meaning:

- Signal red, one meaning: danger. The lowest point below `p.min_cash`, a Closing below the floor, negative Headroom, a CK-E diagnostic, the bell dot.
- Positive green only on a figure that already carries `+`.
- Warning amber only on the `p.min_cash` line, Assumed badges, WHAT-IF tags and CK-W bars.

Everything else is ink on paper.

Two deliberate breaks from the mobile frames, both taken from the runners-up:

1. **Save is an Ink primary button** `[graft B]`. On mobile Save is the one red text action. On a desktop page that already shows a red lowest point and red diagnostics, a red Save reads as a warning. Signal therefore carries one meaning on desktop. The mobile header stays as it is. The break is recorded in `DECISIONS.md` (§11, item 1).
2. **Dotted leaders in the Trace receipt only** `[graft C]`. ADR-0023 named serif numerals and dotted leaders; the shipped S1b frames replaced both with Plex Mono and hairlines, and `app.pen` contains no leaders. This spec follows S1b everywhere except the receipt, which gets its leaders back. The line is clean: leaders in the receipt, hairlines in lists. ADR-0023 is amended to say so (§11, item 2).

Continuity with the mobile app is otherwise literal: same tokens, same ledger row, same chart grammar, same confirmation card, same stamp component. Two apps, one product, one engine.

---

## 2. Tokens

One token file. Every colour rule is stated as a contrast requirement (text 4.5:1, non-text 3:1), so a print-safe export set could be added later without redrawing a component.

### 2.1 Colour

| Name | Hex | Use |
|---|---|---|
| Paper | `#FFFFFF` | Page ground, cards, popovers, modals, forecast columns, primary-button text |
| Surface | `#F5F5F5` | Actual columns (left of cutover), chart area fill, selected-range fill, icon tiles, bullet-graph band, empty-outside-segment cells |
| Hairline | `#E2E2E2` | Row dividers, card edges, chart connectors. Decorative only (1.30:1). Never the only boundary of a control |
| Ink | `#0A0A0A` | Text, figures, control borders, primary-button fill, active nav fill, cutover divider, balance line, focus ring, corner mark |
| Ink-2 | `#5C5C5C` | Secondary text, notes, every stamp on every ground, inflow bars, scenario dashed line, hero label |
| Ink-3 | `#767676` | Marks and glyphs, not prose: `—` exact zero, hatch, row glyphs, certainty dots, axis ticks, receipt leader dots, shortcut hints on Paper, struck-through amounts. As text it sits on Paper only |
| Muted-on-ink | `#A8A8A8` | Secondary text and shortcut hints on an Ink fill (active nav row, primary button) |
| Signal | `#E4002B` | Danger only. Text on Paper only; never a fill behind text except the 8 px bell dot |
| Positive | `#0A7A3E` | A figure that already carries `+`; favourable variance above threshold; waterfall receipts bars |
| Positive-cvd | `#0072B2` | Replaces Positive in colour-vision mode |
| Warning | `#B26B00` | Lines, outlines and bars only: `p.min_cash` dashed line, Assumed badge outline, WHAT-IF tag outline, CK-W bar. Never text |
| Warning-text | `#8F5600` | Text inside a Warning outline: Assumed badge text, WHAT-IF tag text, CK-W severity word |

Zebra `#FAFAFA` from the candidate is removed `[graft B]`. At 1.04:1 against Paper it is invisible, and it collided with the Surface actual-column cue. Rows are separated by hairlines and the 8 pt rhythm only, in every density.

### 2.2 Fonts

| Role | Family | Weights | Fallback |
|---|---|---|---|
| UI | IBM Plex Sans | 400, 500, 600 | `-apple-system, "Segoe UI", sans-serif` |
| Figure | IBM Plex Mono | 400, 500 | `"SF Mono", Consolas, monospace` |

Source: Google Fonts. No third family. Every money figure, percentage, date, id, ext_id, code and stamp is Plex Mono. Plex Sans is for labels and prose. Plex Mono's digit advance is 0.600 em: 7.2 px at 12 px, 6.6 px at 11 px. All column arithmetic below uses those two numbers.

### 2.3 Radii

| Token | px | Use |
|---|---|---|
| box | 4 | Cards, inputs, buttons, popovers, modals, KPI tiles, active nav fill |
| tab | 3 | Inspector tabs, segmented-control segments |
| tag | 2 | Tags, chips, badges, WHAT-IF tag, code tag |
| cell | 0 | Grid and table cells |

### 2.4 Spacing

8 pt rhythm. Scale: 4, 8, 12, 16, 24, 32, 48, 64. Content padding 24 px on non-grid pages. The grid runs full-bleed to the centre-pane edges. Cell side padding: 8 px (Comfortable, Compact), 4 px (Dense).

### 2.5 Type scale

| Role | Family | Size | Weight | Tracking |
|---|---|---|---|---|
| page title | Sans | 20 | 600 | −0.2 |
| section title | Sans | 15 | 600 | 0 |
| label (uppercase eyebrow, column group, form label) | Sans | 11 | 600 | +1.2 |
| body | Sans | 13 | 400 | 0 |
| body secondary (notes, Why sentences) | Sans | 12 | 400 | 0 |
| button | Sans | 13 | 600 | 0 |
| figure-hero (KPI tile) | Mono | 32 | 400 | −0.5 |
| figure-hero delta | Mono | 14 | 400 | 0 |
| figure-table Comfortable | Mono | 12 | 400 | 0 |
| figure-table Compact and Dense | Mono | 11 | 400 | 0 |
| figure-total (subtotal, Net, Closing, Headroom) | Mono | 12 / 11 | 500 | 0 |
| receipt line (Trace) | Mono | 12 | 400 | 0 |
| stamp (uppercase words, dot-separated) | Mono | 11 | 400 | +1.2 |
| id / code / shortcut hint | Mono | 11 | 400 | 0 |
| date (`dd Mon yyyy`) | Mono | 12 | 400 | 0 |
| chart axis mark | Mono | 11 | 400 | 0 |

---

## 3. Rules

1. **Figures.** Every money, percentage, date, id, code and stamp is Plex Mono. Money and percent columns are right-aligned, header included. Text, dates, ids and tags are left-aligned. A figure inside a Sans sentence is set in Mono inline.
2. **Digits.** The client renders the service's 2 dp `display` string in full. Cents are never dropped (D-MLP-06, D-MLP-41). Grouping is a thin space U+2009, never a comma `[graft C]`: `124 519.10`. Decimal is a point. `DATASET.md` prints commas because it is a Markdown table; the drawing converts them to thin spaces by string replacement and touches no digit.
3. **Currency.** The symbol appears once in a column header (`Cash · €`) or a tile label, never in a grid cell. KPI heroes, ledger rows, receipts and confirmation cards carry the symbol with a space: `€ 126 310.40`.
4. **Sign.** Unicode minus U+2212. In a grid cell directly before the digits: `−52 000.00`. Where the symbol is present, the mobile spacing: `− € 5 856.00`, `+ € 31 720.00`. Flow rows are signed in ledger and list rows; in the grid, outflow rows carry `−` and inflow rows are unsigned. Balances are unsigned unless negative. Colour is never the only cue. "Accounting style (parentheses)" is a per-user display preference.
5. **Empty cells.** `—` in Ink-3 for a computed exact zero. Blank on Surface for a period outside every segment or outside the horizon. 45° hatch, 1 px Ink-3 lines on Surface at 6 px pitch, for a cell masked by cutover or by the scenario. `0` never appears in a money cell. All three open `why_zero()` on click or Enter, and the reason is printed with its number (`(4) generation suppressed by cutover: the ledger row ev-0821 carries August`).
6. **Borders.** 1 px Ink on every control. 1 px Hairline for row dividers and card edges. No vertical grid rules except the 2 px Ink cutover divider and the 1 px Ink right edge of the frozen block. Group footers get a 1 px Ink top rule. Net cash flow gets a 1 px Ink top rule. Closing balance gets a double rule (two 1 px Ink lines, 2 px apart). No shadows, no gradients, no tinted banners. Popovers, the palette and modals are Paper with a 1 px Ink border.
7. **Selection and focus.** Focused cell: 2 px Ink outline inset. Selected range: Surface fill plus a 1 px Ink outline around the range (on actual columns the outline is the cue). Selected row: 2 px Ink bar on the left edge of the name column. Focus on any control: 2 px Ink ring 2 px outside. Never colour.
8. **WHAT-IF (ADR-0024).** Any figure, column, tile, series or answer computed on a fork carries the tag `WHAT-IF · downside · r51`: Mono 11 uppercase words, Warning-text on Paper inside a 1 px Warning outline, r2. Base figures carry `BASE · r51` in Ink-2. WHAT-IF cells get no tint; the tag is the whole signal. The tag comes from that payload's own `what_if` field (D-MLP-54), never from client logic. Scenario columns sit in their own column group and never interleave with base columns. Only Apply removes a tag.
9. **Stamp.** Mono 11, +1.2 tracking, dot-separated: `AS OF 28 SEP 2026 · r51 · 7c2e19b · BASE · ENGINE 1.4.0 · ret_acme`. Words are uppercased by the component; revision, sha and ids are printed as the engine gives them, because uppercasing an id changes it. Colour is Ink-2 on every ground (Ink-3 passes on Paper by 0.04 and fails on Surface; one colour, no swap). One stamp under every KPI tile, chart, table, card, palette answer and inspector receipt. While figures are stale the stamp appends ` · RECOMPUTING` and the figures dim to Ink-3; the grid is never blanked `[graft C]`. The word is the cue, not the grey.
10. **No client sums.** Every subtotal, Net, Closing and Headroom figure is an engine figure. A group the service does not total shows `NO SUBTOTAL · THE ENGINE COMPUTES PER ITEM` in the stamp voice in its footer cell, never a client sum (D-MLP-62) `[graft C]`. Chart axis marks are the series' own high, low, ZERO and end figures (D-MLP-141).
11. **Signal red.** Text on Paper only (4.85). Never on Surface (4.44 fails): a negative actual on a shaded column is Ink with its minus. Signal is never a fill behind text; the 8 px bell dot is the only fill. Signal never marks an action. No red button and no red text action exist on desktop.
12. **Save and destructive acts** `[graft B, C]`. Save is an Ink primary button in the header (`Cmd+S`); it opens the Commit modal whose confirming verb is `Commit as r42`. Destructive verbs are Ink primary buttons named by verb and count (`Void 3 events`), never red. An empty commit message is refused with an inline diagnostic under the field (`A commit needs a message.`), not with a disabled button.
13. **Uncommitted changes** `[graft C]`. A cell whose value comes from an uncommitted overlay change carries a 2 px Ink corner mark at its top-left. The header count `3 uncommitted changes` opens a list with the cell, the SDK call and a Revert per row; list and corner marks share one source, `status()`.
14. **Cutover.** Actual columns sit on Surface with an `A` tag in the period header; forecast columns on Paper with `F`. The period that contains the cutover is split at the cutover into two columns, both marked `(partial)`; the demo cutover is a Sunday (20 Sep), so `W38 · A · 14 Sep–20 Sep` and `W39 · F · 21 Sep–27 Sep` are whole weeks and no partial column appears. The 2 px Ink divider stands between them and carries `CUTOVER · 20 SEP 2026` in the header. On the Forecast grid the first forecast column is pinned next to the name column, so the divider and its stamp never leave the screen when the grid scrolls `[graft B]`.
15. **Row and cell glyphs** `[graft B, C]`. The name column starts with a 12 px Mono glyph in Ink-3 naming the row type: `I` generated from an item, `E` event row, `Σ` subtotal, `ƒ` formula (read-only). Certainty is per cell, not per row: a 6 px dot in Ink-3 at the cell's left edge, filled = actual (`A`), half = committed (`C`), outline = forecast (`F`), none = generated (`I`). The same letters appear as status tags in the ledger and in the inspector, so the set is `A · C · F · I · Σ · ƒ` and a committed ERP invoice is told apart from a guess without opening the inspector. The toolbar carries the legend. Aggregated cells (week, month) open trace with the list of day cells; edits resolve to the Item, Event or param, never to the aggregate (ADR-0013).
16. **Keyboard** `[graft B, C]`. The grid is one tab stop with roving focus. Single letters inside the grid: `T` trace, `E` edit, `C` correct, `F` fork, `D` diff, `Z` why_zero, `?` shortcut sheet. `Cmd+S` save, `Cmd+K` palette, `Cmd+[` sidebar, `Cmd+]` inspector. `Cmd+Z` is not bound: there is no Undo, only Discard (D-MLP-58, FR-33). Every menu row, context-menu row, palette row and inspector action prints its shortcut in Mono 11 right-aligned: Ink-3 on Paper, Muted-on-ink on Ink.
17. **Diagnostics.** 3 px left bar (Signal CK-E, Warning CK-W, Ink-3 CK-I). Order: severity word, plain message Sans 13, `suggested_fix` as a secondary button, the code as a Mono 11 outline tag. CK-E also shows a top banner (Paper, 1 px Ink border, 3 px Signal left bar) and blocks compute; the grid stays visible with its last good stamp. CK-W sits inline on the row it names. CK-I only in the Diagnostics list. A code is printed only when the engine raised it.
18. **Correction scar (ADR-0012, D-MLP-61).** The original actual stays in place, amount struck through (1 px line-through) in Ink-3, stamp `CORRECTED · SEE ev-0812`. The correction row sits directly beneath, joined by a 2 px Ink left bracket, and reads `was − € 5 586.00 · note: "Digits transposed in the CSV import. Statement line 14/08 shows 5,856.00."` in Sans 12 Ink-2. Both rows keep source and ext_id (`bank:intesa · 2026-08-14-0031` and `…-0031-c1`). Nothing is deleted from the ledger view.
19. **Density** `[graft B]`. Three tiers in the status bar. Comfortable = 40 px rows, Mono 12 (default for CFO and founder). Compact = 32 px rows, Mono 11 (default for treasurer). Dense = 28 px rows, Mono 11, 4 px cell padding, 192 px name column (default for controller). Header, toolbar and inspector rows follow the tier. Sparklines are Comfortable-only. Inline icon buttons stay 24 × 24 px in every tier (WCAG 2.5.8); chips and tags stay ≥ 24 px tall. Hairlines are the only row separator in every tier.
20. **Colour-vision mode (Settings).** Swaps Positive for Positive-cvd on figures and chart marks. Signal and Warning stay. Chart series are told apart by dash pattern and direct label, never by hue.
21. **Copy.** Short declarative sentences. Tracked uppercase section labels. Every write control names the verb and the row count (`Record 3 actuals`). Every write path ends in a confirmation card with the SDK call printed and the line `Nothing is saved until you confirm.` A question turn can never write (ADR-0029). No marketing words.
22. **Token-swap lint.** Five context rules, and only five, are what a build must enforce: (a) no Ink-3 text on Surface; (b) no Signal text on Surface; (c) Warning is never text, Warning-text is never a line; (d) stamps are Ink-2 everywhere; (e) hints on an Ink fill are Muted-on-ink. Anything else that needs a swap is a design defect, not a sixth rule.

---

## 4. Shell spec (1440 × 900)

Three-pane shell on Paper under a full-width header. Icons Lucide (names from `design/desktop/lucide-valid.txt`): 16 px in rows, 18 px in the header. Controls 32 px tall (28 px in Compact and Dense). Targets ≥ 24 px. Designed at 1440 and 1920; supported down to 1280 with the rail collapsed and the inspector as an overlay.

### 4.1 Header (48 px, full width)

Paper, 1 px Hairline bottom. Left to right, 8 px gaps:

- **Book switcher** 32 px, 1 px Ink outline r4, `book` icon, `Delta Ingegneria 2026` Sans 14/600, `chevron-down`. Lists books with currency and horizon. The same control as mobile.
- **Page title** Sans 20/600.
- **Scenario switcher** 32 px outlined: `BASE · PLAN OF RECORD` in Ink-2 stamp style, or the WHAT-IF tag `WHAT-IF · downside · r51`. When the working scenario is a fork and the page shows base figures, the tag is followed by `WORKING IN downside · FIGURES ARE BASE`.
- **Provenance stamp** `AS OF 28 SEP 2026 · CUTOVER 20 SEP 2026 · r51 · 7c2e19b` Mono 11 Ink-2. The header is the one place the currency is named: `EUR`.
- Right group: `3 uncommitted changes` text action (Ink, underlined on hover; opens the change list) · **Save** Ink primary button 72 × 32 px (`Cmd+S`) · `Discard` secondary button · `Cmd+K` palette button (1 px Ink outline, 32 px, `command` icon) · `bell` 32 px outlined square with an 8 px Signal dot when a CK-E exists or an alert fired.

Width check at 1440: switcher 200 + title 160 + scenario 220 + stamp 340 (≈ 50 chars × 6.6) + right group 96 + 72 + 80 + 80 + 32 + 9 gaps × 8 = 1352 px, inside 1440 − 2 × 24 padding = 1392.

### 4.2 Left sidebar

- 208 px expanded; 48 px icon rail collapsed (`Cmd+[`). Paper, 1 px Hairline right edge.
- Nine entries, 32 px tall, 8 px inset, Sans 13/500 Ink, 16 px icon, 8 px gap, shortcut hint Mono 11 at the right (Ink-3; Muted-on-ink on the active row). Active = Ink fill r4, Paper text.

| # | Entry | Icon | Hint |
|---|---|---|---|
| 1 | Overview | `layout-dashboard` | `G O` |
| 2 | Forecast | `table-2` | `G F` |
| 3 | Ledger | `receipt` | `G L` |
| 4 | Items | `list` | `G I` |
| 5 | Scenarios | `git-branch` | `G S` |
| 6 | Variance | `git-compare` | `G V` |
| 7 | Import | `upload` | `G M` |
| 8 | History | `history` | `G H` |
| 9 | Book | `settings` | `G B` |

- Count tags: Ledger (unmatched rows), History (uncommitted changes). Mono 11 inside a 1 px Ink outline tag r2. A blocking count adds an 8 px Signal dot, never red text.
- Bottom of the sidebar: `Diagnostics · 2 W 1 I` entry (the demo book raises CK-W004, CK-W003, CK-I001) with the same tag, opening the Diagnostics list.

### 4.3 Page toolbar (40 px)

Under the header on grid and table pages. Left to right: grain segmented control (Day | Week | Month | Quarter), measure segmented control (Cash | Accrual | VAT), scenario select, group-by select (cat, customer, cost_center, owner), filter chips (status, `cat:`, `customer:`), `Hide empty rows`, `Show ids`, glyph legend (`● A · ◐ C · ○ F · I generated`). Right end: `Weeks 1–10 of 13` Mono 11 and a `Fit` toggle that collapses the sidebar and switches to Dense.

### 4.4 Centre pane

Fluid, minimum 720 px. The grid or a table. Tables use the mobile ledger row at 40 / 32 / 28 px with hairline dividers and a 1 px Ink rule under the header. Non-grid pages have 24 px content padding.

### 4.5 Right inspector

- 440 px default, resizable 400–480, collapsible (`Cmd+]`). Paper, 1 px Hairline left edge. Below 1280 px it becomes an overlay with a 1 px Ink left border.
- Tabs r3: Trace, Edit, History, Diagnostics, Ask. A pending confirmation card docks at the bottom of the inspector; one pending card per screen (D-MLP-65).
- **Trace renders the receipt** `[graft C]`: the formula line; the bindings; one row per factor in canonical order, binding name on the left, Mono 12 figure on the right, joined by dotted leaders (1 px Ink-3 dots, 3 px pitch); a 1 px Ink-3 rule above the result; the result in Mono 12/500; contributing items and events with ids as links; then the ADR-0013 actions as buttons (Edit amount / Record a correction / Create forecast event / Open upstream). Leaders appear here and nowhere else in the app.

```
trace · ret_acme · Apr 2027 · cash

segment amount · segments[1] · net ...... € 60 000.00
× escalation · p.istat_index 0.02 ^1 .... € 61 200.00
× probability · 1 ....................... € 61 200.00
× share · 1.0 @ due[0].offset "68d" ..... € 61 200.00
− withholding · 0 ....................... € 61 200.00
+ VAT · p.vat_standard 0.22 ............. € 13 464.00
→ cash date · 31 Jan 2027 + 68 d = 9 Apr 2027 (Fri) → adjust next → 9 Apr 2027
──────────────────────────────────────────────────────
= cash leg ..............................  € 74 664.00

AS OF 28 SEP 2026 · r51 · 7c2e19b · BASE · ENGINE 1.4.0 · ret_acme
```

- `why_zero()` text replaces the receipt for empty cells. A spinner sits inline in the inspector only.
- **Ask** is the inspector tab and the `Cmd+K` palette: 640 px wide, Paper, 1 px Ink border, no shadow. Mono input, scope pills Book › Scenario › Item, sections Top result / Actions / Items / Events / Params / Recent, fuzzy match, shortcut hint on every row. The 12 read intents render a receipt inline. The 9 mutation intents always end in the confirmation card with the SDK call printed (ADR-0029).

### 4.6 Status bar (28 px)

Paper, 1 px Hairline top. Left: source freshness in stamp style, names not colours: `erp:ar 28 Sep 07:10 · 7 rows · erp:ap 28 Sep 07:10 · 10 rows · bank:intesa 25 Sep 18:30 · 19 lines · payroll:zucchetti 24 Sep`. Centre: `coverage 97.83 % · signed r46` (host statistic, labelled). Right: density segmented control (Comfortable | Compact | Dense), colour-vision toggle, diagnostics count with severity dot, `ENGINE 1.4.0`, recompute state (`RECOMPUTING` while stale figures dim to Ink-3).

### 4.7 Modals

Only for the four acts the requirements list: Commit, Apply to Base, Record correction, Import conflict. 560 px, Paper, 1 px Ink border, Ink at 40 % behind. Outcome diff preview first, then the input, then verb buttons. Apply to Base for `downside`, from `DATASET.md`:

```
Horizon close 30 Jun 2027   € 313 129.08  →  − € 88 334.92
LOWEST POINT                € 56 685.00 · 5 Nov 2026  →  − € 119 030.62 · 30 Mar 2027
First day below min_cash    4 Nov 2026  →  25 Sep 2026
First negative day          not within horizon  →  16 Oct 2026
Config (items only): ret_acme, ret_borghi, support_misc × 0.85 from 1 Sep 2026, offsets +30 d · ms_comune, ms_veltro schedule × 0.85, ms_comune offset +30 d · pipe_nord probability 0.6 → 0

[ Apply downside to Base ]  [ Keep as scenario ]
Nothing is saved until you confirm.
```

### 4.8 Pixel budget at 1440 × 900

| Element | px |
|---|---|
| Header | 48 |
| Sidebar | 208 (rail 48) |
| Toolbar | 40 |
| Status bar | 28 |
| Inspector | 440 (closed by default on Forecast at 1440) |
| Grid height | 900 − 48 − 40 − 28 = 784; minus 48 period header and 36 footer stamp row = 700 |
| Rows visible | 17 Comfortable (40) · 21 Compact (32) · 25 Dense (28) |

### 4.9 Reconciliation with `REQUIREMENTS.md`

The requirements were written before the style was judged. Where they differ, this spec wins and the delta is listed here for the parent:

- UI-04 zebra in Compact: dropped (§2.1). UI-05 lighter shading beyond week 4: dropped; a fourth grey has no contrast room and confidence is a per-cell glyph, not a tint. UI-06 `G` for generated: `I` per the judges' set. UI-07 comma grouping: thin space per D-MLP-41. UI-10 Signal for the primary text action: removed on desktop. UI-12 stamps in Tertiary: Ink-2 (rule 22 d). §3.1 rail 56 / 200 px: 48 / 208 to keep the 8 pt grid. §3.4 status strip under the header: its content lives in the 28 px status bar at the bottom; the 40 px slot under the header is the page toolbar.

---

## 5. Grid spec

### 5.1 Frame

One grid, four grains (Day, Week, Month, Quarter) sharing the same rows. Default = Cash measure, Week grain, the pinned first forecast column plus 12 more = the 13-week window from cutover (`W39 · F · 21 Sep–27 Sep` to `W51 · 14 Dec–20 Dec`; the cutover is a Sunday, so no partial column).

- Frozen top: 48 px period header, two lines: Sans 11 tracked `W40 · F` over Mono 11 `28 Sep–4 Oct`. Surface for `A`, Paper for `F`. The year is in the toolbar, so dates inside columns omit it (UI-09).
- Frozen left block: the name column (232 / 208 / 192 px), then the 2 px Ink cutover divider with `CUTOVER · 20 SEP 2026` in the header, then the pinned first forecast column `[graft B]`. The block has a 1 px Ink right edge; horizontal scroll starts after it. Actual periods are not on the Forecast page; `◀ Actuals` in the pinned header jumps to Variance for the same rows.
- Name column contents: 20 px glyph gutter (`I` / `E` / `Σ` / `ƒ`, Ink-3) + name Sans 13/500 + optional 60 × 16 px Ink sparkline (Comfortable only) + tag chips; item id as a Mono 11 Ink-2 stamp under the name (`ret_acme`).
- Rows: 40 / 32 / 28 px, 1 px Hairline dividers, no vertical rules.

### 5.2 Fixed frame rows (all engine figures)

| Row | Weight | Rule | Colour rule |
|---|---|---|---|
| Opening balance | Mono 500 | 1 px Ink bottom | Ink |
| Receipts by `tag:customer` (collapsible groups; `pivot(columns=tag:customer)`) | Mono 400; footer 500 (`sub_receipts`) | footer 1 px Ink top | Ink, `+` in ledger only |
| Disbursements by `tag:cat` (footer `sub_disbursements`) | same | same | Ink, signed |
| VAT & tax (`cat:contributions`, `cat:tax`, the `_tax:iva:liability` row; footer `sub_vat_tax`) | same | same | Ink, signed |
| Net cash flow (`net_flow`) | Mono 500 | 1 px Ink top | Ink, signed |
| Closing balance (`cash`) | Mono 500 | double rule above | Signal only when below `p.min_cash`; ◆ marker when a day inside the column is below and the closing is not (UI-05) |
| Headroom (`headroom = it("cash") − p.min_cash`) | label Ink-2, figure Mono 500 | none | Signal when negative; hatched band under a ◆ column |

Groups open collapsed on first load. The footer figures are derived items defined in the book through the SDK (DATASET.md §Items); a book without them shows `ENGINE TOTAL PENDING` in the footer cell (D-MLP-62), never a client sum.

Worked column, `W40 · F · 28 Sep–4 Oct`, base, from `DATASET.md` (every figure is an engine output; the arithmetic is printed here only to show that the frame ties):

```
Opening balance             109 712.10
customer:veltro              31 110.00      30 % of the 30 Sep milestone, incl. VAT
Receipts                     31 110.00
cat:opex                        −48.00      bank_charges, 1 Oct
cat:subcontract             −15 880.00      subcontract −9 760.00 · freelance −6 120.00
cat:financing                  −375.00      fido_commission, 1 Oct
Disbursements               −16 303.00
VAT & tax                          —
Net cash flow                14 807.00      31 110.00 − 16 303.00
Closing balance             124 519.10      109 712.10 + 14 807.00
Headroom                     64 519.10      124 519.10 − 60 000.00
day-grain min               109 712.10 · 28 Sep (no crossing)
```

The one week where the frame turns red: `W45 · 2 Nov–8 Nov` closes at `56 685.00` with no receipts and `cat:opex −4 684.00`; the Headroom row reads `− € 3 315.00`. The lowest point after cutover is `€ 56 685.00` on 5 Nov 2026, inside `W45`, so the week column shows it. The month column does not: Nov 2026 closes at `231 805.90` after the 30 Nov receipts, so the Nov column carries the ◆ crossing marker (UI-05) with `€ 56 685.00 · 5 Nov` on hover. An aggregated column can hide a daily trough; the marker, the chart mark and the alert exist for that reason.

### 5.3 Column widths

The widest expected cell is a signed seven-digit balance with cents, 13 characters: `−1 250 000.00`. The thin space has the same advance as any other Mono glyph, so the arithmetic is unchanged `[graft C]`.

| Tier | Row | Figure | 13 chars | Padding | Column | Name column |
|---|---|---|---|---|---|---|
| Comfortable | 40 | Mono 12 | 93.6 → 94 | 2 × 8 | **112** | 232 |
| Compact | 32 | Mono 11 | 85.8 → 86 | 2 × 8 | **104** | 208 |
| Dense `[graft B]` | 28 | Mono 11 | 85.8 → 86 | 2 × 4 | **96** | 192 |

Columns auto-size to the widest figure in the column, rounded up to a multiple of 4, minimum 80 px; the Closing row usually sets it. The demo book's widest figure is 11 characters (`−318 813.01`, downside Δ), which gives 96 / 92 / 84 px. Cents are never dropped to make columns fit.

### 5.4 Width math

Visible columns = floor((width − sidebar − name column − inspector) / column). The pinned column counts as one of the 13.

| Width | Sidebar | Inspector | Comfortable 7-digit / demo | Compact 7-digit / demo | Dense 7-digit / demo |
|---|---|---|---|---|---|
| 1440 | 208 | closed | 8 / 10 | 9 / 11 | 10 / 12 |
| 1440 | rail 48 | closed | 10 / 12 | 11 / 12 | 12 / **14** |
| 1440 | rail 48 | 440 | 6 / 7 | 7 / 8 | 7 / 9 |
| 1920 | 208 | closed | **13** / 15 | 14 / 16 | 15 / 18 |
| 1920 | 208 | 440 | 9 / 10 | 10 / 11 | 11 / 12 |
| 1920 | rail 48 | 440 | 12 / 14 | 14 / 15 | 15 / 17 |

All 13 weeks fit without scroll from: Dense + rail 1488 px (seven-digit) or 1332 px (demo book); Compact + rail 1608 / 1452; Comfortable + rail 1736 / 1528; sidebar open adds 160 px to each. So the honest claim is: **13 weeks with cents fit at 1440 in Dense with the rail collapsed for books whose figures stay under seven digits; a seven-digit book shows 12 of 13 and scrolls one column.** This replaces the candidate's 1512 px claim and the judges' 90 px estimate with the font's own arithmetic. Below the fit width, forecast weeks scroll behind the frozen block, the toolbar shows `Weeks 1–10 of 13`, and `Fit` offers rail + Dense.

### 5.5 Trailing total column `[graft C]`

Optional `Σ 13 wk` column, frozen right on Surface, one column width, only when the service supplies the sum. Absent otherwise; never a client sum. It costs one more column of width.

### 5.6 Variance sub-rows (Variance page, periods left of cutover)

A row expands to F / A / Δ / Δ % sub-rows. F = the value at the revision current before the period, via `at()` (plan = r38 at cutover 31 May for Jun–Aug 2026). Δ is coloured only above `p.variance_threshold_pct`: Positive favourable, Signal unfavourable, Ink below threshold; the sign is the first cue. Example row from `DATASET.md`: `cat:revenue` plan `+ € 503 580.00`, actual `+ € 445 020.00`, Δ `− € 58 560.00`, `−11.6 %`. The `Net (engine total)` row is the service's, not a column sum.

### 5.7 Scenario compare

One column group per scenario under a tracked label: `BASE · r51` | `WHAT-IF · downside · r51` | `Δ`. Maximum 5 scenarios. Δ as amount or % via toolbar toggle. Scenario columns carry the WHAT-IF tag in their header and no tint. Each column's stamp is the service's own (D-MLP-54). Example: horizon close `€ 313 129.08` | `− € 88 334.92` | `− € 401 464.00`.

### 5.8 Interaction

- One tab stop, roving focus, arrows between cells, Home / End, Ctrl+Home / End, PageUp / Down. Space selects the row; Shift+arrows extend.
- Enter opens the cell in the inspector (trace / edit / correct per ADR-0013).
- Typing on a forecast Event cell edits in place (`ev-0840 · Server refresh · − € 22 570.00` is the one such cell in the demo book). Typing on a generated, empty or actual cell opens the `Create forecast event` or `Record a correction` card. Never a silent override.
- Single letters: `T` trace, `E` edit, `C` correct, `F` fork, `D` diff, `Z` why_zero, `?` sheet.
- Hover on a cell shows a 16 px trace glyph in Ink-3 at the right edge.
- A changed cell in the working overlay carries the 2 px Ink corner mark.

### 5.9 Footer and export

Every grid carries one stamp in its 36 px footer row: `AS OF 28 SEP 2026 · r51 · 7c2e19b · BASE · ENGINE 1.4.0`. XLSX / CSV export repeats the stamp in the header row and writes the `display` strings.

### 5.10 Monthly grid

Same frame, Month grain: the pinned first forecast month `Sep 2026 · F · 1–30 Sep (partial)` plus the horizon's remaining months to `Jun 2027` = 10 columns, which fit in every tier at 1440 with the sidebar open (10 × 112 = 1120 > 1000 in Comfortable: 8 visible, scroll 2; Compact 9; Dense 10). Column widths are the same as Week grain, because the widest figure is a balance, not a flow. Quarter grain: 4 columns, always fits. The Overview and Variance pages show the actual months (`Jan 2026 · A` … `Aug 2026 · A` on Surface) with the same divider, not pinned, and a `Go to cutover` control in the toolbar.

---

## 6. Chart spec

Three chart types. No pie, no gauge, no fan or uncertainty band (the engine is deterministic; scenarios are separate stamped series). All lines and marks are ≥ 3:1 non-text on Paper; every mark also has a label or a dash pattern. Every chart has a data-table toggle that reuses the grid component (UI-26).

### 6.1 Balance line

- Sizes: Overview 1000 × 280 px; inspector 400 × 200 px.
- Series = closing balance per period, 1.5 px Ink line, Surface area fill from the line to the zero line or the chart floor.
- Zero line 1 px Ink with the word `ZERO` in Mono 11 at the left, drawn only when the series crosses zero (D-MLP-141). Base never crosses; `downside` does at `− € 78 238.60` on 24 Dec 2026.
- Threshold = `p.min_cash` as a 1 px Warning dashed line (dash 4, gap 3), legend `min cash · € 60 000.00 · p.min_cash` Sans 12 Ink-2. Drawn only when the param exists.
- Cutover = 1 px Ink dashed vertical with `CUTOVER · 20 SEP 2026` at the top. The area left of it (actuals) sits on Surface like the grid.
- Marks: local highs as 6 px Paper dots with 1.5 px Ink stroke and a Mono 11 Ink value above; the `LOWEST POINT` as a 6 px Signal filled dot with `€ 56 685.00 · 5 Nov 2026` in Mono 11 Signal below; the end-of-horizon value `€ 313 129.08` at the right end. Axis marks are the series' own figures (high, low, ZERO, end). No rounded tick ladder. X ticks Mono 11 Ink-3.
- Hover `[graft C]`: a 1 px Ink-3 vertical rule at the hovered period and a mini receipt in a Paper popover, 1 px Ink border: period label, then Opening / In / Out / Net / Closing as Mono 12 right-aligned figures from the service's display strings, then the period's stamp. The receipt uses the same figures as the grid row (for `W40`: `109 712.10 / 31 110.00 / −16 303.00 / 14 807.00 / 124 519.10`), so the point ties to the grid.
- Stamp under the chart: `AS OF 28 SEP 2026 · r51 · 7c2e19b · BASE`.

### 6.2 Waterfall bridge

Period detail, revision diff, Apply modal. Opening bar Ink fill; receipts by cat as Positive fills (Positive-cvd in colour-vision mode); disbursements by cat as Paper bars with 1 px Ink outline; Closing bar Ink; connectors 1 px Hairline; every bar labelled with its Mono 11 figure. Totals neutral, movements coloured. `W40`: `126 310.40 → +31 110.00 → −15 880.00 → 141 540.40`.

### 6.3 Stacked in / out bars

Per period, with a 1.5 px Ink net line. Inflows Ink-2 fill; outflows Paper with 1 px Ink outline; net marks 4 px Ink dots. Used on Variance and Overview.

### 6.4 Scenario compare

Two scenarios overlay on one balance chart: Base = 1.5 px Ink solid, scenario = 1.5 px Ink-2 dashed (6/4) with a direct label at the line end `DOWNSIDE · WHAT-IF` in the tag style. Same y scale, same threshold. Three to five scenarios switch to small multiples in a row of equal widths, same y axis, same threshold, each with its own stamp. Dash patterns for legends: solid, 6/4, 2/3, 8/2/2/2. Never hue.

### 6.5 Backtest (pilot guide §8.2)

Forecast vs actual closing balance on one balance chart: actual Ink solid, forecast-at-cutover Ink-2 dashed, both troughs marked and the day gap printed. The six metrics (weekly MAPE 1–4 < 5 %, weeks 5–13 < 12 %, trough timing < 7 days, trough depth < 10 %, directional accuracy > 85 %, coverage > 95 %) as bullet graphs 240 × 16 px: Ink value bar, Ink-3 target tick, Surface qualitative band, Mono 11 value and target at the right. Coverage `97.83 %` is a host statistic and says so in its stamp.

---

## 7. Components

Required set, one row each. Sizes are Comfortable / Compact / Dense where they differ. Every component is defined once in the .pen before any screen (UI-29).

| Component | Spec |
|---|---|
| Sidebar nav item | 32 px, Sans 13/500, 16 px Lucide icon, shortcut hint Mono 11 right. Active: Ink fill r4, Paper text, hint Muted-on-ink. Optional count tag |
| Header | 48 px Paper full width, Hairline bottom; book switcher, title, scenario switcher, provenance stamp, uncommitted count, Save (Ink primary), Discard, `Cmd+K`, bell |
| Stamp | Mono 11 +1.2, uppercase words, ids verbatim, dot-separated, Ink-2 on every ground. Variants: BASE, WHAT-IF (tag), NO SUBTOTAL, CORRECTED · SEE, `· RECOMPUTING` suffix, host-statistic suffix |
| Figure | Mono, right-aligned, U+2212, thin-space grouping, cents always. Variants: hero 32, hero delta 14, table 12 / 11, total 500 weight, inline, struck (Ink-3, 1 px line-through) |
| Ledger row | 40 / 32 / 28 px, Hairline divider; icon tile Surface r4 (40 / 32 / 28); name Sans 13/500; date Mono 12 Ink-2; status tag A / C / F; amount Mono right with sign and symbol; `source · ext_id` stamp |
| Table header cell | 48 px two-line (Sans 11 tracked over Mono 11), right-aligned for money, `A` / `F` tag, `(partial)` suffix where UI-09 asks, 1 px Ink rule under |
| Table cell | 40 / 32 / 28 px, 8 / 8 / 4 px side padding, r0, Mono figure right. States: exact-zero `—`, blank Surface, hatch, focused, selected, uncommitted corner mark, certainty dot, hover trace glyph |
| Badge | Mono 11 or Sans 11/600, 24 px tall, r2, 1 px outline. Variants: count (Ink outline), Assumed (Warning outline, Warning-text), code `CK-W004` (Ink outline), status A / C / F / I |
| Button primary | 32 / 28 / 28 px, r4, Ink fill, Paper text Sans 13/600. Save, verbs with counts, destructive verbs |
| Button secondary | 32 / 28 / 28 px, r4, Paper, 1 px Ink border, Ink text. suggested_fix, Keep editing, Edit details, Discard |
| Button danger | The confirming verb of an irreversible modal: drawn as primary, preceded by a 3 px Signal-bar line naming what cannot be undone (`This writes r42. Revisions are never deleted.`). No red button exists |
| Text action | Ink, Sans 13/600, underlined on hover. `See all`, `3 uncommitted changes`, `◀ Actuals` |
| Input | 32 / 28 / 28 px, r4, Paper, 1 px Ink border, Sans 13; money and date inputs in Mono; label above Sans 11 tracked; inline diagnostic below with a 3 px bar |
| Select | Input drawing plus 16 px `chevron-down`; menu = Paper, 1 px Ink border, rows 32 px with shortcut hints |
| Segmented control | 1 px Ink border r4, segments 32 / 28 / 28 px, active Ink fill Paper text (the mobile Expense / Income pattern) |
| Tabs | Inspector tabs r3, Sans 13/500, active Ink fill Paper text, 1 px Hairline under the tab row |
| Alert banner | Paper, 1 px Ink border, 3 px left bar (Signal / Warning / Ink-3), severity word, message Sans 13, code tag, suggested_fix secondary button, 24 px dismiss |
| Confirmation card | Mobile anatomy verbatim: Paper 1 px Ink card r4, operation title, prop rows with Hairlines, Assumed badges, Mono impact lines, SDK call in Mono 11, verb buttons, `Nothing is saved until you confirm.` States: ready / stale / refused / applied; a superseded card loses its primary button |
| What-if stamp | Tag r2, 1 px Warning outline, Warning-text Mono 11: `WHAT-IF · downside · r51` |
| Diagnostic row | 3 px left bar by severity, severity word, message Sans 13, code tag, suggested_fix button, link to item / field. Example: `warning · Withholding 0.20 is in use and no cat:tax item covers the remittance. [Add a cat:tax item 'F24 ritenute'] CK-W004 · freelance` |
| Empty state | Section title Sans 15/600, one sentence Sans 13 Ink-2, one secondary button naming the verb, no illustration. Never used for an empty cell |
| Toast | Bottom-right, Paper, 1 px Ink border, 3 px left bar (Ink for done, Signal for CK-E), Sans 13 with Mono revision `Committed as r52 · 4 changes`, 24 px close, 6 s |

Additional components the screens need:

| Component | Spec |
|---|---|
| KPI tile | Paper, 1 px Hairline border r4, label Sans 11 tracked (`BOOK · 25 SEP 2026`), hero figure Mono 32 (`€ 109 712.10`), beside the bank fact tile (`BANK · 25 SEP 2026` · `€ 114 592.10`), delta Mono 14 (Positive only with `+`), stamp |
| Book switcher | 32 px, 1 px Ink outline r4, `book` icon, Sans 14/600, `chevron-down` |
| Scenario switcher | 32 px outlined; `BASE · PLAN OF RECORD` stamp style or the WHAT-IF tag |
| Period header cell | Sans 11 tracked `W40 · F` over Mono 11 `28 Sep–4 Oct`; Surface for A, Paper for F |
| Cutover divider | 2 px Ink vertical, `CUTOVER · 20 SEP 2026` at the header; chart variant 1 px dashed |
| Certainty dot | 6 px, Ink-3: filled A, half C, outline F, none I |
| Row glyph | 12 px Mono Ink-3: `I` / `E` / `Σ` / `ƒ` |
| Uncommitted corner mark | 2 px Ink square at the cell top-left |
| Correction scar pair | Struck original + bracketed correction row (`ev-0811` / `ev-0812`) |
| Receipt line | Mono 12, binding left, figure right, 1 px Ink-3 dotted leader at 3 px pitch; result row 500 weight above a 1 px Ink-3 rule. Trace tab only |
| Chart hover receipt | Paper popover, 1 px Ink border, period label, five Mono rows, stamp |
| Command palette | 640 px, Paper, 1 px Ink border, Mono input, scope pills, section labels, rows 32 px with shortcut hints |
| Palette row | Icon 16, text Sans 13, intent code Mono 11 Ink-3 (`M5 add_event`), shortcut Mono 11 right |
| Shortcut sheet | `?` overlay, Paper, 1 px Ink border, two columns of Mono 11 keys and Sans 13 actions |
| Modal | 560 px, Paper, 1 px Ink border, outcome diff first, verb buttons |
| Status bar | 28 px Paper, Hairline top; freshness stamps, coverage, density, colour-vision, diagnostics, engine, recompute |
| Bullet graph | 240 × 16 px, Ink bar, Ink-3 target tick, Surface band, Mono 11 labels |
| Sparkline | 60 × 16 px, 1 px Ink line, Comfortable only |
| Change list | Popover from the header count: cell, SDK call Mono 11, Revert secondary button per row, Discard all |
| Read-only banner | Paper, 1 px Ink border, names the reason (`Viewing r38 · read only`); write controls hidden, not disabled |
| Skeleton | Surface blocks in row and column shapes for book open; never for a recompute |

---

## 8. Contrast table (WCAG 2.2, computed 2026-09-26)

Text needs 4.5:1, non-text 3:1.

| Pair | Ratio | Verdict / rule |
|---|---|---|
| Ink on Paper | 19.80 | pass |
| Ink on Surface | 18.16 | pass |
| Ink-2 on Paper | 6.69 | pass |
| Ink-2 on Surface | 6.13 | pass; stamps are Ink-2 on every ground |
| Ink-3 on Paper | 4.54 | pass by 0.04; the only Ink-3 text is hints, ticks and struck amounts, all on Paper |
| Ink-3 on Surface | 4.17 | FAIL text; pass non-text (hatch, dots, glyphs) |
| Ink-3 on Hairline | 3.51 | pass non-text (leader dots over a rule never happen; listed for completeness) |
| Signal on Paper | 4.85 | pass; Signal text on Paper only |
| Signal on Surface | 4.44 | FAIL; a negative actual on a shaded column is Ink |
| Positive on Paper | 5.43 | pass |
| Positive on Surface | 4.98 | pass |
| Positive-cvd on Paper | 5.19 | pass |
| Positive-cvd on Surface | 4.76 | pass |
| Warning on Paper | 4.20 | FAIL text; pass non-text (lines, outlines, bars) |
| Warning on Surface | 3.86 | pass non-text only |
| Warning-text on Paper | 6.00 | pass |
| Warning-text on Surface | 5.50 | pass |
| Paper on Ink | 19.80 | pass (active nav, primary button) |
| Muted-on-ink on Ink | 8.33 | pass (hints on Ink) |
| Ink-3 on Ink | 4.36 | FAIL; never used |
| Ink-2 on Ink | 2.96 | FAIL; never used |
| Paper on Signal | 4.85 | pass but unused: no red fill behind text |
| Hairline on Paper | 1.30 | decorative only |
| Hairline on Surface | 1.19 | decorative only |
| Surface on Paper | 1.09 | actual-column tint; the `A` tag and the divider are the real cue |

The five lint rules of §3 rule 22 are the whole set of exceptions this table produces.

---

## 9. Rejected alternatives

- **B, Trading Desk (near-black ground, accent blue, amber band).** Best density and the widest contrast margins, but it contradicts accepted ADR-0023, splits the palette between the two apps, needs a second light token set before the first board-pack export, and reads as a trading screen rather than an audit ledger. Taken as grafts: the Dense tier, the pinned cutover column, the `C` status, the single-letter keys and hints, Save as an Ink button. Rejected with it: the WHAT-IF band (a tint behind figures), the accent focus ring (colour as the focus cue), the command palette as primary navigation.
- **C, Calm Fintech Grid (grey ground, white cards with shadows, blue accent, Inter + Geist Mono).** The direction ADR-0023 already called vanilla; eleven colour meanings; two number faces on one screen; Inter's 0.631 em digits make it the least dense of the three; four status pairs fail AA on its own grey surfaces. Taken as grafts: thin-space grouping, dotted leaders in the receipt, `NO SUBTOTAL`, `RECOMPUTING` in the stamp, the corner mark, the inline commit refusal, the hover receipt, the optional `Σ` column. Rejected with it: shadows, tints, category hues, violet for hypothesis, the half-dot as the only committed cue (kept as the dot, with the letter set beside it).

---

## 10. Risks that remain

- Width: a seven-digit book shows 12 of 13 weeks at 1440 in Dense with the rail; Comfortable with the sidebar open shows 8. `Fit` and the pinned column soften it; they do not remove it.
- Ink-3 text on Paper passes by 0.04. Thin Plex Mono at 11 px on Windows ClearType renders lighter than nominal. Every Ink-3 text use may move to Ink-2 without a redesign.
- Two ambers look like an inconsistency to a designer who does not know the contrast reason. The token names carry the rule.
- Dense at 28 px over a four-hour session is the judges' own eye-strain note on B; it is a choice in the status bar, not the default for the CFO.
- Hatched masked cells and blank Surface cells are close in appearance. Both open `why_zero()`.
- A monthly column can hide a daily trough (Nov 2026 closes at `231 805.90`; 5 Nov dips to `56 685.00`). The ◆ crossing marker on the column (UI-05), the chart mark and the alert row carry it.

---

## 11. Open items for the parent (before drawing)

1. **`DECISIONS.md` entry** for the desktop header: Save is an Ink primary button and Signal carries one meaning; the mobile header is unchanged `[graft B]`.
2. **Amend ADR-0023** to name S1b (Plex Mono figures, hairlines) as the carrier of the receipt language, with one exception: dotted leaders in the Trace receipt `[graft C]`.
3. **Restyle `app.pen` flows 12–34 to S1b**; the literal-continuity claim depends on it.
4. **Align the mobile sign and grouping** with rules 2 and 4 (the client code already does this per D-MLP-41; the frames still show `- € 1,400`).
5. **Service escalations** the grid depends on: `group_by` totals for group footers (D-MLP-62), the optional `Σ` column sum, `at()` per period for variance F rows.
6. **`DATASET.md` grouping**: regenerate or post-process with U+2009 before any frame is drawn from it, so no frame is drawn with commas.

---

## 12. Token JSON (pen.dev SetVariables)

```json
{
  "color.paper": "#FFFFFF",
  "color.surface": "#F5F5F5",
  "color.hairline": "#E2E2E2",
  "color.ink": "#0A0A0A",
  "color.ink2": "#5C5C5C",
  "color.ink3": "#767676",
  "color.mutedOnInk": "#A8A8A8",
  "color.signal": "#E4002B",
  "color.positive": "#0A7A3E",
  "color.positiveCvd": "#0072B2",
  "color.warning": "#B26B00",
  "color.warningText": "#8F5600",
  "font.ui": "IBM Plex Sans",
  "font.figure": "IBM Plex Mono",
  "radius.box": 4,
  "radius.tab": 3,
  "radius.tag": 2,
  "radius.cell": 0,
  "space.1": 4,
  "space.2": 8,
  "space.3": 12,
  "space.4": 16,
  "space.5": 24,
  "space.6": 32,
  "space.7": 48,
  "space.8": 64,
  "type.pageTitle": 20,
  "type.sectionTitle": 15,
  "type.label": 11,
  "type.body": 13,
  "type.bodySecondary": 12,
  "type.button": 13,
  "type.figureHero": 32,
  "type.figureHeroDelta": 14,
  "type.figureTable": 12,
  "type.figureTableCompact": 11,
  "type.receipt": 12,
  "type.stamp": 11,
  "type.code": 11,
  "type.date": 12,
  "type.axis": 11,
  "tracking.label": 1.2,
  "tracking.stamp": 1.2,
  "tracking.pageTitle": -0.2,
  "tracking.figureHero": -0.5,
  "layout.header": 48,
  "layout.sidebar": 208,
  "layout.rail": 48,
  "layout.toolbar": 40,
  "layout.statusBar": 28,
  "layout.inspector": 440,
  "layout.inspectorMin": 400,
  "layout.inspectorMax": 480,
  "layout.palette": 640,
  "layout.modal": 560,
  "layout.contentPadding": 24,
  "layout.minWidth": 1280,
  "grid.rowComfortable": 40,
  "grid.rowCompact": 32,
  "grid.rowDense": 28,
  "grid.periodHeader": 48,
  "grid.footer": 36,
  "grid.nameColComfortable": 232,
  "grid.nameColCompact": 208,
  "grid.nameColDense": 192,
  "grid.periodColComfortable": 112,
  "grid.periodColCompact": 104,
  "grid.periodColDense": 96,
  "grid.periodColMin": 80,
  "grid.cellPadding": 8,
  "grid.cellPaddingDense": 4,
  "grid.cutoverDivider": 2,
  "grid.cornerMark": 2,
  "grid.certaintyDot": 6,
  "grid.hatchPitch": 6,
  "receipt.leaderDot": 1,
  "receipt.leaderPitch": 3,
  "control.height": 32,
  "control.heightCompact": 28,
  "control.border": 1,
  "control.focusRing": 2,
  "control.iconButton": 24,
  "control.tagHeight": 24,
  "icon.row": 16,
  "icon.header": 18,
  "chart.overviewW": 1000,
  "chart.overviewH": 280,
  "chart.inspectorW": 400,
  "chart.inspectorH": 200,
  "chart.line": 1.5,
  "chart.mark": 6,
  "chart.thresholdDash": 4,
  "chart.thresholdGap": 3,
  "chart.sparklineW": 60,
  "chart.sparklineH": 16,
  "chart.bulletW": 240,
  "chart.bulletH": 16
}
```
