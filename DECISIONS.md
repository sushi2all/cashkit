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

## PRD conflicts

### C-P1-01 · `CalendarSpec.weekend` "ISO weekday indices" vs default `{5, 6}` = Sat/Sun
ISO weekday numbering is 1–7 with Sat=6/Sun=7, under which the stated default
`{5, 6}` would mean Fri/Sat. The default and its "Sat/Sun" label are
consistent only with Python `date.weekday()` 0-based numbering (Mon=0,
Sat=5, Sun=6). Implemented as Python `weekday()` indices 0–6, preserving the
PRD's stated default semantics; the "ISO" label is treated as a misnomer.
