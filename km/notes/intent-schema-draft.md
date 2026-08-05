# Intent schema draft — v0 (unscored)

Per ADR-0019: the agent surface is an enumerated intent grammar with typed slots. This is the draft for review and model-scoring; it is not a shipped contract. Scoring against a small (~3–4B) and a mid-size model is still owed before this hardens.

Conventions:

- `as_of` is **always host-filled** (ADR-0019 rule 2), never model-filled; omitted from slot lists below.
- `scenario` defaults to the active scenario; every intent accepts it.
- Amounts are decimal strings in book currency, VAT-exclusive (net) — same as the SDK boundary.
- `R` = read intent, `M` = mutation intent (host may require confirmation on M; ADR-0019 rule 3).
- Every reportable question is ONE call (rule 1). Intents marked **[SDK gap]** need a single-call SDK verb that does not exist yet — this list is the input to the Phase-10/post-v1 SDK review.

## Read intents (12)

| # | Intent | Slots | Maps to |
|---|---|---|---|
| R1 | `project_balance` | `delta?: money`, `delta_date?: date`, `horizon: duration \| date` | `summary()` over a throwaway overlay |
| R2 | `runway` | — | `summary().runway` |
| R3 | `min_cash` | `horizon?: date` | `summary().min_cash` |
| R4 | `breakeven` | — | `summary().breakeven` |
| R5 | `top_categories` | `direction: in\|out`, `period: date-range`, `n?: int=5` | **[SDK gap]** single-call ranked tag aggregation |
| R6 | `item_total` | `item: item_id \| tag-selector`, `period: date-range`, `measure?: accrual\|cash` | `frame(where=...)` reduced — **[SDK gap]** as one verb |
| R7 | `explain_cell` | `item: item_id`, `period: date` | `trace()` |
| R8 | `explain_zero` | `item: item_id`, `period: date` | `why_zero()` |
| R9 | `compare_scenarios` | `scenarios: [scenario_id]`, `metric?: cash` | `compare()` |
| R10 | `coverage` | — | render of `validate()` info diagnostics (ADR-0020) |
| R11 | `list_items` | `tag?: selector` | `describe_book()` subset |
| R12 | `history` | `n?: int=10` | `history()` |

## Mutation intents (9)

| # | Intent | Slots | Maps to |
|---|---|---|---|
| M1 | `add_item` | `id`, `direction`, `amount`, `recurrence`, `start`, `end?`, `tags?`, `vat_rate?` | `set_item` (typed subset — full Item shape stays SDK-only) |
| M2 | `set_amount` | `item`, `amount`, `from_date?` | `set_item` segment amount (from_date splits) |
| M3 | `shift_items` | `selector`, `by: duration` | `ShiftItems` macro |
| M4 | `scale_items` | `selector`, `factor` | `ScaleItems` macro |
| M5 | `add_event` | `date`, `amount`, `direction`, `item?`, `note?` | `add_event(status="forecast")` |
| M6 | `correct_actual` | `event`, `amount`, `note` (required) | `correct_event` (ADR-0012) |
| M7 | `fork_scenario` | `name`, `parent?` | `fork()` |
| M8 | `set_cutover` | `date` | `set_cutover` |
| M9 | `save` | `message` | `commit()` |

## Deliberately absent

- Free-form formula authoring: an intent that accepts a formula string is SDK composition wearing a hat; formulas stay notebook/SDK territory.
- `void_event` as an intent: destructive, rare, and easily confused with M6 by a small model — SDK-only for now.
- Anything advisory (ADR-0015): no `is_my_forecast_complete`, no `what_should_i_do`. R10 renders diagnostics; it does not judge.
- `import_events`: file-shaped, host-mediated, not conversational.

## Open questions for scoring

1. Is M1's slot set small enough for a 3–4B model, or does it need splitting (add_recurring vs add_one_off — M5 already covers one-offs)?
2. Does R1 (`project_balance` with a hypothetical delta) need its own verb, or is a throwaway-overlay convention enough for the host to implement?
3. Selector grammar in R5/R6/M3/M4: full PRD §5.4 selectors, or an enumerated subset (`tag:k=v`, `direction`, id-list)? Draft assumes the subset.
