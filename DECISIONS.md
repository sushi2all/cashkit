# DECISIONS — judgement calls made under ambiguity

Append-only. Every entry records a choice the PRD/ADRs did not fully
determine, with reasoning. Larger architectural decisions live in `km/adr/`;
this file is for implementation-level calls.

## Phase 1 — Models and canonical serialization (Session S1)

### D-P1-01 · Overlay recordedness via `model_fields_set`; recorded `None` serialized as explicit `null`
ADR-0009 requires field-sparse overlays, but several Item fields are
legitimately nullable (`direction`, `formula`, `settlement`, `vat`), so
`None` cannot mean "unrecorded". A field is *recorded* iff it was explicitly
provided at construction (Pydantic `model_fields_set`); the canonical emitter
omits unrecorded fields entirely and emits a recorded `None` as an explicit
`field: null`. This is the one exception to the "None omitted" emitter rule —
without it, clearing a parent's `settlement` in a child scenario would be
unrepresentable, and a recorded-None field would phantom-shift to unrecorded
across a round trip. Overlay equality (`SparseOverlay.__eq__`) includes the
recorded-field set, because two overlays with equal values but different
recorded fields resolve differently.

### D-P1-02 · Empty collections always serialized explicitly (`[]` / `{}`)
The no-flow-style rule is read as applying to non-empty collections; empty
ones have no block representation in YAML, so `[]`/`{}` are the only spellings.
Emitting them always (rather than omitting empties) keeps "empty" and
"absent" distinguishable — load-bearing for `Settlement(due=[])`, which means
"accrues, never settles" and must not collapse into "no settlement" — and
keeps parsing independent of model defaults.

### D-P1-03 · `Amount.schedule` serialized as `{date, amount}` maps
The in-memory type stays `list[tuple[date, Money]]` per PRD §4.0; the
canonical YAML form is a block sequence of two-key maps
(`- date: "..."` / `amount: "..."`) because a tuple has only flow-style or
doubly-nested block spellings, both hostile to git diffs (the whole point of
YAML config, D9). A before-validator on `Amount` accepts the map form.

### D-P1-04 · `str | Decimal` rate fields disambiguated by grammar at parse
`VatSpec.rate` and `Escalation.rate` are param key or literal; both serialize
to quoted strings. On input, a string matching the param-key grammar
`[a-z][a-z0-9_]*` is a key; anything else must parse as a Decimal literal
(else `ValidationError`). No ambiguity is possible because the key grammar
and Decimal syntax are disjoint. Chosen over a tagged representation
(`{param: ...}` / `{literal: ...}`) for authoring legibility.

### D-P1-05 · Identifier grammars
- `ItemId`, param keys, tag keys, flag names, `TaxRegime.id`:
  `[a-z][a-z0-9_]*` (≤64 chars) — the PRD fixes this for param keys (§4.1,
  CK-E007); applied uniformly so every id is formula- and
  selector-addressable, and item ids are safe as `items/<id>.yaml` filenames.
- `Book.id`, `Scenario.id`: additionally allow `-` (they name directories
  and files — the PRD's own example is `acme-cashflow` — and never appear in
  formulas).
- Tag *values*: no whitespace, no `:` — anything looser would author tags the
  §5.4 selector grammar can never match, a silent-mismatch class.
- `EventId`: opaque non-empty string (ledger-owned).
- Currency `[A-Z]{3}`, country `[A-Z]{2}`.

### D-P1-06 · Money bounds enforced at the boundary
`Money` (Decimal at parse/serialize) must be finite, carry at most 4 decimal
places, and satisfy |x| ≤ 9×10¹⁴ (the int64@4dp engine ceiling, PRD §5.3).
A 5-dp authored amount is rejected at parse rather than silently rounded by
the engine later; overflow fails loudly at the door. Money and all Decimal
fields also reject `float` input outright (a float has already lost the
author's digits to binary fractions). The single occurrence of the identifier
`float` in `cashkit/model/` is the rejection guard itself, allowlisted
narrowly in `tests/test_no_float_money.py`.

### D-P1-07 · Model-level validation is structural; diagnostics are the SDK's job
Pydantic validators in `cashkit/model/` enforce only structural invariants
(types, ranges, patterns, xor-fields, key↔id consistency); violating them
raises `ValidationError`, which at this layer is programmer error. Everything
a user or agent could plausibly do wrong — settlement shares not summing to 1
(CK-E004), generative stocks (CK-E012), sign vs direction (CK-E011), invalid
recurrences (CK-E009) — is pre-validated by the SDK (later sessions) into
§10.1 diagnostics *before* model construction. Consequence: share-sum and
share/amount-mix rules are deliberately NOT model validators. The catalogue
itself ships in Phase 1 as data (`cashkit/model/diagnostics.py`).

### D-P1-08 · `EventOverlay` field set
The PRD references `EventOverlay` without defining it. Overridable fields:
`date`, `amount`, `status`, `item`, `tags`, `vat`, `settlement`, `currency`,
`note`. Not overridable: `id` (the dict key), `source`, `ext_id` (import
identity — rewriting it would break `UNIQUE(source, ext_id)` idempotency).
`status` is typed `Literal["committed", "forecast"]` — an overlay that
*fabricates* an actual is unrepresentable at the type level; the remaining
CK-E006 case (an overlay targeting an event whose ledger status is actual) is
checked at apply time in Phase 7 when the ledger is available.

### D-P1-09 · All models frozen
Immutability by default (`model_config frozen=True`): nothing can mutate an
Event (actuals immutable) or any other model after construction; updates are
functional via `model_copy`. Also `extra="forbid"` everywhere, so a typo'd
field fails parse instead of silently vanishing.

### D-P1-10 · Canonicalization inside models, not the emitter
`CalendarSpec.holidays` is sorted and de-duplicated by a field validator, so
a constructed model is already canonical and `parse(serialize(x)) == x` holds
without the emitter reordering user data. Lists whose order is authored
meaning (`segments`, `Settlement.due`, `Amount.schedule`) are preserved
as-authored. Sets (`flags`, `weekend`, `removed`) serialize sorted; mapping
keys serialize sorted; model fields serialize in declaration order.

### D-P1-11 · Every string in canonical YAML is double-quoted
User strings (values and mapping keys) always emit double-quoted with a fixed
escape table (`\n`, `\t`, `\xXX`, `\uXXXX` for controls/separators); field
names emit bare. This makes the canonical form independent of YAML
plain-scalar heuristics ("no", "3.14", "2026-01-01" can never be mistyped by
a parser) at a small readability cost. Decimals keep `str(Decimal)` exactly —
exponent and trailing zeros preserved — so `"0.10"` never phantom-diffs to
`"0.1"`.

### D-P1-12 · Wall-clock lint is conservative
The lint (a test, `tests/test_wall_clock_lint.py`) flags *any* attribute
named `today`/`now`/`utcnow`, any `time.time` reference, and any import of
the `time` module in `cashkit/engine/` and `cashkit/model/` — call or no
call, whatever the receiver. False positives are acceptable; a missed
wall-clock read is not. The lint validates itself against sample violations
so it can never silently rot into linting nothing.

### D-P1-13 · Structural extras on Scenario and Event
- `Scenario`: `added` keys must equal `item.id`; an id cannot be in both
  `items` (overlay) and `added`; a scenario cannot be its own parent.
  `removed` ∩ descendant re-`added` resolution stays deferred to Phase 7 per
  the review note.
- `Event.ext_id` requires `source` — half an idempotency key cannot exist.
- `Book.items` keys must equal `item.id`; `tax_regimes` ids unique (they name
  synthetic graph items `_tax:<id>:*`, ADR-0005).
- `Recurrence.day` is set iff `anchor == "day_of_month"`.
- `Segment.probability` range is deliberately NOT bounded at the model layer
  (no catalogue code exists for it; a future `validate()` check may warn).

### D-P1-14 · `TaxRegime.accumulates` may be empty
For a VAT regime the base defaults to "every item carrying a VatSpec"
(ADR-0005); the empty string is that default's representation until Phase 6
gives the selector grammar a concrete resolver.

### D-P1-15 · `Event.corrects` — structural rules at the model, referential rules in the ledger (ADR-0012)
ADR-0012 makes mis-recorded actuals correctable append-only: a correcting
event carries `corrects=<original EventId>`; no in-place update exists.
Following the D-P1-07 split, the model enforces only what is checkable on the
event alone: `corrects != id` (no self-correction) and a **non-empty** `note`
(an empty or whitespace note is no more auditable than a missing one — the
ADR's "note is mandatory" is read as "a stated reason is mandatory").
Referential rules — target exists, target not already corrected, target not a
tombstone — need the ledger and are Phase 5 diagnostics with codes assigned
there. `EventOverlay` deliberately does NOT get a `corrects` field: corrections
live in the ledger, never in a scenario (ADR-0012 decision 1), so a scenario
that "corrects" an actual stays unrepresentable at the type level. No
void/tombstone state on the model either — that is ledger row state.

## Phase 2 — Reference engine and formula front-end (Session S2)

### D-P2-01 · Rounding policy is a run-level engine parameter, not a Book field
PRD §5.3 declares "one declared rounding policy (half-up by default,
configurable to banker's)" and §5.4 refers to "the book's declared policy", but
no model field holds it and adding one would change the canonical serialization
S1's gate pins. It is therefore `RoundingPolicy` in
`cashkit/engine/numeric.py`, threaded through `run(book, policy=...)`, default
`HALF_UP` (halves away from zero, matching `decimal.ROUND_HALF_UP` — so a
credit note rounds as the exact mirror of the invoice it reverses). Determinism
is preserved because the default is fixed and the policy is constant for a whole
run. Where the value is *stored* is deferred to the session that introduces
`.cashkit/config.toml`; if it must survive across machines it belongs on the
Book (git-tracked), not in the untracked config file — flagged for that session.

### D-P2-02 · `Amount.schedule` dates *are* the occurrence dates
The PRD gives `Amount` a `constant` xor a `schedule` of `(date, Money)` pairs and
notes "computed schedules are authored via the SDK", but never says how a
schedule interacts with the mandatory `Recurrence`. Two readings existed: the
recurrence generates the dates and the schedule is a lookup keyed by those dates,
or the schedule's own dates are the occurrences. The first can silently drop
authored money (a schedule entry not landing on a generated date) and silently
zero occurrences (a generated date with no entry); the second uses every authored
pair exactly once. Chosen: the schedule's dates are the occurrences, filtered
only by the horizon. `Recurrence` still supplies `business_day_adjust`, and
escalation/probability still apply. Consequence: `Segment.end` does not filter an
explicit schedule — the author dated each amount deliberately, so the only
boundary applied is the model's own horizon.

### D-P2-03 · Recurrence phase follows the segment; accruals outside the horizon are not generated
Buckets step from `Segment.start` always, even when the segment opens years
before the horizon — a rent contract running since 2025 still falls due on the
1st, not on whatever day the horizon happens to open. (Anchoring buckets to the
horizon instead was the first implementation and was wrong; the fast-forward that
skips unreachable buckets arithmetically keeps it cheap.) Accruals whose
*unadjusted* anchor falls outside the horizon are not generated at all, so an
obligation accrued before the horizon does not settle inside it: the horizon is
the model's declared window, and the pre-horizon world is represented by
`opening_balance` and the ledger. Segment-window membership is tested on the
unadjusted anchor so adjacent segments partition their occurrences cleanly
whatever business-day rolls do; escalation is likewise keyed to the unadjusted
anchor, so a New Year roll cannot change an amount.

### D-P2-04 · `settlement=None` settles immediately; `Settlement(due=[])` never settles
D-P1-02 established that an explicit empty `due` list means "accrues, never
settles" and must stay distinguishable from absence. Absence therefore needs its
own meaning, and the only useful one for a cash-flow engine is the obvious
default: cash moves when the amount accrues, in the same period, with no
withholding — equivalent to `Settlement.immediate()`.

### D-P2-05 · Settlement on derived items applies uniformly; stocks produce no cash leg
The open item S1 flagged. A derived item's formula result is its **accrual**; if
it carries a settlement, that settlement applies exactly as it does to a
generative accrual, with the period's start date as the accrual date. No special
case, no second code path. For `kind="stock"` the value is a *level*, not a
movement, so settling it is meaningless: a stock produces no cash leg and any
`settlement` on it is ignored. This keeps a frame filtered to `measure="cash"`
free of balances, which is what makes summing it meaningful.

### D-P2-06 · Formula item references default to the cash measure; stocks answer both with their level
PRD §5.4 gives `it()`, `agg()` and `cum()` no measure argument and does not say
which measure they read. The default is `cash`, because the engine forecasts cash
and the PRD's own canonical example — a cash balance folding over
`agg(tag="cat:revenue")` — is only correct against settled cash. `measure=
"accrual"` is one keyword away for P&L-style derivations. Exception, and the
reason it is stated here: a stock has no cash column, so `it`/`prev`/`agg`/`cum`
on a stock return its level for *either* measure. Without that, the canonical
`prev("cash")` would read zero.

### D-P2-07 · Two value kinds in the expression evaluator: exact rates and 4 dp money columns
Formula values are either a **rate** — a dimensionless scalar held as an exact
rational, from a literal or a param — or **money**, a 4 dp fixed-point column.
Rates stay exact until they meet money, which is what keeps a 22% VAT rate or a
3.1% escalation from drifting. Promotion rules, identical in both engines:
`+`/`-`/comparisons/`min`/`max`/`clip` convert a rate to money (rounding at 4 dp)
when the other side is money; `*` and `/` keep a rate operand exact and round
once at the end; rate-only arithmetic stays exact. `t.index` and `t.month` are
integer-valued *money* columns (so all numeric columns share one representation),
while `t.is_business_day` and `t.is_quarter_end` are masks. `t.is_business_day`
is evaluated on the period's start date, `t.is_quarter_end` on its inclusive end
date, and quarters follow `CalendarSpec.fiscal_year_start_month` rather than the
calendar — the field exists for a reason.

### D-P2-08 · A malformed book degrades one line, never the run
`compile_book` never raises on book content. An item with a rejected formula, an
unresolvable reference, an unknown param, a cross-currency aggregate or a
membership in an illegal cycle is marked *broken*: it evaluates to zero columns
and its diagnostic explains why. The alternative — aborting the run — would deny
an agent the diagnostics it needs to fix the book. Callers must check
`RunResult.diagnostics` for error severity before trusting numbers; the zero is
never presented as a computed value.

### D-P2-09 · A structurally invalid settlement produces no cash, plus an error
Per D-P1-07 the share-sum (CK-E004) and share/amount-mix (CK-E005) rules are the
SDK's to enforce, but the engine can be handed a book that never went through
`add_item()`. Rather than normalize the shares or invent a split, the engine
emits the error and produces **no** cash legs for that item; it still accrues.
Inventing a split the author did not write is exactly the silent numerical error
the project forbids. Also treated as CK-E005: fixed-amount terms with no
`remainder`, or with more than one.

### D-P2-10 · Kind/formula/segments consistency is reported as CK-E003
`kind="derived"`/`"stock"` requires a formula and forbids segments; `kind="flow"`
forbids a formula. The catalogue has no dedicated code, and CK-E003 ("Formula
rejected: {reason}") carries the reason precisely enough. Adding catalogue codes
was avoided throughout Phase 2–4: every condition the engine detects maps onto an
existing §10.1 code.

### D-P2-11 · Per-occurrence diagnostics are deduplicated per (code, item)
A monthly settlement clamp over a five-year horizon is one modelling fact, not
sixty. Both engines deduplicate identically — `RunResult.diagnostic_keys()` is
part of what the dual-engine gate compares, so a divergence in *which* problems
are reported fails the gate just like a divergent number.

### D-P2-12 · Escalation factors are applied as exact integer ratios
ADR-0002 says the Decimal factor is "converted to scaled int64 multipliers". A
scaled int64 multiplier rounds the factor itself, and a 4 dp rate raised to the
30th power has 120 exact decimal places — truncating it would reintroduce, in a
different disguise, exactly the drift the ADR removed. Implemented instead as the
factor's exact integer ratio (`Decimal.as_integer_ratio`), applied as
`round(value * numerator / denominator)` with arbitrary-precision intermediates.
This satisfies the ADR's intent (no float, one factor per distinct `(rate, n)`
pair, memoized) and strictly dominates its letter on exactness. The working
Decimal precision is sized to the exact digit count, so the factor is the true
value and not a rounded one.

### D-P2-13 · Cutover suppression lands in Phase 2; a suppressed occurrence loses its cash legs too
ADR-0004's blanket pre-cutover suppression is a property of generative expansion
and needs no ledger, so it is implemented here rather than deferred to Phase 5
(which owns only the *event* side of the union). An occurrence whose accrual date
falls before `cutover` is suppressed entirely, including cash legs that would
have landed after cutover: before cutover the ledger is the complete record, and
the ledger event carries its own settlement. Phase 5 adds the event union on top;
nothing else here anticipates it.

### D-P2-14 · VAT is not applied in Phases 2–3
The canonical rounding order (ADR-0003) ends with "VAT per line", and Phase 6
owns VAT and tax regimes. Phases 2–3 implement the chain through withholding and
leave the VAT step as a declared, documented gap: `Item.vat` is read by nobody,
and `VatSpec.rate` params are deliberately *not* resolved (only escalation rate
params are checked, as CK-E008), so the engine never implies VAT support it does
not have. The Phase 3 dual-engine corpus excludes VAT for the same reason. Phase
6 slots in at the declared position.

### D-P2-15 · Overflow raises; it is not a diagnostic
`MoneyOverflowError` is an exception, not a `Diagnostic`, because D-P1-06 already
bounds every authored amount at 9×10¹⁴ — reaching the int64 ceiling means a
pathological book or a programmer error, not something a user did wrong at the
boundary. Silent int64 wraparound is forbidden (PRD §5.3), so products widen to
Python ints whenever an int64 multiply could overflow, and additions are
magnitude-checked *before* the add, since numpy wraps silently and an
after-the-fact check would be worthless.

### D-P2-16 · `DueTerm.basis="period_end"` means the base-grain period's inclusive last day
`month_end` is already its own basis, so `period_end` must mean something else;
the only other period in scope is the book's base-grain period. At day grain that
coincides with the accrual date, which is why the distinction only becomes
visible on a coarser base grain. Withholding is computed as
`leg - round(leg * withholding)`, so the withheld amount is itself a clean
rounded number.

### D-P2-17 · `cum()` is a same-period dependency; the engine supports every base grain
`cum(x)` at period `t` reads `x[0..t]` inclusive, so it is a same-period edge in
the graph, not a lagged one. Separately, the engine partitions the horizon into
base-grain buckets stepping from `horizon.start`, which makes every `Grain` usable
as a base grain rather than rejecting all but DAY: occurrence and settlement dates
are computed as dates and then bucketed, so coarser grains need no separate
arithmetic. DAY remains the intended and tested default (D1).

### D-P2-18 · What the oracle shares, and what it must not
The reference engine shares the model, the formula front-end (ADR-0001), the
escalation factor table (ADR-0002 — byte-equality is unattainable if the two
engines consume different factors), the dependency graph and condensation
(structure, not arithmetic), and the recurrence-date generator. It **duplicates**
every arithmetic and rounding operation, segment amount computation, settlement
splitting, and formula-node evaluation, because that is where silent numerical
error lives. The reference rounds with `Decimal.quantize`; the vectorized engine
rounds with integer division. Double rounding in the reference is ruled out
rather than hoped away: multiplications run at a precision sized to the operands
(so the product is exact and only the 4 dp quantization rounds), and division runs
at 80 digits — a quotient of two 4 dp money values that lands exactly on a 4 dp
half-way boundary terminates within ~25 digits, and one that does not sits at
least 5×10⁻²³ away from a boundary, far beyond that precision's error.
`tests/test_numeric.py` pins the integer path against `Decimal.quantize`
directly, which is the contract the dual-engine gate rests on.

### D-P2-19 · Phase 1 defect fixed in place: U+FFFE/U+FFFF broke the round trip
Not a judgement call but a recorded cross-session repair, per the S2 brief's
"minimal change with its own commit message noting the reason". Mid-session,
Hypothesis found a `Scenario` whose `note` contained U+FFFE: the canonical
emitter passed it through raw and `yaml.safe_load` then refused the document, so
`parse(serialize(x)) == x` — the Phase 1 gate property — failed. The emitter's
escape table covered C0 controls, DEL, C1, the line/paragraph separators, the BOM
and the surrogates, but not the two BMP non-characters, which are the remaining
inputs a YAML reader rejects outright. Fixed by extending the `\uXXXX` branch in
`cashkit/model/canonical.py` to `0xFFFE..0xFFFF`, with
`tests/test_canonical_emitter.py::TestYamlForbiddenCharacters` now asserting the
round trip over *every* character PyYAML rejects, so the class cannot regress
character by character. Nothing else in `model/` was touched, and the committed
golden fixture is byte-unchanged. Flagged in the S2 handoff note because it is
S1's code, not S2's.

### D-P2-20 · `where()` always yields money, never a per-period rate
Recorded late: both engines already implement this (`columns.py`, `reference/
engine.py` cite it) but the entry itself was lost when Session S2's first agent
was interrupted mid-phase. A rate is exact and dimensionless; money is a 4 dp
column. `where(cond, a, b)` selects elementwise, so if it could return a rate its
result would have to be a *per-period* rate — a representation the column model
does not have, and one that would make the rounding boundary depend on which
branch each period took. `where` therefore coerces both branches to money before
selecting, which fixes the rounding boundary at the `where` itself for every
period. Consequence: `where(c, 0.1, 0.2) * x` rounds the fraction to 4 dp before
multiplying, unlike `0.1 * x`. Acceptable, and the alternative — a value kind
that is a rate in some periods and money in others — is not.

## Phase 3 — Graph, condensation, vectorized engine (Session S2)

### D-P3-01 · The sequential fold is staged, not interpreted
The fold is the one loop left in the engine, so per-node interpreter overhead
there is multiplied by the horizon length: walking the AST once per period cost
39 ms of the delta path's 39.5 ms budget of 5 ms. `cashkit/engine/fold.py` walks
each feedback member's expression **once** and returns a closure
`fn(t) -> (minor_units, zero_div)`; rate arithmetic, the rate-to-money
conversion, `prev()` init values, column resolution and rounding ratios are all
resolved during that walk. This is sound because the value *kind* of every node
is statically determined by the node type and never by a runtime value — and,
because a rate can only come from a literal, a param, or arithmetic on rates,
**every rate is a compile-time constant**. Delta went 39 ms → 4.5 ms, cold run
57 ms → 17 ms. The cost is a second implementation of the promotion rules inside
the vectorized engine; it is pinned by `tests/test_fold.py`, which compares the
staged closure against `ColumnEvaluator` cell-by-cell over every node type and
both rounding policies, and by the dual-engine gate against the Decimal oracle.
`ScalarKernel` is deliberately kept: it is the readable definition of the scalar
semantics and it is what the staged compiler is tested against.

### D-P3-02 · A delta run reports the full diagnostic set, not the recomputed one
Found by the Phase 3 gate: `Engine.delta` recomputed only the stale dependency
cone, so warnings raised by items *outside* the cone silently vanished from
`RunResult.diagnostics` — a `CK-W001` clamp that stopped being reported because
its item happened not to be recomputed. Runtime diagnostics are now bucketed per
item on the Engine and survive across evaluations; a stale item's bucket is
cleared and refilled, everything else is carried forward, and the output list is
compile diagnostics followed by the buckets in item order. The per-(code, item)
deduplication of D-P2-11 falls out of the bucketing unchanged. Ordering of
`RunResult.diagnostics` therefore differs from the reference engine's; only
`diagnostic_keys()` — which sorts — is compared across engines, and it is now
identical between a full run and a delta.

### D-P3-03 · Settlement splits into arithmetic and placement
`settle_occurrences` was one function doing both the leg arithmetic and the
calendar placement, which forced the fold to redo per period what is constant
per run. It is now `split_legs` (share/fixed split then withholding — positions
3 and 4 of ADR-0003) and `leg_targets` (offset, basis, business-day adjustment,
period lookup). A derived item accrues in every period, so its targets are a
fixed function of the period index: `FoldSettlement` resolves them once per run.
`split_legs` stays the single implementation of the arithmetic, called by both
paths with the same array code — the fold passes a one-element array rather than
getting a scalar twin that could drift. Immediate settlement (the common case
for a feedback item) bypasses the split entirely and is one guarded add.

### D-P3-04 · Memoize only immutable values; never cache an array column
Three memoizations carry the performance gate: `parse_formula` (the delta path
recompiles the whole book after a one-item change, and re-parsing 50 unchanged
formulas is pure waste), `exact_fraction` (`Decimal` → `Fraction`), and the
per-evaluator rate-constant cache. The last one is restricted to the scalar
kernel on purpose: an `ArrayKernel` cache would hand the same `ndarray` object
to two items, and a later in-place write to one column would corrupt the other.
The whole-horizon path evaluates each expression once anyway, so it has nothing
to gain and everything to lose. `EvalWindow` does cache the *resolved* column
per `(item, measure)`, which is safe for the opposite reason: the fold mutates
its columns in place and never rebinds them, so the array object is stable for
the window's life — and the staged compiler depends on exactly that invariant.

### D-P3-05 · The dual-engine corpus is deterministic and its coverage is derived
The gate names eleven features the corpus must cover. Two failure modes were
avoided deliberately: a corpus that drifts (so the random layer is seeded, and a
failing book can be rebuilt exactly by id), and a coverage claim that rots into
a stale list (so `corpus.coverage_of` re-derives what the books actually
exercise — by reading the segments, the classified settlements and the compiled
graphs — and the test asserts the required set is a subset of that). The corpus
is built in three layers: focus books for one idea each, cross-product sweeps
where a bug would hide in a single cell (anchor × business-day adjustment,
settlement basis × leg adjustment, recurrence unit, base grain), and seeded
random books. Six of the focus books are deliberately *broken*, because the two
engines must agree on which diagnostics a bad book produces and not only on the
numbers in a good one.

## Phase 4 — Formula language (Session S2)

### D-P4-01 · The whitelist is the translator; the call surface is an explicit table
An AST pre-pass listing legal node types would be a second definition of the
language, free to drift from the translator. Instead the translator is
exhaustive: every `ast` node it does not explicitly handle falls through to a
`CK-E003` naming the node type, so a node type is legal exactly when there is
code that translates it. `tests/test_formula_hardening.py` asserts the
complement — for every expression node type in the `ast` grammar, a sample
either translates or is rejected with a diagnostic, and a Python release adding
a node type fails that test rather than widening the language silently.

The *call* surface is the opposite: an explicit dict, not name-based dispatch.
It used to be `getattr(self, f"_call_{name}")`, which made every method of the
translator whose name began with that prefix reachable from a formula string —
`numeric(1, 2)` reached the variadic-builtin handler with the wrong signature
and raised `TypeError` out of the parser instead of returning a diagnostic. The
Phase 4 gate found it through a structural check that walks the parser's own
source for calls that could execute anything; the fix is `_CALL_TABLE`, and the
corpus now includes every translator method name.

### D-P4-02 · Three bounds on parse, because the failure is a crash and not a number
`ast.parse` on a deeply nested expression can exhaust the C stack before any of
our code runs; CPython raises `RecursionError` or `MemoryError` from inside the
parser, and both are caught and turned into `CK-E003`. On top of that the
translator enforces `MAX_AST_DEPTH`, and `parse_formula` refuses a source longer
than `MAX_FORMULA_LENGTH` (4096) before calling `ast.parse` at all — parsing a
100 kB "formula" is doing the attacker's work. Three bounds rather than one
because what they prevent is a hard failure of the process evaluating someone's
book, not a wrong number.

### D-P4-03 · Numeric literals and `prev(n=)` are bounded at parse, not at evaluation
`1e400` parsed happily and then raised `MoneyOverflowError` the moment it was
promoted to a money column — an exception on book content, which the error
policy forbids. Literals are now bounded at `MAX_LITERAL_MAGNITUDE` = 9x10^14,
the same ceiling the model puts on authored money (D-P1-06), and rejected with
`CK-E003` above it. `prev(n=...)` is bounded at `MAX_PREV_LAG` = 1,000,000 for
the same reason: an unbounded lag reaches numpy as a Python int too large for
int64 and raises there. Both bounds are generous enough that no real book can
meet them and tight enough that the failure is a diagnostic.

### D-P4-04 · A malformed param key is `CK-E007`, not `CK-E003`
The catalogue has a code for exactly this — "dotted or otherwise invalid param
key" — so `p.a.b` and `p.BAD` report `CK-E007` naming the key, while everything
else about a rejected formula stays `CK-E003`. `p.9bad` is not in that class:
it is a syntax error before any param key exists, and reporting it as a param
problem would misdirect the reader. Consistent with the Phase 2-4 rule of
mapping every detected condition onto an existing §10.1 code rather than
minting new ones (D-P2-10).

### D-P4-05 · `agg()` resolution is graph-build time; self-membership is a cycle
PRD §5.4 requires selectors to resolve to concrete ids at graph-build time.
Phase 2 already did this; Phase 4 pins the two edges with tests rather than
changing behaviour. A selector matching *nothing* is `CK-E001` — the reference
is unresolvable — while a selector matching the item that owns the formula is
`CK-E002` with the cycle spelled `item -> agg("...") -> item`, because that is
a self-dependency and §5.4 and the catalogue both describe it as one.
Resolution reads the book's tags as they stand at compile time, which is why
`Engine.delta` recompiles the graph after any item change: editing a tag moves
aggregate membership, and a stale membership would be a silently wrong number.

### D-P4-06 · "Never executes" is proved structurally as well as empirically
The empirical half is a recorder wrapped around every dangerous builtin the
corpus tries to reach; it must stay silent while the whole corpus is parsed.
The recorder *delegates* to the original rather than replacing it, because a
canary that swallowed calls would change the behaviour of everything else in
the process — an earlier version that replaced `__import__`, `compile` and
`type` outright hung the test session, which is a failure mode of the canary
and not evidence about the parser. The structural half walks the parser's own
source for calls to `eval`, `exec`, `__import__`, `compile`, `getattr` and
friends; it holds for inputs the corpus never thought of, and it is what caught
the name-based dispatch hole in D-P4-01.

## PRD conflicts

### C-P1-01 · `CalendarSpec.weekend` "ISO weekday indices" vs default `{5, 6}` = Sat/Sun
ISO weekday numbering is 1–7 with Sat=6/Sun=7, under which the stated default
`{5, 6}` would mean Fri/Sat. The default and its "Sat/Sun" label are
consistent only with Python `date.weekday()` 0-based numbering (Mon=0,
Sat=5, Sun=6). Implemented as Python `weekday()` indices 0–6, preserving the
PRD's stated default semantics; the "ISO" label is treated as a misnomer.

### C-P8-01 · §5.5's frame example shows an inclusive `period_end`; §4.0 makes it exclusive
The §5.5 sample row is `2026-03-01 | 2026-03-01` for a day-grain period, which
reads `period_end` as the period's last day. §4.0 defines `PeriodRange` as
`[start, end)` with "end: date # exclusive", and `PeriodIndex`, `RunResult.rows()`
and every date computation in the engine follow that. Implemented half-open
throughout: `period_end` is the first instant *after* the period, so a day-grain
row spanning 1 March reports `2026-03-01 | 2026-03-02` and a month bucket
reports `2026-03-01 | 2026-04-01`. Two end conventions in one system is a
silent-error class — a filter written for one and applied to the other is off by
exactly one period and looks right — so the §2 reading that keeps one convention
wins.

### C-S55-01 · §6.1 types `add_tax_regime` as `-> None`; §6.5 requires it to return diagnostics
"Errors are data, not exceptions. Every fallible operation returns
`Diagnostic(...)`" (§6.5) cannot hold for an operation whose return type has no
room for one, and adding a tax regime is fallible in the quietest possible way:
an `accumulates` selector matching nothing schedules nothing, produces no
liability item and moves no cash, while raising nothing. Implemented as
`-> ChangeReport`, the §6.5 reading, following the precedent D-P9-09 set for
`commit() -> Revision | None`. `None` remains recoverable from the report
(`ChangeReport.empty`), so nothing §6.1 promised is lost. Recorded as a conflict
rather than as a plain decision because the two sections give different return
types for the same call, which C-P8-01's standard makes a conflict.

## Phase 5 — Ledger and events (Session S3)

### D-P5-01 · The ledger is one append-only log, not a table per concept
Events, tombstones and corrections all live in `ledger_entries`, discriminated
by `kind` and ordered by one `AUTOINCREMENT` sequence. The alternative —
`events` plus a `voids` table — needs two orderings reconciled into one
watermark, and ADR-0006's `at(ref)` truncation is exactly a statement about a
single ordering: "the ledger as it stood at seq N". With one log, truncation is
`seq <= max_rowid` and nothing else, and a void that arrived after a revision is
invisible to that revision for free. `void_event` appends a `void` entry;
`correct_event` appends a `void` plus an `event`. There is no `DELETE` and no
`UPDATE` of an entry anywhere in `stores/ledger.py`, which
`tests/test_ledger.py` asserts structurally as well as behaviourally.

### D-P5-02 · Import identity is `(source, ext_id)`; the row's own `id` is excluded
PRD §6.2 keys idempotency on `(source, ext_id)`, but `import_events` takes
`Event` models, which also carry an `id`. If the id took part in the "identical
payload" comparison, a source that mints a fresh surrogate id per export would
turn every re-import into a conflict and abort every batch — the gate would pass
only for sources that happen to be stable in a field the PRD never made part of
the key. The payload fingerprint therefore substitutes a fixed placeholder for
`id` and compares everything else. Consequence: two rows differing only in `id`
are the same row, which is what `(source, ext_id)` already claimed.

### D-P5-03 · An import row with no `ext_id` aborts the batch (`CK-E017`)
`UNIQUE(source, ext_id)` is called "the only thing preventing double-counted
actuals on re-import" (PRD §4.3), and a row without `ext_id` has no key: import
it twice and it lands twice, silently. Rather than insert it anyway or skip it
quietly, the batch aborts with a diagnostic pointing at `add_event()`, which is
the right door for a one-off with no upstream identity. This makes
`import_events` idempotent by construction rather than by convention.

### D-P5-04 · Phase 5 mints five catalogue codes, `CK-E014`…`CK-E018`
Phases 2–4 deliberately minted none (D-P2-10), but ADR-0012 §5 explicitly defers
the referential rules to "codes assigned in Phase 5", and none of the §10.1 codes
describes them. Assigned: `CK-E014` target not found; `CK-E015` the row is not in
a state the operation can act on (already void, already corrected, missing note);
`CK-E016` `void_event` refusing a bare actual, whose `suggested_fix` names
`correct_event` (ADR-0012 §3); `CK-E017` an import row with no `ext_id`;
`CK-E018` an event attached to an item that cannot carry it. `CK-E010`'s
`suggested_fix` was rewritten to name `correct_event` (ADR-0012 §4) — the code
and its meaning are unchanged, which is what "codes never change meaning"
protects. `tests/test_diagnostics_catalogue.py` now asserts the catalogue equals
the PRD set *plus an explicitly enumerated additions set*, so growth stays
deliberate instead of accidental.

### D-P5-05 · A correcting row inherits `source` but never `ext_id`
The original row is tombstoned, not deleted, so it keeps occupying
`(source, ext_id)` in the unique index; a correction carrying the same key could
not be inserted at all. Giving the correction a mangled key (`<ext_id>~c1`) would
work but would make a later re-import of the erroneous upstream row insert it
afresh — a double count. Leaving the correction keyless means re-importing the
original is still an identical-payload *skip*, and the correction stands
untouched. `source` is kept for provenance.

### D-P5-06 · Correction ids are derived from the target: `<target>~cN`
`correct_event` takes a payload, not an id, and the ledger owns ids (D-P1-05).
Deriving the id makes the audit trail readable (`a1`, `a1~c1`, `a1~c1~c1` is the
whole story of a row) and deterministic — no counter, no clock, no randomness —
and the `N` suffix only ever advances because ids are never freed. Any `id` on
the supplied payload is ignored rather than honoured, since honouring it would
let a caller collide with an existing row.

### D-P5-07 · "Already corrected" is reported before "already void"
A corrected row is also a tombstone, so both referential rules fire. The
corrector check runs first because its message names the row that supersedes the
target; "already void" leaves the caller looking for it.

### D-P5-08 · Events are never suppressed by cutover
ADR-0004 suppresses *generation* before cutover and says ledger events in that
window "are taken as-is, whatever their status". Phase 2 already implemented the
generative half (D-P2-13), so Phase 5 adds only the event half and applies no
date filter at all to events: every live row applies, whatever its status and
whichever side of cutover it sits on. An `actual` dated on/after cutover raises
`CK-W003` and still counts. Filtering events by cutover here would double-suppress
the reconciled past and put a hole in the total-sum invariant the gate checks.

### D-P5-09 · An unattached event lands in a synthetic item keyed by its dimensions
The frame is one row per `(period, item, measure)` and tags live in an item
dimension table (PRD §5.5), so an event with no `item` has nowhere to be and
nothing `agg()` can match — which would violate non-negotiable #4 the moment a
book books bank fees as bare events. Unattached events are therefore grouped by
their resolved dimensions (tags, currency, settlement, VAT) into synthetic items
`_event:<sha256-16>`; the id is a function of the dimensions and not of the
events, so a thousand identical fee rows share one column and the id is stable
across imports and machines.

**Accepted limitation, flagged for Phase 8:** an *attached* event's own `tags`
(PRD §4.3: "merged over the item's; event wins on conflict") are row metadata and
do **not** move `agg()` membership, which stays the item's. Making them move it
would require the event to be its own dimension row, contradicting §5.5's
item-dimension design. The frame layer should surface per-event tags on the row
and `validate()` should warn when an event's tags would change the membership of
a selector its item does not match.

### D-P5-10 · Synthetic items are built with `model_construct`, keeping `ItemId` strict
ADR-0005 names synthetic items `_tax:<id>:liability`, and this phase adds
`_event:<digest>` — ids the `ItemId` grammar (`[a-z][a-z0-9_]*`) cannot
represent. Widening the grammar was rejected: the *reason* a synthetic id can
never collide with an authored one is precisely that authored ids cannot start
with `_`. Widening it to admit synthetics would destroy that guarantee and put
collision detection on the to-do list. Synthetic items are therefore constructed
with `Item.model_construct`, which skips validation — safe here because every
field value comes from an already-validated `Event` or `TaxRegime`. They are
engine-internal: never in `Book.items` as authored, never serialized.

### D-P5-11 · An event attached to a derived or stock item is `CK-E018`
A derived item's column is *written* by its formula, so an event added to it
before derived evaluation is silently overwritten a moment later. Refusing with
a diagnostic is the only honest option; the alternatives are a wrong number
(overwrite) or an ordering rule nobody could remember (add after evaluation, so
the formula does not see its own item's facts).

### D-P5-12 · An event dated outside the horizon is outside the model, cash legs included
The same rule generative occurrences already follow (D-P2-03). An invoice dated
before the horizon whose payment lands inside it is a real receivable and this
loses it; the pre-horizon world is represented by `opening_balance`, as it is for
generative items. Recorded as a known limitation rather than a decision anyone
should be happy with: when opening receivables matter, the answer is an opening
balance sheet, not a horizon that quietly leaks.

### D-P5-13 · The watermark's content hash covers voids, not only events
PRD §3.3 describes the hash as being "over `(source, ext_id, date, amount)`
rows". Taken literally, a ledger and the same ledger with a tombstone added hash
identically, and the cache key `(sha, scenario, engine_version, watermark)` would
call two materially different runs the same run. The hash therefore covers every
log entry including voids, with the entry kind in the digest.

### D-P5-14 · Row payloads are stored as model JSON; identity is a stored canonical-YAML digest
Two different jobs. *Rehydration* wants speed: `facts()` runs on every evaluation
and rebuilds every live row, and `yaml.safe_load` costs ~250 µs a row against
~7 µs for `model_validate_json` — 1,258 ms versus 32 ms for a 5,000-row ledger.
*Identity* wants the audited canonical form: the fingerprint that decides "same
payload or conflict" is a sha256 over `to_canonical_yaml`, whose escaping,
Decimal spelling and recorded-`None` handling were gated in Phase 1. Both are
exact — a `Decimal` is a string in either form. The digest is computed once at
insert and stored in its own column, so conflict detection never rehydrates a
row at all.

### D-P5-15 · `ImportReport` and `ChangeReport` live in `cashkit/model/reports.py`
The PRD gives them no home. They cannot live in `stores/` (Phase 7 returns
`ChangeReport` from scenario writes, which have no store), and putting them in
`sdk/` would make `stores/` import from the layer above it. They are frozen
Pydantic result models carrying `Diagnostic`s, which is exactly what
`cashkit/model/` already holds, so they go there. `ImportReport` subclasses
`ChangeReport`: an import *is* a write that reports what it recorded.

### D-P5-16 · The engine takes events as a sequence, never a store
`run(book, events=...)` and `Engine(book, policy, events)` accept a plain tuple
of `Event` models. Nothing in `engine/` or `reference/` knows a ledger exists,
so the storage layer stays swappable (constraint 3 of the ambiguity rule) and
the dual-engine gate can build ledgers in memory without a database. Assembling
the sequence — tombstones excluded, corrections included, watermark applied — is
`LedgerStore.facts()`'s job.

### D-P5-17 · The fact union happens before graph construction, not before evaluation
S2's handoff says events must enter where `Engine._evaluate` expands generative
items. That is true for the *numbers*, but synthetic carriers (D-P5-09) have to
exist earlier still: `agg()` selectors resolve to concrete item ids at
graph-build time (D-P4-05), so a carrier created after `compile_book` would be
invisible to every aggregate in the book. Both engines therefore resolve facts
first, augment the book's item map, and compile that. The numeric union stays
where the handoff put it — after expansion, before the component loop.

## Phase 6 — VAT and tax regimes (Session S3)

### D-P6-01 · VAT is computed per line and *allocated* across the cash legs
ADR-0003 puts VAT last in the canonical order, but not whether "per line" means
per accrual occurrence or per settlement leg. Per leg (`round(leg × rate)`) is
simpler and wrong in a way that matters: the legs' VAT would not sum to the VAT
the invoice states, so cash collected would differ from VAT remitted by
fractions of a cent, and accrual-basis and cash-basis totals would disagree for
the same line. VAT is therefore computed once per occurrence — one rounding —
and split across the legs in proportion, with the **last leg absorbing the
residual**, which is exactly the rule ADR-0003 already fixes for the share
split. Consequence: `Σ leg VAT == line VAT` exactly, always.

Proportions are taken against the **sum of the legs**, not against the accrual.
They are the same number for a share split (legs sum to the accrual by
construction) but not for fixed terms whose remainder clamps to zero, where
dividing by the accrual would hand a leg more VAT than the line carries.

### D-P6-02 · VAT is computed on the taxable amount, not on what withholding leaves
The canonical order runs `… → settlement share split → withholding → VAT`,
which read strictly would apply VAT to the amount net of the ritenuta. That is
wrong twice over: a ritenuta d'acconto has never reduced the VAT on an invoice,
and the Italian arithmetic everyone knows is 1,000 + 220 VAT − 200 ritenuta =
1,020 collected. Withholding and VAT are two *independent* adjustments to the
same taxable base; VAT's position in the order fixes it as the last **rounding
boundary**, not as an operation on withholding's output. `split_legs` therefore
returns both the pre-withholding legs (which VAT rides) and the post-withholding
legs (which move cash). Read as a clarification of ADR-0003, not a deviation.

### D-P6-03 · The output/input side follows `direction`, falling back to the sign
`Item.direction` is "display only; storage is signed" (PRD §4.2), but VAT needs
to know whether a line is a sale or a purchase, and the sign cannot answer it: a
credit note against a sale is negative and is still a sale. Classifying it by
sign would move it to the input side and reclaim VAT that was never paid.
`direction` therefore decides when set, and the sign decides when it is not —
right for the ordinary case where revenue is positive and costs negative, and
`add_item()` already rejects amounts whose sign contradicts a stated direction
(CK-E011), so the two can never disagree on a book built through the SDK.

### D-P6-04 · What each treatment does, and why four of them are the same
Only two treatments produce numbers. `standard` charges VAT, grosses up the cash
leg, and books output or input VAT by side. `reverse_charge` on a *purchase*
self-accounts: the same VAT is booked as output (owed) and as recoverable input,
so a fully deductible purchase nets to zero and a partly deductible one leaves
the non-recoverable part payable — which is the correct Italian answer, and the
reason the treatment cannot be modelled as "no VAT". A reverse-charge *sale*
carries no VAT at all. `exempt`, `out_of_scope`, `export` and `split_payment`
all produce no VAT cash leg and no regime liability and are, in v1,
observationally identical; split payment because the buyer remits the VAT to
the state directly, so the supplier never owes it (PRD §7.2). They stay distinct
values because a return form distinguishes them and the authored intent is worth
keeping for the reporting this engine does not yet do.

### D-P6-05 · The regime's contributions are signed, so the net is a sum
Output and input VAT are stored as *signed contributions to the liability*
rather than as magnitudes: a sale's output VAT is positive, a purchase's input
VAT negative, and a credit note is the mirror of the line it reverses. The
period's net is then `output + input` with no sign gymnastics and no `abs()`,
and a credit note on the sales side correctly reduces output VAT instead of
appearing as input VAT.

### D-P6-06 · Return periods follow `fiscal_year_start_month`, and an open one recognises nothing
The PRD gives `periodicity` but never says what a quarter is phased on. Quarters
follow the fiscal year, like every other quarter in the system (D-P2-07); with
the default January start these are the calendar quarters an Italian entity
files on. A return period that does not *close* inside the horizon recognises
nothing — the return is not due yet, and inventing a payment for it would put an
obligation in the forecast that nobody owes. A period that closes inside the
horizon but opened before it accumulates only what the horizon contains,
consistent with the pre-horizon world being represented by `opening_balance`
(D-P2-03).

### D-P6-07 · Tax items are graph nodes whose columns come from a schedule, not a formula
ADR-0005 requires the regime to be *in* the graph before condensation, and the
formula language cannot express "net the quarter and pay 16 days after it ends"
— `agg()` and `prev()` are per-period, and a return is not. So `_tax:<id>:credit`
and `_tax:<id>:liability` are compiled items with `expr=None` whose columns are
filled by a fold over *return* periods (twenty of them in a five-year day-grain
book, not 1,826). The credit depends on the regime's base; the liability depends
on the credit it is netted against — one direction, no artificial cycle — and
both are computed when the credit's component comes up.

What ADR-0005 actually wanted from graph membership is preserved: the cash fold
sees tax payments like any other flow, and `trace()` will explain a tax number
with the same machinery as everything else.

The two items are tagged `cat:tax`, the convention §9.5 already asks *manual*
tax items to follow, so `agg(tag="cat:tax")` pulls every tax outflow — native
and manual — into a cash balance. They carry no flags: inventing a flag name
would only work for books that happened to guess it.

`compile_book` keeps a guard (`CK-E019`) for a tax node caught in a non-trivial
component. Its ordinary path is `CK-E002`, because a base item that reads the
regime back closes a *same-period* cycle and the existing cycle check finds it
first; the guard covers the case a future lagged edge could create. A refused
regime produces zero columns rather than a plausible-looking half-answer.

### D-P6-08 · `Diagnostic.item_id` had to widen to name synthetic items
Found by the Phase 6 gate, fixed in its own commit. `Diagnostic.item_id` was
typed `ItemId`, whose grammar deliberately excludes `_tax:…` and `_event:…`, so
*any* diagnostic about a synthetic item raised `ValidationError` out of the
engine — a regime caught in a cycle, or an unattached event whose settlement
shares did not sum to 1, crashed the run instead of reporting it. Errors are
data (PRD §6.5), so the field now accepts the authored grammar plus the
synthetic one. `ItemId` itself is unchanged: authored ids still cannot start
with `_`, which is precisely why a synthetic id can never collide.

### D-P6-09 · A regime accumulates items, so an event's VAT reaches it only through its item
An event may override its item's `VatSpec` (PRD §4.3), and the override is
honoured — the VAT is computed and grosses up the event's cash leg. Whether it
reaches a regime is decided by the regime's base, exactly as for the item's own
VAT: a default base is "every item carrying a VatSpec", so an event that
introduces VAT to an item that has none is computed but not accumulated. This is
one rule applied consistently rather than a special case, and the remedy is to
give the item a `VatSpec` (an `exempt` one costs nothing) so it joins the base.
Unattached events are unaffected: their synthetic carrier holds the event's own
`VatSpec` and is therefore in the default base already.

### D-P6-10 · `refund_annual` is implemented; without a month it is refused
PRD §4.5 says a credit zeroes out "only on annual adjustment or refund claim"
but leaves the mechanics open. Implemented: at the return period whose end falls
in `annual_adjustment_month`, any outstanding credit becomes a cash inflow at
that period's payment date and the stock zeroes. `refund_annual` without an
`annual_adjustment_month` names no date for the claim, so the regime is refused
with `CK-E019` rather than silently behaving as `carry` — the two produce
materially different cash and guessing between them is exactly the class of
silent error this project forbids.

### D-P6-11 · VAT columns are part of the run result, and part of the dual-engine gate
Four columns per VAT-bearing item — output and input, on each tax point — ride
on `RunResult` and are compared byte-for-byte between the engines. Without them
the gate would pass for two engines that agreed on every cash cell while
disagreeing about which return period a line's VAT fell into: the right bank
balance and the wrong F24. Every item with a resolvable `VatSpec` reports
columns, zero or not, so the two engines report the same key set.

## Phase 7 — Scenarios (Session S4)

### D-P7-01 · Scenario resolution lives in `sdk/`, in memory, storage-free
Resolution is behaviour over models, so it is not `model/`; it never evaluates
anything, so it is not `engine/`; and it must not depend on where scenarios are
persisted, so it is not `stores/`. `cashkit/sdk/scenarios.py` holds
`ScenarioSet` — the authored book plus a dict of `Scenario`s — and every §6.3
operation is a method on it. Persisting the set is the config store's job
(Session S5), and nothing here knows a file exists. Consequence the gate rests
on: the whole phase is testable without a filesystem.

### D-P7-02 · Phase 7 mints four catalogue codes, `CK-E021`…`CK-E024`
Each is something an agent can plausibly do through the §6.3 surface, so none of
them may be an exception (PRD §6.5), and no §10.1 code describes a
scenario-graph or overlay-resolution failure. `CK-E021` an unknown scenario id —
a fork parent, a write target, or a broken chain link, with the reason naming
which; `CK-E022` a scenario id already taken, raised by `fork` and `flatten`;
`CK-E023` an overlay targeting an item its parent chain does not define;
`CK-E024` the reserved `opening_balance` param carrying a value that is not
money. `CK-E001`'s message is fixed at "unknown reference at graph build",
which would misdirect a reader of a scenario problem, so reusing it was
rejected. `tests/test_diagnostics_catalogue.py` enumerates all four.

### D-P7-03 · `ChangeReport.changed` means "the fields whose *record* moved"
PRD §6.3 says the report returns "the fields actually recorded as different".
Read literally: `changed` lists the fields whose record in this scenario changed
— appeared, disappeared, or changed value — which is empty exactly when nothing
is written. The alternative reading ("fields whose resolved value moved") breaks
on one real case: an override that becomes redundant because base was corrected
to match it. Its resolved value does not move, but dropping the record does
change behaviour — a later base correction now propagates where it previously
did not (ADR-0009) — so calling that write empty would be a lie. Under the
chosen reading the gate case is exact: writing the currently resolved item
produces `changed=()`, `CK-I002`, and a scenario byte-identical to a freshly
forked one.

### D-P7-04 · Order inside one scenario: `removed`, then `added`, then `items`
The three fields can name the same id and the PRD never orders them. Removals
apply first, so `added` wins over `removed` in the same scenario — you removed
the parent's version and authored a new one, which is the only reading in which
both records mean something. An overlay on an id the same scenario removed is
contradictory and reports `CK-E023` rather than being silently ignored. Through
the SDK the contradiction cannot arise: `set_item` clears the id from `removed`,
`remove_item` clears any overlay. This also settles what D-P1-13 deferred — a
descendant re-`added` an ancestor removed reinstates the item, because
resolution walks root to leaf and the descendant is nearer.

### D-P7-05 · A book carrying engine-synthesized items is refused with an exception
`Engine.book` is the *augmented* book: `_event:<digest>` carriers and
`_tax:<regime>:*` items that no one authored (D-P5-09, D-P5-10). An overlay
recording one would resurrect a value the next compile recomputes. `ScenarioSet`
therefore refuses such a book at construction with a `ValueError` naming the ids
— this is programmer error (the SDK never hands an agent `Engine.book`), not
something a user did wrong, so it is the one place in this phase that raises
instead of diagnosing. The check is a regex over the synthetic id grammar, which
is exactly the grammar authored `ItemId`s cannot express.

### D-P7-06 · `flatten` produces `parent=None` against the authored book
"Collapse chain to standalone" (PRD §6.3) has to say what the flattened
scenario resolves *against*. Base's content is the top-level book (ADR-0007), so
the only substrate any scenario ever has is that book; "standalone" therefore
means "depends on no other scenario", not "carries a whole book". A flattened
scenario is shaped exactly like base — `parent=None`, overlays over the authored
items — which is what keeps it an ordinary scenario that can be forked again
rather than a second kind of object.

### D-P7-07 · `fork` and `flatten` return `ChangeReport`, not a bare ref
PRD §6.3 types them `-> ScenarioRef`, but a ref cannot carry `CK-E021`/`CK-E022`
and the error policy forbids raising for them. Following the precedent S3 set
with `add_event` (typed `-> EventRef` in §6.2, implemented as `ChangeReport`),
they return a `ChangeReport` whose `target` and `created` name the new scenario.
One return type across the whole write surface also means an agent loops on one
shape.

### D-P7-08 · Macros round authored money at the authoring boundary
`ScaleItems(factor=0.333)` produces amounts with more than 4 decimal places, and
`Money` rejects those at the door (D-P1-06). The macro therefore quantizes to
4 dp itself, half-up by default (matching the engine default, D-P2-01) with a
`banker` flag for a book running the other policy. The stored amount is then
exactly what a human would have typed, which is what "post-macro state is
indistinguishable from typing the items out" requires — and the engine never has
to round an authored value silently. `probability` and `escalation` are not
scaled: scaling revenue by 0.8 is a statement about amounts, not about how
likely they are or how they grow.

### D-P7-09 · `diff()` also covers `opening_balance` and event overrides
PRD §6.3 says the diff is semantic and computed from resolved books. Params and
items follow directly. `opening_balance` is included because the reserved param
moves a Book field, not just a param, and a diff that reported the param but not
the balance would be reporting the cause and hiding the effect. Event overrides
are included even though they are not part of the resolved *book*: two scenarios
differing only in what they override on the ledger are materially different, and
a diff blind to that would answer "nothing changed" about a changed forecast.
The comparison is over the chain-resolved overlays, so it needs no ledger.

### D-P7-10 · Event overrides resolve against a ledger sequence, never into it
`resolve_events(scenario, events)` applies the chain's merged `EventOverlay`s to
the sequence the ledger hands over and returns a new sequence. Nothing is
written back: a scenario is a view over the ledger, and the ledger is
append-only and shared by every scenario. `CK-E006` lives here — the one
remaining actual-immutability case D-P1-08 left open, an overlay *targeting* a
row whose ledger status is actual — and the row passes through untouched.
Fabricating an actual stays unrepresentable at the type level. An overlay naming
a row the ledger does not hold reports `CK-E014`, the code Phase 5 already
minted for exactly that.

### D-P7-11 · Pinning a value equal to the parent's is deliberately not expressible
Recordedness is what decides propagation, so recording `tags` with base's own
value would pin it against a later base correction. `set_item` records only
fields that *differ* from the resolved parent (D4, ADR-0009), so that state
cannot be authored by value — and the by-value pipeline is the whole API. An
agent that wants a value frozen against upstream change has to make it differ,
which is honest: a pin that looks identical to the thing it is pinned against is
invisible in every diff and every review.

### D-P7-12 · Change paths in `ChangeReport.changed`
Bare Item field names for `set_item` (`"tags"`, `"segments"`); `"params.<key>"`
for `set_param`; the Scenario field name for a presence change (`"added"`,
`"removed"`) since that is the record that moved; `"<item_id>.<field>"` for
`apply_macro`, which spans items and would otherwise report ambiguous names.

### D-P7-13 · The reserved `opening_balance` param is money-checked twice
PRD §4.1 makes `opening_balance` a param key that overrides the Book field, but
`params` values are unbounded-precision `FiniteDecimal` while the field is
`Money` (≤ 4 dp, bounded). `Book.model_copy` does not revalidate, so an invalid
value would sail into the engine and raise from `to_minor` mid-run. It is
checked at `set_param` (refusing with `CK-E024` before anything is recorded) and
again at resolve, because a hand-authored scenario file never passed through
`set_param`. On failure the authored balance stands and the error is reported,
per D-P2-08: degrade one value, never the run.

### D-P7-14 · `sdk/` joins the wall-clock and no-float lints
Neither lint covered `sdk/` because it was empty. A `ShiftItems` macro reading
the clock would make a resolved book depend on when it was resolved — the same
reproducibility failure as `date.today()` in the engine, through a different
door — and macros do arithmetic on authored money. Both lints now cover
`model/`, `engine/`, `reference/`, `sdk/` and `stores/`.

## Phase 8 — Frame store and views (Session S4)

### D-P8-01 · One module imports duckdb, and the protocol is the swappability guarantee
`FrameStore` is a `Protocol`; `DuckdbFrameStore` implements it; `duckdb` appears
in `cashkit/stores/frames.py` and nowhere else in the package, which
`tests/test_frames.py` asserts by walking every module's imports. This is the
same shape S3 gave the ledger (`sqlite3` in one file), and it is what PRD §3.4
means by "the FrameStore protocol must abstract both": Parquet export is the
stable sharing path, Quack is optional and not load-bearing, and nothing above
this line may assume either.

### D-P8-02 · `bucket_of` lives in `engine/calendars.py`, not in the store
Additive change to `engine/`, in its own commit per the session brief. Grain
buckets are calendar arithmetic and they have to agree with
`PeriodIndex.is_quarter_end` and the VAT return periods on what a quarter is
(D-P2-07, D-P6-06). A second statement of the fiscal convention inside the
frame store is precisely how the two would drift, and the drift would be
invisible: the frame would just quietly disagree with the F24 schedule about
which quarter a number belongs to. Putting the function next to the definition
it must match is the cheapest available guarantee. It also keeps `summary()`
free of the DuckDB extra (D-P8-11).

### D-P8-03 · Aggregation buckets are calendar-aligned, not horizon-aligned
`PeriodIndex.build` steps base-grain periods from `horizon.start`, because the
base grain defines the model's own periods. Aggregating a frame is a different
job: someone asking for monthly totals wants calendar months, and quarters and
years follow `fiscal_year_start_month` like everything else in the system.
Weeks start on Monday, matching the `date.weekday()` numbering (C-P1-01).
Buckets are **not clipped** to the horizon, so a horizon opening on 15 March
reports a March bucket running to 1 April: the bucket names a calendar period,
and a partial one at the edge is information rather than an error. For the
normal case — a horizon starting on 1 January with a January fiscal year — the
two schemes coincide, which is asserted rather than assumed.

### D-P8-04 · A stock never sums
PRD §5.5 states the rule twice and the two statements can disagree: "aggregation
respects `Item.agg_rule`" and "flows sum, stocks take last-in-period", while
`agg_rule` defaults to `"sum"` for every item including stocks. Resolved by
`effective_agg_rule`: a stock left at the default resolves to `last`, because a
balance added up over thirty-one days is not a quantity anyone has a use for. An
*explicitly* different rule on a stock is honoured — `mean` on a balance is the
average balance over the bucket, which is a real thing to want. The effective
rule is what gets materialized, so a reader of `frame_items` sees the rule that
was applied and not the one that was authored-by-default.

### D-P8-05 · `mean` is the only aggregation that rounds, and it rounds like the engine
`sum` over `DECIMAL(18,4)` is exact and stays in SQL. `last` is `arg_max` over
the period index and is exact. `mean` is a division, so it needs a declared
rounding policy — and a SQL division's rounding mode is the database's business,
not ours. The query therefore returns `(sum, count, last)` and the rule is
applied in one place in Python, with `mean` going through
`engine.numeric.round_div` in int64 minor units under the store's policy
(half-up by default, matching D-P2-01). Consequence: aggregation arithmetic is
identical to the engine's, and swapping the backend cannot change a number.

### D-P8-06 · `status` stays what the run reported; per-cell status needs an engine change
S3 handed Phase 8 the note that `RunResult.rows()` stamps every row
`status="forecast"` and that the frame layer should carry the real status. It
does not, and the reason is worth stating precisely rather than leaving as a
silence.

A cell is a *sum* of contributions that can have different statuses: post-cutover
an item's period can hold a generated occurrence and an `actual` event, and a
pre-cutover actual's settlement leg can land in a post-cutover cash cell
(D-P5-08, D-P5-12). Splitting the value by status therefore requires knowing
which legs landed where, which is settlement arithmetic. Two ways to get it:
have the engine emit per-status columns, or re-derive leg placement inside the
store. The first is an engine change touching `settle_occurrences`, both engines
and the byte-equality gate, which is out of this session's scope. The second is
a second implementation of the arithmetic that the dual-engine gate exists to
prevent — it would drift, and it would drift silently.

So: the frame's grain already includes `status`, `frame(status=...)` filters the
column that exists, and the honest answer is that there is one status per run
until the engine reports more. Flagged for whoever owns the engine next. The
alternative considered and rejected was deriving status from the cutover
boundary, which is exact for accrual and wrong for cash — and "wrong only for
cash" is the worst possible place for it to be wrong.

### D-P8-07 · Minor units reach `DECIMAL(18,4)` through their decimal string, never a division
The engine hands over int64 minor units at 4 dp and the column is
`DECIMAL(18,4)`, so something has to divide by 10⁴. DuckDB's decimal division
does not: `999999999999999999::DECIMAL(38,4) / 10000` returns
`100000000000000.000000` instead of `99999999999999.9999`. A money column that
is exact except for large numbers is exactly the failure this project exists to
prevent, so the conversion is done through the decimal *string*
(`sign || minor//10000 || '.' || lpad(minor%10000, 4)`), which is exact for
every int64 and — measured — faster than the multiplication alternative
(19 ms versus 85 ms for 182,600 rows).

Separately: `Money` permits magnitudes up to 9×10¹⁴ units, which `DECIMAL(18,4)`
**cannot** hold (its ceiling is ~10¹⁴). There is no conflict in practice because
`check_column`'s addition-safe ceiling bounds every stored column at
2.25×10¹⁵ *minor* units — 444× below what `DECIMAL(18,4)` holds. The
materializer still checks each block and reports `CK-E020` rather than letting
DuckDB decide, because "unreachable" is a claim and this one is worth a test
(`test_decimal_18_4_can_hold_every_value_the_engine_can_produce`).

### D-P8-08 · Facts go in column-wise; dimensions go in as literal SQL
DuckDB's Python parameter binding costs the better part of a millisecond per
*value*: `executemany` over 182,600 fact rows took 4.7 s, and one statement with
1.3 million placeholders took 3.7 s — against a 200 ms budget (PRD §5.2). Two
paths replaced it. The fact table is handed over as numpy int64 arrays through
`register()` and joined to a tiny ordinal table, so no fact value is ever
converted in Python. The dimension tables go in as literal SQL, with every
literal rendered by `_literal` (strings quoted by doubling the quote, Decimals
unquoted so DuckDB parses fixed-point, no rendering for `float` at all).

One exception was forced by measurement: the *period* dimension is 1,826 rows of
ten dates, and 18,260 `DATE` literals cost DuckDB's parser 130 ms — more than the
entire fact table costs its executor. Periods therefore also go through
`register()`, as `datetime64[s]` arrays cast to `DATE` on the way in
(`datetime64[D]` is refused by DuckDB outright). Result: 122 ms for the whole
PRD §5.2 shape, inside the budget.

### D-P8-09 · `Table` is a dependency-free carrier, not a DataFrame
PRD §6.2 and §6.4 type five methods `-> Table` without defining it. Adding
pandas or polars to the core install to hand back seven columns would be a
dependency an agent never asked for and a second money representation to police.
`Table` is a frozen dataclass of `columns` and `rows` holding already-converted
Python values — money as `Decimal` — and anyone who wants a DataFrame builds one
in a line. It lives in `cashkit/model/` for the reason `reports.py` does
(D-P5-15): both the stores and the SDK return it.

### D-P8-10 · Synthetic items are materialized and flagged, never dropped
`_tax:<regime>:*` carries real cash and `_event:<digest>` carries real ledger
rows, so a frame that dropped them would not sum to the model. They are written
with `synthetic=true` on the item dimension, and `frame(include_synthetic=False)`
excludes them deliberately. The item dimension is built from
`set(result.accrual) | set(book.items)` rather than from the book alone: an item
with a column but no dimension row would be dropped by every join in the module,
silently. This is the mirror image of D-P7-05 — the *engine's* book is the right
input here, and the authored book is the right input there.

### D-P8-11 · `summary()` is not behind the DuckDB extra
PRD §8.2 gates "frame store, aggregation, Parquet export" on `cashkit[duckdb]`.
"When do we run out of cash" is not any of those, and it is the question the
system exists to answer, so `summary()` computes from the engine's int64 columns
in `cashkit/sdk/views.py` with no `duckdb` and no `cashkit.stores` import —
asserted structurally, because a convenience import would silently make the core
install unable to answer it.

The auto-derived balance is `opening_balance + cumulative cash over every
non-stock item`, which is the `net[t]` fold of PRD §5.1 and is well-defined for
any book; `balance="<item_id>"` reads a designated balance item instead, and the
two are asserted equal on a book that models the balance with `prev()`. Stated
plainly in the docstring rather than hidden: the auto derivation counts a derived
item that re-aggregates settled items and carries its own settlement **twice**,
because as the book is written that is two cash legs. `balance_source` is on the
result so no reader has to guess which derivation produced the number.

### D-P8-12 · Min cash and runway are read at base grain, whatever grain is reported
A trough that opens and closes inside one month is invisible in the month's
closing balance, and it is exactly the trough that empties an account. `min_cash`
and `runway_end` are therefore always computed over the base-grain series even
when `grain=MONTH` is reported; only `breakeven` is grain-relative, because
"is the business sustainably cash-positive" is genuinely a question about the
reporting period. The test asserts that the tidy bucket-close answer would have
been *later* than the reported one for the fixture book, so this is a statement
about behaviour and not a tautology.

### D-P8-13 · A pivot puts untagged items in an explicit `(untagged)` column
`pivot(columns="tag:customer")` on a book where some items carry no `customer`
tag has to do something with them. Dropping them makes the pivot's columns fail
to sum back to the frame — a quiet way to lose money in a view an agent will
present as a summary. They go into a column named `(untagged)`, and the test
asserts the columns reconcile to the frame total.

### D-P8-14 · `compare` reports `None`, not zero, for a period a run does not cover
Runs with different horizons align on the period, and a period one run never
evaluated is not a period where it produced zero. Zero would be a number someone
could sum.

### D-P8-15 · `duckdb` joins the dev dependency group
The Phase 8 gate is about aggregation, tag slicing and a Parquet round trip;
none of it is provable with the extra uninstalled, and a gate that skips is not
a gate. `duckdb` stays an *optional* runtime extra per PRD §8.2 and becomes a
required *development* one. `pyarrow` was deliberately not added: DuckDB reads
and writes Parquet natively, so the export path has no second library in it.

## Phase 9 — Version control (Session S5)

### D-P9-01 · The revision store is four operations over text keyed by path
ADR-0018 requires the interface before the git store. The question it leaves
open is what a "state" is at the seam. `RevisionState` is `Mapping[str, str]` —
book-root-relative path to canonical text — which is the least structured thing
that still round-trips the §3.3 layout. Anything richer would leak the config
store's vocabulary into the revision store (which would then have to know what a
Book is to store one), and anything poorer would make `read_state` unable to
answer without a second call per file. The four operations are exactly §6.6's
needs: write a revision from a state, list revisions, read a state at a
revision, diff two revisions.

The second implementation ADR-0018 demands be *plausible on paper* is an
append-only SQLite revision table: one row per revision (`id`, `parent`,
`message`, `author`, `timestamp`, `depth`, metadata JSON) and one blob row per
`(revision, path)`. Every method above is a query against those two tables;
`HEAD~n` is `depth = head_depth - n`, which is why `Revision` carries `depth` at
all. Nothing in the interface needs a commit graph, content addressing or a
merge base — and nothing in it *has* one, which is the test.

### D-P9-02 · Refs are opaque strings with a linear grammar, parsed once
Non-negotiable 7 says no method takes a git ref-spec "other than the opaque
`ref` string", and the Phase 9 gate is literally `at("HEAD~5")`. Those two only
reconcile if `HEAD~n` is CashKit's grammar rather than git's. `parse_ref` in the
interface module is therefore the single definition — `HEAD`, `HEAD~<n>`, or a
revision id — shared by every implementation so none of them can drift into a
dialect. `HEAD^`, `HEAD@{2}` and every other git spelling is **refused**, which
is a deliberate reduction in expressiveness: an append-only revision table can
answer `HEAD~n` and cannot answer `HEAD^2`, and an interface that quietly
admitted the second would be a git wrapper.

### D-P9-03 · History is linear; there is no merge path, not even a private one
v1 defers branch-based propose-and-review (PRD §7.3), the writer lock makes
concurrent divergence an error rather than a state, and the Phase 9 gate demands
that a second writer "never merges silently". The strongest way to guarantee
that is for no merge to be expressible: `write_revision` always parents on the
current tip and passes exactly one parent, the store walks first-parent only,
and there is no branch API. The second writer cannot merge silently because
there is no code that merges at all.

### D-P9-04 · `.cashkit/config.toml` is tracked, holds engine settings, and closes D-P2-01
Open since S2: where the rounding policy is stored. §3.3 names `config.toml` as
the home for "store backends, engine settings" and lists the tracked paths
without mentioning it either way. The two halves of that file have opposite
requirements — backends are machine-local, engine settings change every number —
so they cannot share a tracking decision.

Resolution: `config.toml` holds **engine settings only** and is tracked. Store
backends become constructor arguments and CLI flags, so nothing machine-local is
committed. The rounding policy must be tracked because a run is identified by
`(revision, scenario, engine_version, ledger_watermark)` and `at("HEAD~5")` has
to recover all four from the revision; a policy living in an untracked file
would reproduce the wrong numbers on a machine whose local settings differ,
silently, which is the exact failure this project ranks worst.

The alternative — a `Book.rounding_policy` field — was rejected on cost, not on
principle: it changes the canonical serialization S1's gate pins and the
committed `tests/fixtures/canonical_book.yaml`, to put a setting on the model
that no formula and no item references. If a later phase needs the policy inside
the model (a per-book default an overlay could sweep, say), the migration path
now exists to add it.

### D-P9-05 · The working tree on disk is the working state
PRD §6.7 says exploratory sweeping stays in memory, and §8.4 lists
`cashkit status` as a shell command. Those are only compatible if "the working
state" is something a *second process* can see. So: mutations are in memory,
`save()` writes the §3.3 layout, `commit()` writes it and records a revision, and
`open()` reads it back. `status()` compares the in-memory state to HEAD;
`discard()` restores from HEAD. A long-lived REPL session sweeping parameters
touches no file until it decides to, and a CLI invocation — which loads from disk
and exits — sees exactly what was last saved.

`write_working_tree` removes tracked files the state does not contain, so the
tree is the state rather than a superset of it. An item deleted in memory and
then saved must not come back on the next `open()`, and it would if the writer
only ever wrote.

### D-P9-06 · One wall-clock read in the package, allowlisted and fenced
A commit and a writer lock need a timestamp; ADR-0010's lint bans the clock in
`engine/`, `model/`, `reference/`, `sdk/` and `stores/`. Three ways out: put the
clock in a directory the lint does not cover (a loophole that invites the next
one), carve a per-call exemption (a ban that decays into a habit), or name a
single file.

`cashkit/stores/clock.py` is that file. The lint was **widened** to the whole
package — `cli/` and anything added later are now covered by default — and
allowlists exactly one path, with a companion test asserting that only
`git_store.py` and `lock.py` reach `wall_clock()`. Importing the `Timestamp`
*type* is unrestricted, because a signature that accepts an injected timestamp is
the opposite of a module that reads one: every commit and lock takes
`timestamp=`, which is also what makes a fixture repository byte-reproducible.

The clock is built from `time.time_ns()` — integer nanoseconds — rather than
`time.time()`, so the identifier `float` still appears nowhere under `cashkit/`
except the boundary guard that rejects it. A timestamp is not money, but an
exception carved for one non-money value is an exception that gets cited for the
next one.

### D-P9-07 · Three schema generations, and what each of them changed
PRD §8.5 requires a migration path tested against "at least three schema
generations", which means inventing two historical layouts. They are the two the
§3.3 layout most obviously evolved from, and each migration step is the move that
made the layout more reviewable:

- **generation 1** — the whole Book in one `book.yaml`. Every plan review is a
  diff of one enormous file.
- **generation 2** — `items/<id>.yaml`, one file per item. `params` still inline.
- **generation 3** (current) — `params.yaml` split out; `.cashkit/config.toml`
  appears, so a state that predates it is read at the documented default.

Migrations run on **parsed documents**, not on models, and before validation: a
generation-1 document need not satisfy today's `Book`. They are forward-only, and
a state from a *newer* generation is refused with `CK-E026` rather than read
optimistically — reading a file you do not understand and getting plausible
numbers is worse than refusing.

The fixture repository writes generations 1 and 2 with `yaml.safe_dump` rather
than the canonical emitter. That is realistic — a past generation had a past
emitter — and it proves that reading an old revision does not depend on the bytes
having been written by today's one.

### D-P9-08 · `diff_revisions()` is semantic; the store's path diff is separate
The gate says a reformat-only change produces an empty `diff_revisions()`. Two
ways to get there: compare bytes and hope everything was always written
canonically, or parse both sides and compare models. The first is true today
*by construction* — every state CashKit writes goes through the canonical emitter
— and stops being true the moment a human edits a file, which §3.3's whole
"config in git-tracked YAML" premise invites them to do.

So `RevisionDiff` (SDK, in `model/reports.py`) is semantic, and `StateDiff`
(store, path-based) is what it is built on. The reformat case reports
`empty == True` **and** a non-empty `reformatted` tuple, so the answer is "the
plan did not change, these files did" rather than silence — and the gate's
assertion is a real comparison rather than one that never ran. It also carries
the outcome diff, because PRD §10 wants config and outcome changes in the same
place, and an item edit whose `min_cash` did not move is a different fact from
one whose did.

### D-P9-09 · `commit()` returns a report carrying `Revision | None`
§6.6 types `commit()` as `-> Revision | None`; §6.5 requires every fallible
operation to return diagnostics rather than raise. A contended lock (`CK-E013`)
is exactly such a failure and has nowhere to go in a bare `Revision | None`.
`CommitReport` extends `ChangeReport` with `revision`, so both hold: `None`
still means "the tree was unchanged", and the diagnostics channel stays open.
Reading §6.6's annotation as a sketch and §6.5 as the rule is the reading that
satisfies §2 — this is not recorded as a PRD conflict because the two sections
do not actually disagree about behaviour, only about how much of it a return
annotation shows.

### D-P9-10 · Reproduction is asserted, not assumed, and the two failure modes differ
ADR-0006 says exact reproduction is guaranteed at matching engine version and
that a mismatch "surfaces as a reported delta, never a silent failure".
`reproduce(ref, scenario)` makes both checkable and gives them different
severities:

- **engine versions match, numbers differ** → `CK-E028`, an error. Something
  outside `(revision, scenario, engine_version, watermark)` reached the
  computation, and both numbers are now suspect. This is the failure the whole
  design exists to make impossible, so it is loud.
- **engine versions differ** → `CK-W011`, a warning, `reproduced=False`, and the
  deltas listed field by field. The engine moved; that is a fact about the build,
  not about the model, and calling it an error would train users to ignore it.

`reproduced` is never `True` on an engine-version mismatch even when every number
agrees, because "these numbers happen to match" is a weaker claim than the
guarantee, and the report says which one it is making.

### D-P9-11 · `blame()` counts creation as a change; an unknown field blames to nothing
"Which revisions changed this field" has to say what happens at the revision that
introduced the field. It counts: the value moved from absent to set, and a reader
asking when `rent.segments` was last touched wants the revision that first set
them if nothing has since. An unknown field name returns an empty list rather
than raising or matching everything — a typo must never read as a fact about the
model, and `OVERLAY_FIELDS` is the authority on what a field is.

### D-P9-12 · A revision-bound kit refuses every write (`CK-E030`)
`at()` returns a kit, so it has the same methods as a live one and something has
to happen when `commit()` is called on the past. Raising would be an exception on
something an agent can plausibly do; silently writing to the live history would
be worse. `bound_to` marks the kit and every write returns `CK-E030` naming the
revision. Reads — `run`, `summary`, `diff_revisions`, `reproduce` — all work,
which is the entire point of the object.

### D-P9-13 · The lock covers the whole commit, and all three stores
ADR-0010 puts the lock at `.cashkit/lock` without saying what it spans.
`commit()` recomputes snapshots (reads the ledger), stamps the watermark (reads
the ledger), serializes config and writes a revision — one consistency domain. A
per-store lock, or a lock held only across the revision write, would let a second
writer import events between the watermark read and the commit, producing a
revision whose watermark describes a ledger that never existed. The lock is taken
before the first snapshot recompute and released after the revision is written,
asserted by a test that checks the lockfile exists at the moment
`write_revision` is called.

Reclaiming a stale lock removes the file and retries the same atomic `O_EXCL`
create rather than overwriting it, so two reclaimers racing still produce exactly
one winner. A lockfile that exists but cannot be parsed is treated as pid 0 —
reclaimable, because it is corrupt rather than live — and a pid we cannot judge
(`PermissionError`) counts as **alive**, so an ambiguous lock is refused rather
than stolen.

### D-P9-14 · The canonical emitter's tuple rule is keyed off the field, not the value
Committing a snapshot means serializing a `RunSummary`, which holds
`diagnostics: tuple[Diagnostic, ...]`. The emitter's only tuple branch existed
for `Amount.schedule`'s `(date, Money)` pairs and unpacked *every* tuple into
`{date, amount}` — so the first model with a tuple of anything else would have
serialized as nonsense or raised.

Fixed by keying the pair form off `(Amount, "schedule")` rather than off the
value's shape. A value-sniffing rule ("a 2-tuple of date and Decimal is a
schedule point") would have worked today and silently reinterpreted the next
model that happened to hold one; a canonical emitter must not guess. No existing
output changes — `Amount.schedule` keeps its form and no other serialized model
had a tuple field — which the S1 round-trip and golden-file tests confirm.

### D-P9-15 · `pygit2` joins the dev dependency group
The same argument as `duckdb` in D-P8-15: all four Phase 9 gates run through the
git store, and a gate that skips because an extra is uninstalled is not a gate.
`pygit2` stays an *optional runtime extra* per PRD §8.2 (`cashkit[git]`) and
becomes a required development one. The seam means a book with no revision store
is still usable — `CashKit` takes `revisions=` — but v1 ships exactly one
implementation, so the tests must run it.

## Phase 10 — Introspection and CLI (Session S5)

### D-P10-01 · `validate()` runs the engine rather than re-deriving its diagnostics
An agent is told to run `validate()` after any structural change and before any
commit (PRD §9.3 rule 3), so it has to say everything a run would say. Two ways:
re-implement the compile-time and expansion-time checks, or run the engine and
harvest its diagnostics.

Re-implementation is the drift the dual-engine gate exists to prevent, in the
worst possible place: a validator that said a formula was fine while the run
refused it would be reassurance rather than information. So `validate()` runs
the engine — 17 ms on the 50-item benchmark book — and adds only what a run has
no reason to check: `CK-E011` (an amount whose sign contradicts `direction`) and
`CK-E012` (a generative stock). Both are authoring rules the engine is
deliberately indifferent to, because storage is signed and `direction` is
display-only, which is exactly why an agent authoring rent as positive/"out"
silently creates an inflow.

### D-P10-02 · The catalogue is partitioned three ways, and the partition is a test
A diagnostic nothing emits is a promise nothing keeps. Every §10.1 code is now
classified as *validate-time* (a property of a book), *operation-time* (an
outcome of a call — an import conflict, a held lock, an unresolvable ref) or
*construction-time* (rejected structurally by the model layer, so a constructed
`Book` cannot carry it — `CK-E007`, `CK-E009`, per D-P1-07). The three sets are
asserted to be disjoint and to cover the catalogue exactly, so a code cannot
quietly become unreachable and a new code cannot be added without someone saying
where it comes from. `OPERATION_TIME_CODES` names the call, not just the fact.

### D-P10-03 · One modelling mistake gets one code
A `kind="stock"` item carrying segments trips both `CK-E012` ("generative item
with kind='stock'") and the compiler's `CK-E003` ("a formula-valued kind must
have no segments"). Both are true; reporting both reads as two mistakes and
sends a reader looking for a second problem. `validate()` suppresses the
`CK-E003`-on-`segments` for an item already reported as `CK-E012`. The dedup
lives in `validate()` and not in the engine, so the engine's contract is
untouched and the dual-engine comparison still sees what it always saw.

### D-P10-04 · A trace's value is the engine's; the arithmetic is evaluated, not paraphrased
`Trace.value` is read straight out of the run's int64 column. For a derived cell
the sub-expressions are evaluated with the engine's own `ColumnEvaluator` over a
one-period window (`scalar=True`) — the same code path the fold uses — so a
traced sub-expression cannot disagree with the run about what it computed. It
costs 2 ms per cell against 0.08 ms for reading a cached column, which is
nothing against a UI click budget and buys the property that makes the output
worth reading.

A generative cell has no expression, so its steps *are* a second rendering of
the canonical rounding order (ADR-0003). That is the one place a second
implementation exists, so `Trace.reconciles` compares the steps' total back to
the engine's cell and the gate asserts it holds for 1,100 sampled cells of the
50-item fixture, both measures. Drift is made visible rather than left to be
noticed.

### D-P10-05 · Every field of a `Trace` is populated; there is no `None` to interpret
ADR-0013 makes `trace()` the primary UI interaction primitive and says a gap is
a Phase 10 defect, not a UI workaround. Optional fields would push that gap onto
every caller, so every field is non-optional with a meaningful empty value: a
generative cell reports a *rendering of its generator* rather than a null
`formula` ("segments[0].amount x (1 + 0.03)^2"), a cell with no bindings reports
an empty tuple, a trace that hit its depth limit reports `truncated=True` rather
than looking like a leaf, and `ArithmeticStep.rounding` says `"none (exact)"`
rather than being blank — "no rounding happened" and "nobody said" must not look
the same.

`render_expr()` exists for the same reason and is proved to **re-parse to the
same tree**: a trace that quoted a paraphrase would be lying in the one place a
reader is entitled to trust.

### D-P10-06 · `why_zero()` answers "not zero", and lists the causes that are also true
PRD §6.5 names five causes. Two additions, both because the alternative is a
worse answer:

- A cell that is **not zero** answers `"not_zero"` rather than being forced into
  one of the five. Inventing a cause for a question that does not apply is the
  guess this system is built to refuse.
- Causes that are *also* true go in `also`. A January cell of a contract that
  starts in March is both pre-cutover and outside every segment; reporting only
  the first would make the fix look smaller than it is, and the user would fix
  cutover and still see zero.

Cause order is fixed: cutover suppression first (it overrides everything
downstream of it), then segment coverage, then probability, then the settlement
leg, then upstream propagation.

### D-P10-07 · `describe_book()` enumerates rather than describes
The gate is "a fresh agent, given only that output, writes a working `pivot()`
call with no invalid field names", which is only checkable if the description
*lists* the legal values instead of explaining them. `PivotVocabulary` therefore
carries exactly the `index` / `columns` / `values` arguments
`FrameStore.pivot()` accepts on **this** book — `tag:<key>` entries exist only
for tag keys the book actually uses — and the gate is tested in both directions:
an agent simulator that sees only the serialized JSON builds every call the
vocabulary licenses and they all run, and every field name outside it is
rejected by the store. A description that omitted a legal value fails the first;
one that invented an illegal value fails the second.

Same reasoning for tag values, selector examples (each asserted to match at
least one item), frame columns, summary fields, measures, grains and statuses.
16 KiB of JSON for a 50-item book — small enough to hand a model whole.

### D-P10-08 · The CLI emits money as a decimal string, never a JSON number
`json.dumps` has no float-free default for `Decimal`, and `float(value)` at the
one boundary where a number leaves the system for a human to read would undo the
entire no-float discipline in the least visible place. Every money value in
every `--json` payload is its exact decimal string, asserted by a walk over the
output of every command looking for a Python `float`. `--json` is on every
command rather than only on `doctor` (PRD §8.4 requires it there): the human
rendering is a view of the same structure, never a second story that could
disagree with it.

### D-P10-09 · `cashkit serve --quack` refuses by default, and the refusal is structured
PRD §3.4 says Quack is `core_nightly` until DuckDB v2.0 and that no workflow may
depend on it. The flag (`CASHKIT_ENABLE_QUACK=1` or `--enable-experimental`) is
off by default and the refusal names the stable alternative — Parquet export —
in a machine-parseable payload, because a gate on an experimental protocol is
only useful if the refusal is legible to the caller. With the flag on, the
refusal comes from DuckDB rather than from the flag, and it is still a report
rather than a traceback.

The Quack call itself lives in `stores/frames.py`, not in the CLI: exposing a
frame store over a wire is a frame-store operation, and the CLI importing
`duckdb` would have broken the "only the frame store imports duckdb" guarantee
that makes `FrameStore` a real seam (D-P8-01). The import is local to the
command so `cashkit doctor` still runs with no extras installed — which it must,
since reporting the extras is half its job.

### D-P10-10 · The positional-segment-patching guard is scoped to the write path
Phase 7's structural test walked every `sdk/` module for `segments[...]`,
`zip(segments)` and `enumerate(segments)`. Phase 10 adds two modules that
legitimately read a segment list: `trace()` must be able to explain
"12 000 x 1.03² x 0.9" (ADR-0013 requires exactly this), and `validate()` must
check every authored amount's sign.

The guard's target is a *merge routine*, so it is now scoped to the modules that
can write an overlay — and paired with a new test proving that scope is the
whole of it: only `scenarios.py`, `macros.py` and `kit.py` may construct an
`ItemOverlay`, so a merge cannot be written anywhere the guard does not look.
Weakening the sweep without that second test would have been a real loss of
coverage; with it, the guard is the same strength over a smaller, provably
complete surface.

### D-P10-11 · `cashkit init` resolves the holiday set at creation, in the CLI
ADR-0010 makes `CalendarSpec.holidays` a resolved, committed list and the
`holidays` package a seed the runtime never consults. Something has to do the
seeding, and it cannot be `engine/` or `model/` (both are lint-fenced against
exactly this kind of environment read). It lives in `cli/main.py`, where book
creation happens, and resolves only the horizon's own years. An unknown country
code returns an empty list rather than failing book creation: the absence is
visible in the committed calendar, and refusing to create a book over a
holiday-table lookup would be the wrong trade.

### D-P10-12 · Phase 10 ships **no** tax-coverage diagnostics (ADR-0021)
ADR-0020 specified `CK-I010` … `CK-I015`, one info diagnostic per non-native tax
mechanic (IRES/IRAP, INPS/INAIL, TFR, acconto IVA, tax credits, instalment
plans), detected from tags, plus a rendered coverage statement. They were
implemented and then **removed** before this phase's gate commit, on a scope
ruling recorded in ADR-0021: all domain *content* — enumerated mechanics,
jurisdiction checklists, anything Italy-specific — belongs to applications built
on the SDK, not to the engine. CashKit core is a calculation engine.

What was removed: the six catalogue codes, `COVERAGE_MECHANICS` and the tag
vocabulary, `tax_coverage()`, `TaxCoverage` / `CoverageLine`,
`BookDescription.tax_coverage_tags`, `CashKit.tax_coverage()` and
`cashkit validate --coverage`.

What stayed, and why it is not the same thing: `CK-W004` (withholding in use
with no `cat:tax` item covering the counter-leg) and `CK-I001` (a `TaxRegime`
with no non-VAT `cat:tax` items) are §10.1 codes that predate this session, and
both are statements about the **model** — a settlement term whose other leg the
engine does not generate; a regime that schedules only what it accumulates —
rather than about any jurisdiction's rules. Their wording did name Italian
mechanics (IRES/IRAP/INPS/TFR, "F24"), which was the same content in a different
place, so both `suggested_fix` strings were rewritten to be jurisdiction-free.
A test now asserts that no catalogue entry names a jurisdiction mechanic, so the
boundary is enforced rather than remembered.

The argument ADR-0020 made — that a behavioural instruction to an agent is the
weakest available mitigation — is not wrong, and it is not answered here. It is
relocated: the check belongs in the layer that knows which entity, which country
and which year, and that layer is not the engine.

## Session S5.5 — The construction surface (PRD §6.1 and the two §6.2 gaps)

### D-S55-01 · A write is refused when it is wrong in isolation, recorded when it is wrong in context
PRD §6.1 says `add_item` is "validated; returns diagnostics" and `add_derived`
is "parsed + DAG-checked NOW", without saying whether a failing validation
*writes*. Both readings are defensible and both fail somewhere:

- **Refuse everything that produces an error.** Clean, and it makes some legal
  books unconstructible. Two items in a genuine `prev()` feedback set reference
  each other; whichever is added first names an item that does not exist yet
  (`CK-E001`) and is refused, so neither can ever be first. The same argument
  applies to a `TaxRegime` whose `accumulates` selector matches only items the
  script has not written yet.
- **Record everything and report.** Also clean, and it means a formula that is
  not a formula sits in `book.yaml` waiting for someone to ignore a diagnostic.

The line drawn is where the problem *lives*. A write is **refused, recording
nothing**, when the thing being written is wrong on its own terms and no later
write could fix it: a formula that does not parse (`CK-E003`, `CK-E007`), a
settlement term list that cannot mean anything (`CK-E004`/`CK-E005`), an amount
whose sign contradicts `direction` (`CK-E011`), a generative stock (`CK-E012`), a
regime asking for an annual refund without naming the month (`CK-E019`). A write
is **recorded, with its diagnostics**, when the problem is a statement about the
book as a whole: an unknown reference, a cycle with no `prev()` edge, an unknown
param, a selector matching nothing, aggregation across currencies. Those resolve
as the book grows, and `validate()` still refuses to let them past a commit.

Either way the news arrives at call time, which is the part §6.1 is explicit
about. `ItemRef.ok` is the one-bit answer an agent loops on.

### D-S55-02 · The context half is a compile **delta**, not a compile
`add_item` and `add_derived` compile the book before and after the write and
report only the diagnostics the write introduced. Reporting the whole
post-compile list would blame an add for breakage that was already there —
building a book bottom-up means every intermediate state has forward references
— and reporting only diagnostics whose `item_id` is the new item would hide the
collateral case, where the new item breaks something else (an `agg()` that now
spans two currencies is reported on the *aggregating* item, which is where the
mistake now lives). Compilation parses, resolves and condenses but evaluates
nothing, so two compiles are the cheap half of one run.

### D-S55-03 · The authored book has exactly one writer: `ScenarioSet.set_book`
ADR-0007 splits the API along the storage split — `add_item(book, …)` writes the
top-level item files, `set_item(scenario, …)` writes overlays — and the risk in
implementing the first half last is that it becomes a second, divergent write
path. It does not: every §6.1 verb funnels through one new method on
`ScenarioSet`, the object that already owns both the authored book and the
scenarios. `set_book(**update)` returns the field names that actually moved (so
an unchanged write is empty by construction, not by a caller remembering to
check), and re-applies the two invariants `model_copy` skips — no
engine-synthesized item may enter the authored book (D-P5-09/D-P5-10), and every
key in `items` must equal its item's id.

### D-S55-04 · Every construction verb saves; the working tree stays the working state
D-P9-05 makes the tree on disk the working state, and `discard()` already
writes it back after restoring. Construction follows: each verb calls
`kit.save()`, so a book half-built by a crashed agent is still a book, and the
CLI or a human editor sees what the SDK just authored. Exploratory *sweeping*
stays in memory as §6.7 requires — that is the scenario surface, which does not
save — so the two costs land where they belong: authoring is durable, sweeping
is free.

### D-S55-05 · `add_item` re-authors an existing id rather than refusing it
PRD §6.1 names it `add_item`, which reads as create-only. Re-authoring is the
better behaviour and matches `set_item`'s by-value idiom: a construction script
re-run against an existing book converges instead of erroring, and `ItemRef`
reports the difference — `created` for a new item, `changed` naming the fields
whose authored value moved, `CK-I002` when the item was already exactly this.
"Add" that silently duplicated or silently overwrote would be worse; "add" that
tells you what it changed is the same operation with a receipt. Same rule for
`add_tax_regime`, keyed on regime id.

### D-S55-06 · `retag` returns an `int` that can carry diagnostics
PRD §6.1 types `retag(book, selector, tags) -> int` and §6.5 requires every
fallible operation to return `Diagnostic` objects. A selector *is* fallible, so a
bare `int` would have to report a malformed selector as `0` — the same answer a
selector that genuinely matches nothing gives. Two different facts, one number,
no way to tell them apart: the silent-failure class this project ranks worst.

`AffectedCount` subclasses `int`, so `retag(...) == 3`, `isinstance(…, int)` and
`affected + 1` all hold and the PRD's annotation is literally true, while
`.diagnostics` carries `CK-E003` when the selector did not parse. A selector
matching nothing is `0` with no diagnostics; a typo is `0` with one. The gate
asserts both.

### D-S55-07 · `add_tax_regime` returns a `ChangeReport`, not `None`
PRD §6.1 types it `-> None`. §6.5 says every fallible operation returns
diagnostics, and a regime is fallible in a way that produces a *zero* rather
than an error — an `accumulates` selector matching nothing schedules nothing at
all. Returning `None` would make that silent. This is the same reading D-P9-09
made of `commit() -> Revision | None`: the annotation shows less of the operation
than §6.5 requires, and §2 settles it in favour of the diagnostic. Recorded
under `## PRD conflicts` as C-S55-01.

### D-S55-08 · `create_book` takes a root, and mints two codes for its own refusals
The PRD signature (`create_book(id, grain, horizon, opening_balance, calendar)`)
describes the model, not its storage, so `root` is added as the first argument
and `ledger`/`revisions` stay constructor arguments — storage swappable, exactly
as `CashKit` already has it.

Two failure modes had no code. `CK-E031` is a book already at that path: §9.6
rule 2 says open it rather than create a second one, and creating a book over a
book would orphan a history no revision can recover. `CK-E032` is an argument
that cannot make a `Book` — a malformed id, a horizon that is not `start < end`,
money past 4 decimal places. Both are things an agent plausibly does, so neither
may be a `ValidationError` escaping into a caller's face (PRD §6.5 reserves
exceptions for programmer error). Both are operation-time codes and neither is
reachable from `validate()`.

### D-S55-09 · Holiday resolution moves from the CLI to the SDK, amending D-P10-11
D-P10-11 put `resolve_holidays` in `cli/main.py` because book creation happened
there and `engine/`/`model/` are lint-fenced against environment reads. Book
creation now happens in `sdk/construction.py`, so that is where it lives; the
CLI re-exports the name. The reasoning is unchanged and so is the behaviour —
resolved once for the horizon's own years, committed, never consulted at runtime
(ADR-0010), and an unknown country code returns an empty list rather than
refusing a book. `sdk/` is inside the wall-clock lint and this function reads no
clock: it is a pure function of `(country, horizon)`.

The move is what makes the gate assertion possible — `cashkit init` and
`create_book` produce books that are **byte-identical** under the canonical
emitter, holiday set included, because there is one function producing them.

### D-S55-10 · Reconciliation is two engine runs, never a re-derivation
`reconcile(book, until)` has to compare "what the bank did" with "what the model
said", and the obvious implementation — sum `Event.amount` over the window and
compare to forecast cash — compares a net accrual to a gross settled amount and
calls the difference drift. So both sides go through the engine over the same
book: the **forecast** side is a run with no ledger at all, the **actual** side a
run over the window's actuals with every generative segment stripped. Both then
carry the same canonical rounding order, the same settlement split and the same
VAT gross-up, and the difference between them is drift by construction rather
than by argument. Two extra runs cost tens of milliseconds; a wrong
reconciliation costs a company.

Three consequences worth stating:

- The window `[since, until]` is a whole number of **base periods** — a period is
  in or out as a unit. Apportioning a month across a boundary would invent a
  number no measure supports.
- `since` defaults to `book.cutover`, because that is the boundary from which
  generation is live and events apply alongside it (ADR-0004). The window
  reconciled is exactly the window not yet closed.
- `suggested_cutover` is the day *after* `until`: generation is suppressed for
  occurrences strictly before `cutover`, so closing through `until` means
  resuming the next day. It feeds `set_cutover()` directly, which is the whole
  point of PRD §6.2's "Feeds set_cutover".

An actual referencing no item lands on the engine's `_event:<digest>` carrier and
is reported under that id. A reconciliation that folded it into an unnamed total
would report the drift without saying where it came from.

### D-S55-11 · The kit gained the four ledger writes so a revision-bound kit can refuse them
`add_event`, `import_events`, `void_event` and `correct_event` remain
`LedgerStore` operations — the store owns append-only-ness and
`UNIQUE(source, ext_id)`, and moving them would move the idempotency key
somewhere it can be bypassed. `CashKit` now wraps them anyway, for one reason:
`at(ref)` returns a kit sharing the **live** ledger object, so a write reached
through `kit.ledger` on a bound kit would append to the present while reading the
past. Every other write on a bound kit refuses with `CK-E030` (D-P9-12); these
now do too. Strictly a hole closed, not a surface widened.

### D-S55-12 · `note` is accepted and not stored, everywhere on this surface
`set_param(…, note)` and `set_cutover(…, note)` take the note PRD §6.1 gives
them and do not persist it, exactly as `ScenarioSet.set_item` / `set_param`
already do. The revision message is where a change's reason lives and travels
with the history; a note that only ever reached memory would be a promise the
history does not keep. Stated here because "accepted and ignored" is the kind of
thing a reader should find written down rather than discover.


## Session S5.6 — The §6.4 execution surface, the cutover guard, the coverage gate

### D-S56-01 · `frame` / `pivot` / `compare` / `export` are kit methods; the arithmetic stays in the store
The four verbs existed and were unreachable from PRD §6: they lived on
`DuckdbFrameStore`, below the SDK line, reached by importing a module the §6
surface never names. An agent following §6 could evaluate a book and could not
tabulate it.

The wiring adds **no arithmetic**. Aggregation rules, the selector join, the
`DECIMAL(18,4)` path and the Parquet `COPY` all stay in `stores/frames.py`, and
`tests/test_execution.py` asserts each kit result equal — as a whole `Table`,
not summed and compared — to the same query run directly against a separately
materialized store. What the SDK layer adds is the three things a store cannot
do for itself: materialize the run it is handed, keep `duckdb` optional, and
validate the strings an agent composes.

### D-S56-02 · The kit's frame store is in memory, not `frames.duckdb`
PRD §3.3 lists `frames.duckdb` in the layout and the kit does not open it.
Three reasons, in order of weight:

- **`at(ref)` shares this kit's `root`.** A live kit and every revision-bound
  kit derived from it would all want the same file, in the same process, at the
  same time — and each has its own `RoundingPolicy` from its own committed
  settings, which the store takes at construction. One file cannot serve two
  policies correctly.
- **`cashkit serve` opens that file read-only over Quack (§8.6).** A kit holding
  it would be a live writer against a path another command legitimately reads.
- **Persistence buys no correctness here.** Every call re-materializes the run
  before reading it (D-S56-03), so the store is a scratch space, and PRD §5.2
  makes recomputation the cheap option — materializing the 50-item 5-year
  benchmark is inside a 200 ms budget.

Nothing was removed: `DuckdbFrameStore(root / "frames.duckdb")` is unchanged and
available to anyone who wants the on-disk store, and the `FrameStore` protocol
is still what the SDK codes against, so a kit backed by a different store is a
constructor argument.

### D-S56-03 · The run key is §6.6's four-tuple plus the effective cutover, and every call re-materializes
`(revision, scenario, engine_version, ledger_watermark)` is the PRD's own cache
key. The effective `cutover` joins it because a `cutover_override` run is the
same four-tuple as the run without it, and §6.4 calls the override "a deliberate
query, not a property of the model" — letting it overwrite the model's own frame
would make the deliberate query destructive.

A live kit has no revision, so its key says `working`. That is honest rather
than unique: two different working trees share a key. The resolution is not a
content hash — hashing a book to serve a cache would be spending the cost the
cache was meant to save — but **re-materializing on every call**. The key exists
to keep distinct runs apart *inside* one store, not to skip work. `compare()`
therefore disambiguates two runs that produce the same key with a `#n` suffix
instead of collapsing them: the caller asked for two columns.

### D-S56-04 · `Table` gains a `diagnostics` channel; `export` returns an `ExportReport`
§6.4 types `frame`, `pivot` and `compare` as `-> Table` and §6.5 requires every
fallible operation to return diagnostics rather than raise. A carrier with no
room for a diagnostic cannot satisfy both, so the room is on `Table` — one
optional field defaulting to empty, which every existing producer leaves empty.
The signature §6.4 states is preserved exactly, and the distinction the whole
catalogue exists to keep survives: an empty table reporting nothing means "the
query matched nothing", an empty table reporting `CK-E033` means "the query did
not run".

`export` is typed `-> Path`, which has no such room, so it follows the precedent
D-P9-09 set for `commit() -> Revision | None` and C-S55-01 for `add_tax_regime`:
`ExportReport(ChangeReport)` carrying `path: Path | None`. `None` exactly when
nothing was written.

### D-S56-05 · `CK-E033`: the duckdb extra's absence is a diagnostic, not an `ImportError`
`duckdb` is an optional extra and `stores/frames.py` is the only module that
imports it (`tests/test_frames.py` lints this). The §6.4 verbs import that module
**lazily**, and a failed import becomes `CK-E033` naming the extra to install.
An agent can loop on a structured diagnostic; it cannot loop on a traceback
raised three frames below the surface it is coding against, and "install
`cashkit[duckdb]`" is exactly the kind of suggested fix §10.1 exists for.

The absence is tested by evicting `cashkit.stores.frames` from `sys.modules` and
setting `sys.modules["duckdb"] = None`, which makes `import duckdb` raise
`ImportError` without uninstalling anything; `monkeypatch` restores both. A
subprocess test additionally proves the SDK does not reach the extra
*transitively* — importing `cashkit.sdk` loads neither `duckdb` nor the frame
store.

`summary()`, `trace()` and `why_zero()` are unaffected and deliberately so: "when
do we run out of cash" is the question the system exists to answer and it works
on a core install, straight off the engine's int64 columns.

### D-S56-06 · Selectors are validated by the SDK; a closed vocabulary still raises
`stores/frames.py` says in its own docstring that its `ValueError`s are
"programmer error at this layer; selectors an agent authors are validated by the
SDK first". This session is that layer. `where=` goes through the one §5.4
grammar via `resolve_selector` and comes back as `CK-E003`, so a typo and an
honest miss stay distinguishable — the same rule `retag` follows (D-S55-01's
gate 4).

Everything else keeps raising: an unknown measure, pivot index, column spec or
export format is a **closed set `describe_book()` enumerates**, which is PRD
§6.5's own definition of programmer error ("bad types, missing store"). The line
is composition: a selector is assembled from tags that vary per book, a measure
name is not. Minting a diagnostic for `measures=["revenue"]` would say that
CashKit expects callers to guess its vocabulary at runtime.

### D-S56-07 · A relative export path lands under `exports/`; an absolute one is honoured
PRD §3.3 puts `exports/` at the book root, git-ignored, because an export is a
copy of what a revision already reproduces. A relative `path` resolves there. An
absolute path is written where it says: "produce this file over there for
somebody else" is the reason the verb exists, and silently relocating an
absolute path would be worse than either choice taken outright.

`read_export()` is on the kit alongside it — six lines onto the store's existing
reader — because an SDK that can write a file it cannot read back is an SDK
whose round-trip nobody can check without dropping below the §6 surface, which
is the SDK-only non-negotiable in miniature.

### D-S56-08 · A revision-bound kit frames and exports; it still refuses to commit
`at(ref)` is read-only (D-P9-12, D-S55-11) and a frame is a read. The run key
carries the revision, so the past and the present cannot collide inside one
store, and `kit.at(ref).frame(kit.at(ref).run())` tabulates that revision's
numbers.

`export()` is included even though it writes a *file*: the file is a copy of
what the revision already reproduces, it lands in git-ignored `exports/`, and
refusing it would make a past revision the one thing an agent cannot hand to
anybody. `commit()`, `discard()` and the four ledger writes still refuse with
`CK-E030`.

### D-S56-09 · `CK-W006` is a warning, and one code covers both directions
The question the code had to answer was whether an out-of-horizon cutover is
ever legitimate. Both directions are:

- **Before `horizon.start`** is the natural state of a book that has never been
  reconciled, and it changes nothing — generation is suppressed strictly
  *before* the cutover, so there is nothing in the horizon to suppress.
- **Past `horizon.end`** is the ordering an agent lands in when it closes a
  window and then extends the horizon to cover the next one. Legitimate on the
  next call; total suppression until then.

Refusing either would make a legal sequence of writes unconstructible, which is
exactly the test D-S55-01 sets. So: **recorded, and warned about.** The warning
carries which direction and what it does to the model, because the state is
otherwise entirely silent — the book compiles, the run succeeds, and every
number is zero with nothing anywhere saying why. That is the quietest failure
mode on this surface, and CLAUDE.md names silent numerical error as the worst
one there is.

One code, not two: it is one condition (a cutover the horizon does not contain)
whose consequence differs by direction, and the consequence is in the message
via an `effect` placeholder. Two codes would make an agent match on two things
to ask one question.

The horizon is half-open, so `horizon.end` **itself is inside it**: a cutover at
the end suppresses everything too, but it is the boundary the model's own
arithmetic reaches, and naming it a mistake would be naming the horizon a
mistake. The predicate is `horizon.start <= day <= horizon.end`.

### D-S56-10 · The check lives in `validation.py` and `set_cutover` calls it
`cutover_problem(day, book)` is one function with two callers: `set_cutover()`,
so the agent that caused the state is the agent told about it, and `validate()`,
so a book opened from disk already in that state is not silent either. A warning
that only surfaced on the next `validate()` is a warning the caller never sees;
two implementations of the same predicate is the drift the dual-engine gate
exists to prevent, in miniature.

`create_book(cutover=…)` is deliberately **not** a third caller. The session
scope named two doors, `validate()` covers the created book from its next call
onward, and a third emission point is surface added under a gate rather than
through one.

`CK-W006` is a validate-time code and joins `VALIDATE_TIME_CODES`; `CK-E033` is
an operation-time code and names its origin in `OPERATION_TIME_CODES`. The
three-way partition test still covers the catalogue exactly.

### D-S56-11 · The coverage gate is two commands, and the threshold lives in `pyproject.toml`
```
uv run pytest                            # everything, uninstrumented
uv run pytest -m "not benchmark" --cov   # the gate, fails below 90%
```

`[tool.coverage.run] source` is exactly `cashkit/engine` and `cashkit/model` —
the two packages where a silent numerical error can hide — and
`[tool.coverage.report] fail_under = 90` is what makes the run exit non-zero.
The threshold lives in the file rather than on the command line so no invocation
can quietly lower it. Measured: **96.56%**; proven to fail by setting
`fail_under = 99.9` and observing exit code 1, then restoring.

`addopts` deliberately does **not** carry `--cov`. S5 verified the §5.2
benchmarks fail under coverage tracing and this session re-verified it: delta
recompute goes from ~5 ms to 12.54 ms against a 5 ms budget. A default run that
instruments `engine/` is a default run whose performance assertions measure
coverage.py. The nine timing tests already carry `@pytest.mark.benchmark`, which
is what `-m "not benchmark"` deselects.

`tests/test_coverage_gate.py` asserts the configuration rather than re-measuring
the percentage: running the suite inside the suite would double the wall clock
to re-derive a number the gate command already prints, while what can silently
break is the config. It checks that `pytest-cov` is a declared dev dependency
(not a remembered `--with` flag), that `source` is the two packages and both
exist, that `fail_under >= 90` with `show_missing`, that `addopts` carries no
`--cov`, and — structurally, so it holds for tests nobody has written yet — that
**every function reading `perf_counter` carries the benchmark marker**. An
unmarked timing test would run instrumented and fail for a reason unrelated to
the engine, which is the kind of failure that gets a budget loosened rather than
a cause found.
