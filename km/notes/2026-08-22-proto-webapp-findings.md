# Proto webapp — SDK-relevant findings (2026-08-22)

Source: `proto/` prototype and its trial suite (`proto/TESTLOG.md`). No SDK code change was necessary. Four observations for the core team.

1. **Mutual recursion needs a placeholder.** `add_derived` parses and DAG-checks at add time. A legal pair (fee reads `prev(closing)`, closing aggregates the fee) is refused in both orders with CK-E001. The working pattern: author the fee as formula `"0"`, add the closing stock, re-author the fee. Suggestion: document this pattern, or defer unknown-reference checks for a formula that the same batch later satisfies.

2. **`Recurrence` is required but inert on schedule segments.** A schedule is its own occurrence series (D-P2-02), yet `Segment` requires `recurrence`. Every LLM eventually omits it there, and the refusal is pure friction. Suggestion: make `recurrence` optional when `amount.schedule` is set.

3. **Excel parity can differ by one cent.** Engine intermediates are int64 @ 4dp. Example from the agios trial: `612.07 × 0.0795 → 48.6596` (4dp), `/12 → 4.0550`, an exact tie, banker's rounding → `4.06`. Excel's float `ROUND` gives `4.05`. This is designed behavior, not a bug. Any "import a spreadsheet and reproduce it" feature must state this tolerance.

4. **The kit is single-threaded, and that is fine — but web servers are not.** The sqlite ledger connection binds to its creation thread. FastAPI's default threadpool broke it immediately. App-layer rule: confine one kit to one thread (async endpoints on the event loop work). Worth one line in the SDK docs.

Model-capability results (OpenRouter): gemini-2.5-flash-lite handles flat lines, windows, schedules, params, scenario forks, settlement terms, and non-monthly recurrences once the JSON transport is hardened. It fails on recursive conditional formulas (agios) with *silent wrong semantics*. gemini-3.7-flash passes everything, including messy-spreadsheet initialization. Claude sonnet/opus were never needed.
