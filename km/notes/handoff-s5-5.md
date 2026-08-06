# Handoff — Session S5.5 (the construction surface)

**Date** 2026-08-06 · **Status** gate passed, committed, not pushed.
**Suite** 1,007 → **1,054** passing (~21 s). `ENGINE_VERSION` **unchanged** (`"1"`) —
nothing under `engine/` was touched; see the last section.

S5's handoff named this the largest remaining hole in the SDK: PRD §6.1 listed
seven construction verbs and only `validate` existed, so `cashkit init` built a
`Book` by hand because there was no `create_book()` to call. The SDK-only
non-negotiable was true of every caller except the one shipping in the box.

## What was built

### `cashkit/sdk/construction.py` — PRD §6.1

`create_book`, `add_item`, `add_derived`, `set_param`, `retag`,
`add_tax_regime`, `set_cutover`, plus `resolve_holidays` (moved here from the
CLI) and two return types: `BookRef` (a live kit, or `None` and the reason) and
`AffectedCount` (an `int` that carries diagnostics — D-S55-06).

The rule the whole module turns on is **D-S55-01**: a write is *refused,
recording nothing*, when the thing being written is wrong in isolation — a
formula that does not parse, a settlement term list that cannot mean anything, a
sign contradicting `direction`, a generative stock. It is *recorded, with its
diagnostics*, when the problem is about the book as a whole — an unknown
reference, a cycle, an unknown param, a selector matching nothing. The second
half is not laxity: refusing context errors would make legal books
unconstructible, because two items in a `prev()` feedback set each reference the
other and neither could ever be added first.

The context half is a compile **delta** (D-S55-02): compile before, compile
after, report what the write introduced. An add is never blamed for breakage
that was already there, and never hides breakage it caused somewhere else.

### `cashkit/sdk/events.py` — the two §6.2 gaps

`query_events()` shapes ledger rows into the §6.2 `Table`. `reconcile()` is two
engine runs over the same book — the forecast side with no ledger, the actual
side with the window's actuals and every generative segment stripped — so both
numbers went through the canonical rounding order and their difference is drift
rather than an artefact of how each was computed (D-S55-10). Its
`suggested_cutover` is the day after the window and feeds `set_cutover()`
directly.

### One write path onto the authored book

`ScenarioSet.set_book(**update)` is the only thing that replaces a field of the
authored book. It returns the field names that actually moved — so an unchanged
write is empty by construction — and re-applies the two invariants `model_copy`
skips: no engine-synthesized item may enter the authored book, and every
`items` key must equal its item's id. ADR-0007's split holds exactly:
`add_item` writes `items/`, `set_item(scenario, …)` writes `scenarios/`.

### Everything reachable from the object an agent holds

Every verb is also a `CashKit` method, as `validate()` and `describe_book()`
already were. `CashKit` additionally gained thin wrappers for the four ledger
writes — not to move them off the store, but so a **revision-bound kit refuses
them** (`CK-E030`). `at(ref)` shares the live ledger object, so before this a
write reached through `kit.ledger` would have appended to the present while
reading the past. That is the one direction ADR-0006 had no defence against.

### `cashkit init` is now a caller like any other

`cmd_init` calls `create_book()`. A source-level test asserts the CLI constructs
no `Book`, `Item`, `Segment` or `Scenario` at all.

## What the gate proved — `tests/test_construction.py` (47 tests)

**Gate 1 — SDK-only end to end.** From a directory that does not exist:
`create_book` → `set_param` → `add_item` ×3 (in and out, VAT, `net(30)`,
`immediate()`, a 50/50 `split`) → `add_derived` → `add_tax_regime` →
`add_event` + `import_events` → `set_cutover` → `run` → `summary` → `commit` →
`history`, ending with a `validate()` carrying no error. Not one model is
constructed except the `Item`/`Event`/`TaxRegime` specs the signatures take, and
no file is written that the SDK did not write. A companion test reopens the book
and finds every authored item, param, cutover and regime on disk.

**Gate 2 — `add_derived` checks now.** Four unparseable formulas (a syntax
error, an `__import__` attempt, an empty string, a dotted param key) are refused
and never reach `book.items`; the run and `validate()` afterwards carry *no*
diagnostic for that id, which is the real claim — the failure was not deferred,
it was prevented. An unknown `it()` target reports `CK-E001` at call time and is
recorded; `a → b` then `b → a` reports `CK-E002` on the second call; a
`prev()` self-cycle is legal and reports nothing.

**Gate 3 — drift, exactly.** Rent forecast at 3 000/month against a ledger where
January actually cost 3 100: `forecast=-6000.0000`, `actual=-6100.0000`,
`drift=-100.0000`, `drift_total=-100.0000`. A window containing only the month
that matched reconciles clean. An actual referencing no item appears as its own
`_event:` line rather than vanishing into a total. `suggested_cutover` is
2026-03-01; feeding it to `set_cutover` leaves the two closed months holding
`-61 000 000` minor units — the ledger's numbers and nothing else, because
generation before cutover is suppressed entirely.

**Gate 4 — `retag` counts.** Two of three items match and the count is `2`; a
selector matching nothing is `0` with no diagnostic; a malformed selector is `0`
**with `CK-E003`**, so a typo and an honest miss are distinguishable; the same
retag applied twice returns `0` the second time. `isinstance(affected, int)`,
`affected + 1 == 3` and `f"{affected}" == "2"` all hold.

**Gate 5 — byte-equality.** `cashkit init` with `--calendar IT
--fiscal-year-start 7 --cutover 2026-02-01` and the equivalent `create_book()`
produce books whose `to_canonical_yaml()` output is identical, resolved Italian
holiday set included.

**Gate 6 — nothing else moved.** 1,054 pass. Coverage on `engine/` + `model/` is
**97%** (lowest file 85%, `engine/result.py`), measured with an ephemeral
`uv run --with pytest-cov` so the dependency set is unchanged — see the open
items. The wall-clock, no-float, no-LLM, overlay-writer and
no-positional-segment-patching lints all pass untouched; `construction.py` and
`events.py` construct no `ItemOverlay` and read no clock.

## Decisions recorded

**D-S55-01…12** — refuse in isolation / record in context; the compile delta;
`ScenarioSet.set_book` as the single write path; every verb saves; `add_item`
re-authors rather than refuses; `AffectedCount`; `add_tax_regime` returns a
report; `create_book` takes a root and mints `CK-E031`/`CK-E032`; holiday
resolution moved to the SDK (amending D-P10-11); reconciliation is two runs;
the four ledger wrappers exist to make a bound kit refuse; `note` is accepted
and not stored.

**C-S55-01** — §6.1 types `add_tax_regime` as `-> None` while §6.5 requires
diagnostics from every fallible operation. Implemented as `-> ChangeReport`,
following D-P9-09's reading of `commit()`. C-P1-01 and C-P8-01 remain otherwise
the only conflicts.

## Changes to earlier sessions' code

- **`cashkit/sdk/scenarios.py`** — one new method, `set_book`. Nothing existing
  changed.
- **`cashkit/sdk/kit.py`** — the §6.1 delegates, the four ledger wrappers with
  `_ledger_write`'s `CK-E030` guard, and `query_events` / `reconcile`. No
  existing method changed.
- **`cashkit/model/reports.py`** — `ItemRef`, `ReconciliationLine`,
  `ReconciliationReport`.
- **`cashkit/model/diagnostics.py`** — `CK-E031`, `CK-E032`. Both appear in
  `OPERATION_TIME_CODES`, so the three-way partition test still covers the
  catalogue exactly.
- **`cashkit/cli/main.py`** — `cmd_init` routed through `create_book`;
  `resolve_holidays` moved out and re-exported from here so
  `tests/test_cli.py`'s import keeps working.
- **`tests/test_diagnostics_catalogue.py`** — the two new codes added to
  `ADDED_CODES` with their reasons.

Nothing under `engine/`, `reference/` or `stores/` was modified.

## `ENGINE_VERSION`: not bumped, deliberately

`ENGINE_VERSION` is still `"1"`. No file under `cashkit/engine/` was touched —
this session only *calls* `compile_book`, `classify_settlement`, `parse_formula`
and `Engine.run`. No evaluation semantics moved, so bumping would have been the
opposite error: it would have made every existing snapshot report
`engine_version_matches=False` and downgrade a real `CK-E028` into a shrugged
`CK-W011`. The `at()`/`reproduce()` guarantee is intact; `git diff --stat` over
`cashkit/engine/` for this session is empty, which is the check anyone should
re-run.

## What §6 still lacks

- **`Settlement` ergonomics are complete, `ItemSpec` is not a thing.** PRD §6.1
  types `add_item(book, spec: ItemSpec)`; there is no `ItemSpec` type in the
  codebase and `Item` is what the signature takes. That is almost certainly what
  the PRD meant (§4.2 defines `Item` and nothing defines `ItemSpec`), but a
  future session wanting a friendlier authoring shape — durations instead of
  explicit segments, say — has a name reserved for it.
- **`export()`** (§6.4) is implemented in the frame store but has no SDK-surface
  entry point on `CashKit`; the same is true of `frame()`, `pivot()` and
  `compare()`. They are reachable through `cashkit.stores.frames`, which is a
  store rather than the §6 surface. This is the next-largest §6 gap and it is a
  wiring job, not a design one.
- **`set_cutover` accepts any date.** A cutover past `horizon.end` silently
  suppresses all generation, and one before `horizon.start` is a no-op. Neither
  is caught here or by `validate()`. Both are cheap to add and want a catalogue
  code; nobody has minted one.
- **`reconcile` compares one measure at a time** (`cash` by default,
  `accrual` on request). A report carrying both would be more useful to a close
  workflow and is a strictly additive change to `ReconciliationReport`.
- **Per-cell `status`** (D-P8-06) and **per-event tags on frame rows**
  (D-P5-09) — still waiting on the same engine change, still correctly described
  in S4's handoff. Untouched here.
- **Test coverage is measured but not enforced.** `engine/` + `model/` is 97%,
  comfortably past the 90% target, but the number came from an ephemeral
  `uv run --with pytest-cov` invocation rather than from the dependency set.
  Making it a standing gate still means adding `pytest-cov` to the dev group —
  the call open since S2. It is now a smaller call than it was, because the
  answer is known and it is a good one.
- **`km/notes/architecture-deck.html`** remains untracked and was not written by
  this session. Left alone.
