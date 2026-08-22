# Proto webapp — trial log

## Summary (2026-08-22)

**Result: the loop works.** Natural language → JSON ops → SDK → diagnostics → self-correction, on the cheapest Gemini model for most tasks. 10 trials, 9 automated + 1 browser E2E, all green on the final harness. Spend: ~$0.09 of the $20 weekly key limit; sonnet/opus never required.

**Model boundary.**
- `gemini-2.5-flash-lite`: flat lines, date windows, schedules, params, scenario forks, net/split settlement, weekly/quarterly recurrence. Fails recursive conditional formulas with plausible-but-wrong numbers and NO diagnostic — the one failure class money cannot tolerate.
- `gemini-3.7-flash`: everything above plus the agios construct and messy-spreadsheet initialization. First-shot on both.

**What moved the needle (ranked).**
1. JSON transport hardening: `response_format: json_object`, first-object `raw_decode`, bracket-stack repair, temp bump on repair retries. Killed ~half of all lite failures.
2. One concrete example per construct in the ops guide (lite follows examples, not prose).
3. Honest diagnostic feedback: untruncated validation messages; the CK-E### loop then genuinely self-corrects.
4. Applier normalization for inert requirements (settlement shorthands, recurrence on schedules, bare-number offsets).

**Route forward.** Default lite for authoring/edits; route to flash when the instruction implies a formula (conditional words: "if", "when negative", "based on last month") and for every upload. Add a verification habit: after formula-bearing turns, show `trace()` output — lite's silent-wrong-formula mode is the real risk, and the engine already has the introspection to expose it.

Trial-by-trial records follow.

Format per trial: what was tried, why, result, next step.
Server: `uv run uvicorn proto.server:app --port 8765`. Trials: `uv run python proto/trials/tNN_*.py [model]`.
Models: lite = google/gemini-2.5-flash-lite, flash = google/gemini-3.7-flash,
sonnet = anthropic/claude-sonnet-5, opus = anthropic/claude-opus-5.

## T00 — smoke test, no LLM (2026-08-22)

- **Tried:** direct `apply_op` calls: create_book, constant item, schedule item, stock derived with `prev` self-reference; then `run_payload`.
- **Why:** separate applier bugs from model bugs before any LLM trial.
- **Result:** two bugs in my server code, both fixed: `Book.items` is a dict (iterated keys as items), and the Book field is `base_grain`, not `grain`. After the fix all values and the balance series were correct.
- **Next:** T01 with the lite model.

## T01 — student base budget, one instruction, model: lite (2026-08-22)

- **Tried:** one chat turn with the scenario-01 base facts: 5 income lines, 4 expense lines, two windowed lines (CROUS pauses Jul+Aug, savings order ends after June). Checks: 5 item cells + closing Jan (411.80) + closing Dec (3227.60), computed independently.
- **Why:** the baseline translation task — flat lines and date windows, no formulas.
- **Result:** **ALL PASS.** 1 round, 10 ops, 0 diagnostics. The model chose multi-segment items for the windowed lines (correct construct). Repeated once: same result.
- **Anomalies:** the very first T01 attempt returned 3 rounds with 0 ops and no visible parse error (error printing was added after). The identical retry worked twice. Watch for it; instrument if it recurs.
- **Next:** T02 — month-varying schedule + one-time arithmetic (CROUS revaluation, double October payment).

## T02 — edit an existing book: slipped instalment, revalued double payment, model: lite (2026-08-22)

- **Tried:** turn 1 builds a 2-item book; turn 2 rewrites the scholarship year: Sep 0, Oct 494 (2×ROUND(242×1.021)), Nov+Dec 247, Jan–Jun unchanged. Checks on 7 CROUS cells + rent untouched + closing Dec (−2870).
- **Why:** the most common real interaction is editing a line of an existing budget, and a schedule amount is the right construct.
- **Result:** **ALL PASS** on lite. Turn 1 needed one correction round: the model first tried a derived formula with `book.` attribute access → CK-E003 → fixed itself on the diagnostic feedback. Turn 2: clean single op (re-authored the item with a 10-entry schedule).
- **Takeaway:** the diagnostic-feedback loop works as designed; re-authoring by id is a good edit primitive for LLMs.
- **Next:** T03 — conditional overdraft fee (the hardest scenario construct).

## T03 — conditional agios with 2 EUR floor, compounding, models: lite then flash (2026-08-22)

- **Tried:** one instruction describing the bank's rule (fee in month t on |closing t−1| × 7.95%/12, 2dp, 2 EUR floor, only if t−1 closed negative, fee reduces the balance). Expected series hand-computed, then engine-verified.
- **Why:** scenario-01 edge case 2; needs param + `where`/`prev`/`max`/`abs_`/`round_` + a closing stock + the fee feeding back into the balance (mutual recursion).
- **Prompt bugs found (mine, both fixed):**
  1. My guide showed `round_(x, 2)`; the engine requires keyword-only `round_(x, ndigits=2)` → CK-E003 loop.
  2. `add_derived` DAG-checks at add time, so a mutually recursive pair (fee↔balance) is un-authorable in ANY order. Doctrine added to the prompt: add fee as formula `"0"`, add balance stock, re-author fee. **SDK observation, not a bug:** parse-now checking makes legal `prev`-broken cycles unreachable without the placeholder trick — worth a line in the SDK docs someday.
- **Result lite:** FAIL twice. After the prompt fixes lite produced *valid but semantically wrong* formulas with zero diagnostics — it turned the 2 EUR floor `max(2, x)` into `max(0, x) − 2` and inverted branch logic. Dangerous failure mode: plausible numbers, no error signal. Also notable: lite discovered `init=p.opening_balance` (works because `opening_balance` is a reserved param key).
- **Result flash:** **ALL PASS**, 1 round, 6 ops, used the placeholder-reauthor pattern correctly.
- **Engine parity note:** int64@4dp intermediates give Aug fee −4.06 where Excel's float ROUND gives 4.05 (`612.07×0.0795→48.6596` at 4dp, `/12→4.0550`, exact tie, banker's → 4.06). Designed behavior; Excel parity can differ by a cent on chained multiplications. Recorded, test expects engine truth.
- **Takeaway:** **model boundary found.** lite is enough for flat lines, windows, schedules, and simple edits (T01, T02); recursive conditional money formulas need flash. Escalation per goal: default the app to lite, use flash for formula-bearing instructions — for now the UI selector covers it.
- **Next:** T04 — scenario fork + param sweep on lite; then the Excel surface.

## T04 — params as levers + scenario fork, model: lite (2026-08-22)

- **Tried:** turn 1: revenue = day_rate × days params + fixed costs; turn 2: fork `downside`, override days 12→8. Checks: revenue and closing in both scenarios.
- **Why:** the param/scenario surface is CashKit's core lever story.
- **Result:** first run FAIL — lite invented `{"amount":{"formula":...}}` inside segments (3 rounds, never recovered) and `book.opening_balance` attribute access. **Fix: prompt, not model** — added one micro-example of a param-driven derived item + an explicit "amounts are literals, computed lines are derived items" rule. Re-run: **ALL PASS** on lite (turn 1: 1 round/3 ops; turn 2: 1 round/2 ops with fork + sparse param override).
- **Takeaway:** a single concrete example in the ops guide is worth more than a rule; lite follows examples, not prose. Re-ran T03 after the guide change: still FAIL on lite (sign inversion this time) — the T03 boundary stands.

## T05 — Excel export (budget window + ledger), no LLM (2026-08-22)

- **Tried:** deterministic book via the new `/api/ops` dev endpoint; export budget 6 months from March; export ledger; parse both with openpyxl and check cells.
- **Result:** **ALL PASS** after one server fix: ledger rows carry dict cells (tags) that openpyxl refuses — now JSON-stringified. Also confirmed: forecast events DO settle into the cash balance.
- **Infra bug found (app-layer, not SDK):** the kit's sqlite ledger connection is thread-bound and FastAPI's threadpool hops threads per request → `sqlite3.ProgrammingError`. The SDK is single-threaded by design; fix was making every endpoint async so the whole app lives on the event-loop thread.

## T06 — Excel upload round-trip of our own export, models: lite, flash (2026-08-22)

- **Tried:** build the student book deterministically, export 12-month budget, reset, upload the file, compare 5 closings + item cells.
- **Result lite #1:** FAIL badly — and the root cause was **my export**: the budget sheet carried no opening balance, so no model could recover it. Fixed: export now writes an `Opening balance | meta` row; upload guide told to map it to create_book and to treat 0/blank months as "no amount".
- **Result lite #2:** 6/10 → only one real error left: exclusive-end off-by-one (CROUS window ended 08-01 instead of 07-01 → July paid).
- **Result flash:** **ALL PASS.**
- **Takeaway:** round-trip is format-limited before it is model-limited: make the export self-describing first, then judge models.

## T07 — messy human budget sheet, models: lite, flash (2026-08-22)

- **Tried:** generated a realistic family-budget workbook (month-name headers, POSITIVE expenses, section + SUM/net/balance rows, starting-balance corner cell, 13th-month salary, bimonthly utilities, one annual premium, mid-year price rise). Upload → book → 13 checks.
- **Why:** this is the actual "initialize from an existing budget" use case.
- **Result lite:** 11/13 — clean on signs, sections, schedules and the price rise; failed the annual premium (made June open-ended → charged 7×540).
- **Result flash:** **ALL PASS**, needed one correction round: it omitted `recurrence` on schedule segments (Pydantic requires it even though a schedule is its own occurrence series). **Applier improvement:** default `recurrence` to monthly when the amount is a schedule — that error class disappears for every model. **SDK ergonomics note:** a required-but-inert field on schedule segments is authoring friction; consider making `recurrence` optional when `amount.schedule` is set.
- **Takeaway:** flash parses human sheets reliably; no need to reach for sonnet/opus on this class of file. lite is one semantic slip away — usable with a review step, not for unattended init.

## T08 — payment terms: net-45 + 50/50 split on a one-time project, model: lite (2026-08-22)

- **Tried:** turn 1: monthly 6000 invoice paid 45 days later + immediate 300 subscription; turn 2: one-time June project 12000, half on signing, half after 60 days. Checks separate accrual from cash, and December's invoice must settle outside the horizon.
- **Why:** accrual-vs-cash is the CashKit feature a spreadsheet cannot express; settlement grammar had zero coverage.
- **Iterations (4 runs to green, every fix app-side):**
  1. lite emitted structurally broken JSON and temp-0 retries reproduced it byte-identical, 3 dead rounds. Fixes: `response_format: json_object` on OpenRouter + `raw_decode` (first object wins) + temperature 0.7 on repair rounds.
  2. One-time project authored as an open-ended monthly segment (same slip as T07-lite). Fix: explicit ONE-TIME doctrine in the guide ("once/annual/in June" = exactly one period).
  3. Offset died on `'45dd'` — **my bug**: the model sent `{"net":"45d"}` and my normalizer appended a second `d`. Also my diagnostic feedback truncated the Pydantic pattern before the model could see the expected format. Fixes: unit-aware net normalization, bare-number offsets get `d`, compact untruncated validation errors.
- **Result:** **ALL PASS on lite**, 1 round per turn, zero diagnostics. Accrual Jan 6000 with cash 0, cash lands Feb; split pays Jun+Jul; closing Dec 79400 (Dec invoice correctly drops off the horizon).
- **Takeaway:** most "model failures" so far were harness failures — JSON transport, my normalizer, truncated feedback. lite handles settlement fine once the harness is honest.

## T09 — non-monthly recurrences: quarterly + weekly, model: lite (2026-08-22)

- **Tried:** one instruction: 300 EUR premium every 3 months from March, 120 EUR wage every week from Monday Jan 5. Checks include the 4-Monday January (480) and the 52-week year (6240).
- **Result run 1:** FAIL before any op — lite dropped ONE closing brace in an otherwise perfect 827-char response, and retries reproduced it. **Harness fix:** a bracket-stack repair pass in `extract_json` (insert expected closers on mismatch, drop strays). Unit-tested on three shapes.
- **Result run 2:** **ALL PASS on lite.** `every:3/month` and `every:1/week` both correct; the engine counted Mondays right.
- **Takeaway:** with JSON transport made robust (json_object mode + first-object decode + bracket repair), lite's op quality is much better than its raw reliability.

## Regression — full suite on the final harness (2026-08-22)

`T01 lite / T02 lite / T03 flash / T04 lite / T05 — / T06 flash / T07 flash / T08 lite / T09 lite`: **9/9 ALL PASS.** Total OpenRouter spend to this point: $0.077 (66 calls). The only trial that ever needed a model above flash: none — sonnet/opus were never required.

## T10 — browser end-to-end of the page itself (2026-08-22)

- **Tried:** Playwright drive of `http://localhost:8765`: chat a 3-line budget on lite (table + closing row render, spend badge updates), switch the selector to flash, upload the messy T07 workbook through the file chooser.
- **Result:** **PASS.** Upload rebuilt the family budget in the UI: 13th-month salary, parental-leave gap + 400 return, bimonthly utilities, one-time June premium, kindergarten rise — closing Dec 13390 as hand-computed. Screenshots: `ui-chat.png`, `ui-upload.png` (session artifacts, not committed).
- **One cosmetic nit left:** the summary strip prints raw 4dp values (`13390.0000`).
