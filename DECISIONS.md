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
