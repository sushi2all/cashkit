# Handoff — Session S1 (Phase 1: models and canonical serialization)

**Date** 2026-07-29 · **Status** Phase 1 gate passed, committed.

## Session-start protocol note

There was no existing test suite when this session started (first session,
docs-only repo) — the "re-run the entire existing suite before writing code"
start step was a no-op, by construction rather than by omission.

## What was built

- **Package bootstrap**: `pyproject.toml` (uv; Python ≥3.11, pinned 3.13
  locally via `.python-version`, `uv.lock` committed), `README.md`,
  `DECISIONS.md`, `BENCHMARKS.md`, `.gitignore` additions. Skeleton packages
  `cashkit/{engine,reference,stores,sdk,cli}` exist but are deliberately
  empty (docstring-only `__init__.py`) — no later-phase logic was stubbed.
- **`cashkit/model/`** — every PRD §4 model, all frozen Pydantic v2,
  `extra="forbid"`:
  - `primitives.py`: Grain, Money/FiniteDecimal (finite, ≤4 dp, |x| ≤ 9e14,
    float input rejected), Duration, PeriodRange, CalendarSpec (holidays
    sorted+deduped, weekend = Python `weekday()` indices — see DECISIONS
    C-P1-01), Watermark, Amount (constant xor schedule), Escalation,
    Diagnostic, identifier grammars, `SparseOverlay` base (recordedness via
    `model_fields_set`, equality includes the recorded set).
  - `settlement.py` (DueTerm exactly-one-of share/amount/remainder;
    `Settlement.immediate/net/split` constructors), `tax.py` (VatSpec,
    TaxRegime), `item.py` (Item, Segment — `recurrence` required, one-offs
    are Events; Recurrence day iff day_of_month), `event.py` (Event; ext_id
    requires source), `book.py` (Book; items keys == item.id), `scenario.py`
    (Scenario, ItemOverlay, EventOverlay — overlay `status` cannot even
    represent `"actual"`).
  - `diagnostics.py`: the full §10.1 catalogue as data (22 codes, message +
    suggested_fix templates) and `make_diagnostic()`.
  - `canonical.py`: hand-written canonical YAML emitter + `yaml.safe_load`
    parser. Field order = declaration order; every user string double-quoted
    with a fixed escape table; Decimals as `str(Decimal)` quoted; dates
    quoted ISO; None omitted (recorded-None on overlays → explicit `null`);
    empty collections explicit `[]`/`{}`; mapping keys sorted; sets sorted;
    LF; one trailing newline. Never `yaml.dump()` on a model.

## What the gate proved

`uv run pytest` → **62 passed** (~8 s). The gate test
`tests/property/test_roundtrip.py::test_book_roundtrip` runs **250** generated
Books (gate requires 200+) proving `parse(serialize(x)) == x` and
`serialize(parse(s)) == s` byte-for-byte; plus 150 each for Scenario, Event,
ItemOverlay, EventOverlay (overlay tests also prove the recorded-field set
survives the round trip). An additional one-off stress run of **1000 Books +
500 Scenarios** passed with zero failures (not committed as a test — it is
the same property at higher example count).

Also in the suite, already active per the S1 brief:

- `tests/test_wall_clock_lint.py` — wall-clock ban in `engine/`+`model/`
  (self-validating against sample violations).
- `tests/test_no_float_money.py` — `float` identifier absent from
  `model/`+`engine/` source (single allowlisted rejection guard), no model
  field type reaches float, money fields reject float input.
- `tests/test_canonical_emitter.py` — format rules pinned one by one, plus
  the committed golden file `tests/fixtures/canonical_book.yaml`
  (byte-compared on every run; cross-version phantom-diff guard).
- `tests/test_diagnostics_catalogue.py` — catalogue == §10.1 exactly, every
  code instantiable.

## Decisions made (full text in DECISIONS.md)

D-P1-01…14 (overlay recordedness + explicit `null`; explicit empty
collections; schedule as `{date, amount}` maps; rate str/Decimal
disambiguation; identifier grammars; money bounds at the boundary; structural
vs diagnostic validation split; EventOverlay field set; frozen models;
canonicalize-in-model; quote-everything emitter; conservative lint; Scenario/
Event structural extras; empty `accumulates`) and one PRD conflict
(C-P1-01: weekend index convention).

## First thing S2 should verify

Re-run `uv run pytest` — all 62 tests must pass before writing any code.
Then, before building the reference engine, read DECISIONS D-P1-06 and
D-P1-07: the models guarantee amounts are ≤4 dp and ≤9e14, so the engine's
Decimal→int64 minor-unit conversion is exact by construction — the reference
engine must consume `Decimal` from the models and apply the ADR-0003
canonical rounding order (base → escalation → probability → settlement split
→ withholding → VAT), and C-P1-01's weekday convention (Python `weekday()`,
weekend `{5,6}` = Sat/Sun) governs `business_day_adjust` and
`t.is_business_day`.

Note for S2 scope: `Item.settlement` on *derived* items is representable in
the model (left unspecified by the PRD, per the review note "Not changed") —
Phase 2 must decide its semantics and record a DECISIONS entry.
