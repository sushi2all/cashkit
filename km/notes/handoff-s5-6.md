# Handoff — Session S5.6 (§6.4 wiring, cutover guard, coverage gate)

**Date** 2026-08-07 · **Status** gate passed, committed, not pushed.
**Suite** 1,054 → **1,105** passing (~21 s). `ENGINE_VERSION` **unchanged**
(`"1"`) — `git diff --stat 32eb48e..HEAD -- cashkit/engine/` is empty.

The closure session for the SDK surface. Three items from `handoff-s5-5`'s open
list, nothing else: the §6.4 verbs that had no entry point, the cutover that
accepted any date, and the coverage number that was measured but not enforced.

## What was built

### 1 · `cashkit/sdk/execution.py` — PRD §6.4

`frame`, `pivot`, `compare` and `export` are now `CashKit` methods taking a
`RunRef`, alongside free functions taking the kit first (the `construction.py` /
`events.py` idiom). `read_export` came with them, because an SDK that writes a
file it cannot read back is one whose round-trip nobody can check without
dropping below the §6 surface.

This is wiring and it added **no arithmetic** (D-S56-01). Aggregation rules, the
selector join, the `DECIMAL(18,4)` path and the Parquet `COPY` all stay in
`stores/frames.py`. Three things the SDK layer adds:

- **The run becomes a frame without anyone saying so.** The store takes a
  `run_id` and expects the caller to have materialized it; here a `RunRef` is
  the argument. The key is §6.6's `(revision, scenario, engine_version,
  ledger_watermark)` plus the *effective* cutover, so a `cutover_override` run —
  "a deliberate query, not a property of the model" — cannot overwrite the
  model's own frame. A live kit's key says `working`, which is honest rather
  than unique, so **every call re-materializes** rather than trusting the key
  (D-S56-03). `compare()` disambiguates two runs sharing a key with `#n`
  instead of collapsing them.
- **DuckDB stays optional.** The frame store is imported lazily and a failed
  import becomes `CK-E033`, naming the extra to install (D-S56-05). An agent can
  loop on a diagnostic; it cannot loop on an `ImportError` from three frames
  down. `summary()`, `trace()` and `why_zero()` never needed the extra.
- **Agent-authored strings are validated first.** `where=` goes through the one
  §5.4 grammar and comes back as `CK-E003`. A closed vocabulary — measure,
  pivot index, column spec, export format — still raises, because §6.5's own
  definition of programmer error is a caller passing something the schema
  enumerates (D-S56-06).

The kit's frame store is **in memory**, not `frames.duckdb` (D-S56-02): `at(ref)`
shares this kit's `root` and carries its own rounding policy, `cashkit serve`
opens that file read-only over Quack, and every call re-materializes anyway so
persistence buys no correctness. `DuckdbFrameStore(root / "frames.duckdb")` is
untouched and still available.

`Table` gained a `diagnostics` field, defaulting to empty (D-S56-04). It is the
only way to hold §6.4's `-> Table` and §6.5's "every fallible operation returns
diagnostics" at once, and it keeps the distinction the catalogue exists for: an
empty table reporting nothing matched nothing, an empty table reporting
`CK-E033` never ran. `export` returns an `ExportReport(ChangeReport)` carrying
`path: Path | None`, following `commit()`'s precedent (D-P9-09, C-S55-01).

A relative export path lands under `<root>/exports/` per §3.3, git-ignored; an
absolute path is written where it says (D-S56-07).

### 2 · `CK-W006` — the cutover guard

`set_cutover` accepted any date. Past `horizon.end` it suppressed every
generative occurrence there is: the book compiled, the run succeeded, every
number was zero, and nothing anywhere said why.

**A warning, not an error, and one code for both directions** (D-S56-09). Both
are things a user can plausibly mean — before `horizon.start` is the natural
state of a book never reconciled, past `horizon.end` is the ordering an agent
lands in when it closes a window before extending the horizon. Refusing either
would make a legal sequence of writes unconstructible, which is D-S55-01's own
test. The consequence differs by direction and rides in the message via an
`effect` placeholder, so an agent matches on one code to ask one question.

The horizon is half-open, so `horizon.end` itself is inside it: the predicate is
`horizon.start <= day <= horizon.end`. A cutover *at* the end suppresses
everything too, but it is the boundary the model's arithmetic reaches, and
naming it a mistake would be naming the horizon a mistake.

`cutover_problem()` lives in `validation.py` with two callers — `set_cutover()`
so the agent that caused the state hears about it, and `validate()` so a book
opened from disk in that state is not silent either (D-S56-10). One function, so
the two answers cannot drift. `create_book(cutover=…)` is deliberately not a
third caller: the session scope named two doors.

### 3 · The coverage gate

```
uv run pytest                            # everything, uninstrumented
uv run pytest -m "not benchmark" --cov   # the gate, fails below 90%
```

`pytest-cov` is in the dev group; `[tool.coverage.run] source` is exactly
`cashkit/engine` + `cashkit/model`; `[tool.coverage.report] fail_under = 90` is
what makes the run exit non-zero. The threshold lives in `pyproject.toml` so no
invocation can lower it quietly (D-S56-11).

`addopts` deliberately carries no `--cov`. S5 verified the §5.2 budgets fail
under coverage tracing; re-verified here — delta recompute goes from ~5 ms to
**12.54 ms** against a 5 ms budget. All nine timing tests already carry
`@pytest.mark.benchmark`, which is what the gate deselects.

`tests/test_coverage_gate.py` asserts the *configuration* rather than
re-measuring the percentage — running the suite inside the suite would double
the wall clock to re-derive a number the gate already prints, while what can
silently break is the config. Its last test is structural and holds for tests
nobody has written yet: **every function reading `perf_counter` must carry the
benchmark marker**, because an unmarked timing test runs instrumented and fails
for a reason unrelated to the engine — the kind of failure that gets a budget
loosened rather than a cause found.

## What the gate proved

**Gate 1 — the kit is the store, with nothing added.** Every §6.4 result is
asserted equal *as a whole `Table`* — not summed and compared — to the same query
run against a store the test materialized itself from the same `RunRef`.
`frame` at all five grains and across five slicings (`measures`, `status`, two
selectors, `include_synthetic=False`), `pivot` across four index/column
combinations, `compare`, and `export` **byte for byte** against
`DuckdbFrameStore.export`. The frame is non-empty where equality is claimed:
3 items × 3 months × 2 measures = 18 rows, each measure summing to
`36000.0000`. `_FRAME_COLUMNS` is asserted equal to the store's own
`FRAME_COLUMNS` — the duplication is forced by the lazy import, the drift is not.

**Gate 1b — export.** A relative path lands at `<root>/exports/q1.parquet` and
`exports/` is in the book's `.gitignore`; the file re-reads through
`kit.read_export()` **equal to `kit.frame()`**, with every value an `isinstance`
`Decimal` rather than a float that prints the same; CSV round-trips too; an
absolute path is written there and *not* under `exports/`.

**Gate 2 — the extra is optional, both ways.** With
`sys.modules["duckdb"] = None` and `cashkit.stores.frames` evicted, all four
verbs plus `read_export` return rather than raise, each carrying exactly
`["CK-E033"]` with `duckdb` named in its `suggested_fix`; the refused `frame`
still declares its seven columns; `export` writes nothing and `exports/` does not
appear; and `summary()` still reports `net_cash == 36000.0000`, because the
question the system exists to answer needs no columnar engine. With the extra,
the same calls are `ok` and non-empty. A subprocess test proves the SDK does not
reach duckdb *transitively*: importing `cashkit.sdk` loads neither `duckdb` nor
the frame store.

**Gate 3 — cutover.** `set_cutover(2026-05-01)` on a `[2026-01-01, 2026-04-01)`
book records the change and reports exactly `CK-W006`, naming the horizon and
the word "suppressed" — and the numbers back it up: a rent item added afterwards
gives `net_cash == 0`. `set_cutover(2025-12-01)` reports the same code with "no
effect". `2026-01-01`, `2026-02-01` and `2026-04-01` report nothing new. A book
left in the state, closed and reopened from disk, has `CK-W006` in `validate()`;
moving the cutover back inside removes it.

**Gate 4 — a bound kit reads its own revision.** Two commits apart, the same
kit's `frame()` shows four items totalling `34500.0000` while
`at(first).frame()` shows three totalling `36000.0000`; the two run keys start
with the revision id and `working` respectively, so they cannot collide in one
store. `frame`, `pivot` and `export` all succeed on the bound kit while
`commit()` and `discard()` return `CK-E030`.

**Gate 5 — the coverage gate fails.** `fail_under = 99.9` → *"FAIL Required test
coverage of 99.9% not reached. Total coverage: 96.53%"*, **exit code 1**.
Restored to 90 → exit code 0, *"Required test coverage of 90.0% reached. Total
coverage: 96.56%"*, 1,096 passed / 9 deselected. Benchmarks uninstrumented:
9 passed in 2.87 s.

**Gate 6 — nothing else moved.** 1,105 pass. The wall-clock, no-float, no-LLM,
duckdb-import, overlay-writer and catalogue-partition lints all pass untouched.

## Decisions recorded

**D-S56-01…11** — the four verbs are wiring, not design; the kit's store is in
memory; the run key and re-materialization; `Table.diagnostics` and
`ExportReport`; `CK-E033`; selectors validated / closed vocabularies raise;
relative exports land in `exports/`; a bound kit frames and exports but does not
commit; `CK-W006` is one warning covering both directions; one predicate with two
callers; the two-command coverage gate.

No new PRD conflicts. C-P1-01, C-P8-01 and C-S55-01 remain the only three.

## Changes to earlier sessions' code

- **`cashkit/model/table.py`** — one field, `diagnostics`, defaulting to empty,
  and the `ok` property. Every existing producer is unchanged and every existing
  `Table` comparison still holds.
- **`cashkit/model/diagnostics.py`** — `CK-W006`, `CK-E033`.
- **`cashkit/sdk/validation.py`** — `cutover_problem()`, called from
  `validate()`; `CK-E033` added to `OPERATION_TIME_CODES`.
- **`cashkit/sdk/construction.py`** — `set_cutover` calls `cutover_problem`.
  Nothing else changed.
- **`cashkit/sdk/kit.py`** — a `frames` field and five delegates. No existing
  method changed.
- **`pyproject.toml`** — `pytest-cov`, `[tool.coverage.*]`. `addopts`
  unchanged.
- **`README.md`** — the two commands and why there are two.
- **`tests/test_validation.py`**, **`tests/test_diagnostics_catalogue.py`** — the
  two new codes registered, with reasons.

Nothing under `engine/`, `reference/` or `stores/` was modified.

## `ENGINE_VERSION`: not bumped, deliberately

Still `"1"`. No file under `cashkit/engine/` was touched — this session *calls*
`Engine.run`, `parse_selector` and `compile_book` and changes none of them. No
evaluation semantics moved, so bumping would be the opposite error: it would
make every existing snapshot report `engine_version_matches=False` and downgrade
a real `CK-E028` into a shrugged `CK-W011`. The check to re-run is
`git diff --stat <base>..HEAD -- cashkit/engine/`, which is empty.

## What §6 still lacks

Carried forward from S5.5, plus what this session found.

**Found here, not closed — read this one first:**

- **A revision-bound kit's §6.1 writes are not refused.** S5.5 closed this for
  the four ledger verbs (`CK-E030`, D-S55-11) and for `commit`/`discard`. The
  construction verbs — `add_item`, `add_derived`, `set_param`, `retag`,
  `add_tax_regime`, `set_cutover` — have no such guard: they mutate the bound
  kit's in-memory book and call `kit.save()`, which writes the **live** working
  tree at the shared `root`. Reproduced: on a committed book,
  `kit.at(ref).set_param("b", 2)` returns `changed=('params.b',)` with no
  diagnostic, the param lands in `book.yaml` on disk, and the live in-memory kit
  still reports `status().clean is True` while a reopened kit reports
  `clean is False`. It is the same hole S5.5 named for the ledger — "a write
  reached through a bound kit appends to the present while reading the past" —
  in the one place it was not looked for. The fix is an `_authored_write` guard
  mirroring `_ledger_write`, and it wants its own gate; left alone here because
  the session scope was three items and a write-path change in a closure session
  is exactly what gates exist to stop.

**Carried from S5.5, still open:**

- **`ItemSpec` is not a thing.** §6.1 types `add_item(book, spec: ItemSpec)`;
  `Item` is what the signature takes and almost certainly what the PRD meant
  (§4.2 defines `Item`, nothing defines `ItemSpec`). The name stays reserved for
  a friendlier authoring shape — durations instead of explicit segments.
- **`reconcile` compares one measure at a time** (`cash` by default, `accrual`
  on request). A report carrying both is strictly additive to
  `ReconciliationReport`.
- **Per-cell `status`** (D-P8-06) and **per-event tags on frame rows**
  (D-P5-09) — still waiting on the same engine change, still correctly described
  in S4's handoff. Untouched, deliberately: D-P8-06 records per-status frames as
  absent by design.

**New, minor:**

- **The kit's frame store is never closed.** It is in-memory and dies with the
  kit, so nothing leaks past the process, but `CashKit` has no `close()` and a
  long-lived process opening many kits holds one DuckDB connection each. A
  context-manager `CashKit` would close it and the ledger together.
- **`compare()` labels columns with run keys**, which are unambiguous and ugly
  (`working|base|1|-|2026-01-01`). A caller-supplied label list would be the
  friendlier surface; the key stays the right default because it is the only
  thing that distinguishes two runs of the same scenario.

## Untracked files this session did not write and did not stage

`km/notes/architecture-deck.html` (pre-existing) and `QUICKSTART.md`, which
appeared in the working tree at 23:31 on 2026-08-07 from outside this session.
Neither was staged; explicit paths were used for every `git add`.
