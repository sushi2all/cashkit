# Handoff — Session S5 (Phases 9–10: version control, introspection, CLI)

**Date** 2026-08-05 · **Status** Phases 9 and 10 gates passed, committed.
**Suite** 866 → **1,007** passing (~31 s).

Per **ADR-0021**, which landed mid-session, v1 completes here: Phase 11 (the
packaged agent skill) is no longer a core deliverable, so this is the last
implementation session unless the orchestrator's verification finds a gap.

## What was built

### Phase 9 — version control

- **`cashkit/stores/revisions.py`** — the interface, written before the git
  store (ADR-0018). Four operations: write a revision from a state, list
  revisions, read a state at a revision, diff two revisions. A state is
  `Mapping[str, str]` — book-root-relative path to canonical text — so the
  revision store never learns what a Book is and the config store never learns
  what a revision is. No git noun appears in any signature, asserted by a test
  that strips docstrings and greps the rest.
- **`cashkit/stores/git_store.py`** — the v1 implementation, and the **only**
  module in the package that imports `pygit2`. Object database only: trees are
  built from text and committed by writing a ref; `repo.index` and
  `repo.checkout` appear nowhere, asserted structurally, and nothing shells out.
  Revision metadata rides in `cashkit-<key>:` commit trailers.
- **`cashkit/stores/config.py`** — the PRD §3.3 layout and its forward-only
  migrations across three schema generations. `.cashkit/config.toml` holds
  engine settings and **is tracked**, which closes D-P2-01 (open since S2).
- **`cashkit/stores/lock.py`** — the single-writer lock over all three stores
  (ADR-0010). `O_EXCL`, `CK-E013` for a live holder, `CK-W010` for a reclaimed
  dead one. There is no merge path anywhere, not even a private one.
- **`cashkit/stores/clock.py`** — the package's one wall-clock read.
- **`cashkit/sdk/kit.py`** — `CashKit`: the whole §6.6 surface (`commit`,
  `status`, `discard`, `history`, `at`, `diff_revisions`, `blame`) plus
  `reproduce()`, which asserts ADR-0006 rather than assuming it.

### Phase 10 — introspection and CLI

- **`cashkit/sdk/introspection.py`** — `trace()`, `why_zero()`, `depends_on()`,
  `dependents_of()`, `describe_book()`, and `render_expr()` (which re-parses to
  the same tree, so a trace never quotes a paraphrase).
- **`cashkit/sdk/validation.py`** — `validate()` over the §10.1 catalogue. It
  **runs the engine** rather than re-deriving its diagnostics.
- **`cashkit/model/introspection.py`** — the result models, every field
  non-optional with a meaningful empty value (ADR-0013).
- **`cashkit/cli/`** — `init`, `doctor --json`, `validate`, `run`, `status`,
  `commit`, `history`, `describe`, `serve --quack`. Every command takes
  `--json`; money is always a decimal *string*, never a JSON number.
- **`tests/test_no_llm_dependency.py`** — ADR-0016's import guard, three layers
  (client imports, inference endpoints, embedded prompts) plus a self-test that
  each layer catches a planted violation and a check of the declared extras.

## What the gates proved

### Phase 9 — `tests/test_revision_store.py` (51 tests)

**Gate 1.** A six-deep history: `at("HEAD~5").run("base").summary()` equals the
summary committed at that revision field for field, *and* differs from today's —
so the reproduction is of the old numbers, not of a constant. Every revision in
the history reproduces. On a forced engine-version mismatch the report says
`engine_version_matches=False`, `reproduced=False`, carries `CK-W011` and lists
the deltas — and it does **not** claim reproduction even though every number
happened to agree, because "these match" is a weaker statement than the
guarantee. A mismatch at *matching* engine version is `CK-E028`, an error.

**Gate 2.** A revision written with a completely different emitter (flow style,
unsorted keys, 4-space indent, single quotes) diffs semantically **empty** while
`reformatted` names every path whose bytes moved — so the empty answer is a real
comparison rather than one that never ran. A real change diffs non-empty, and
config diff and outcome diff arrive together (PRD §10).

**Gate 3.** A fixture repo whose three revisions were written in three schema
generations — generation 1 with the whole Book in `book.yaml`, generation 2 with
`items/` split out, generation 3 the current layout — migrates forward and
reproduces all three historical runs, each holding different numbers. A state
from a *newer* generation is refused with `CK-E026`. The historical generations
are written with `yaml.safe_dump`, not the canonical emitter, so the test also
proves that reading an old revision does not depend on today's bytes.

**Gate 4.** With a lock held, `commit()` returns `CK-E013` naming the pid, the
history does not advance and HEAD still holds the pre-change state — proved
in-process and again across a real subprocess boundary. A dead holder's lock
reclaims with `CK-W010`; a corrupt lockfile reclaims rather than being trusted;
an unjudgeable pid counts as alive and is refused rather than stolen. The lock
is asserted to be held at the moment `write_revision` is called.

Commit including snapshot recompute: **61 ms against a 3 s budget**.

### Phase 10 — `tests/test_introspection.py`, `test_validation.py`, `test_cli.py` (81 tests)

**Gate 1.** 1,100 sampled cells of the 50-item benchmark fixture — every item,
both measures, periods strided across five years so the sample crosses segment
boundaries, escalation anniversaries and settlement lags. Every node of every
trace, to depth 3: no `None` field, no blank formula, no unreconciled
explanation. A generated cell shows the canonical rounding order as ADR-0013
asks for it — `12000.0000 → 12730.8000 → 11457.7200`, with the escalation factor
(`1.0609`) and the probability named as bindings. `prev()` reports the period it
reached, or that it fell back to `init`. `agg()` names the concrete ids its
selector resolved to.

**Gate 2.** All five causes reachable and distinguished on one fixture book,
plus `"not_zero"` for a cell that is not zero (rather than forcing it into one
of the five), plus `also` for causes that are simultaneously true — a
pre-cutover period of a contract that starts later reports both, because fixing
one would leave the cell at zero.

**Gate 3.** Tested as its mechanical equivalent, in both directions. An agent
simulator that sees **only** the serialized `BookDescription` JSON — never the
Book — builds every `pivot()` call the vocabulary licenses, and all of them run.
Every field name *outside* the description is rejected by the store. A
description that omitted a legal value fails the first; one that invented an
illegal value fails the second. Same treatment for tag values, selector examples
(each asserted to match at least one item), frame columns and summary fields.

## Changes to earlier sessions' code

- **`cashkit/model/canonical.py`** (D-P9-14): the tuple branch unpacked *every*
  tuple as an `Amount.schedule` point, so the first model holding a tuple of
  anything else would have serialized as nonsense — and committing a snapshot
  means serializing `RunSummary`, which holds `tuple[Diagnostic, ...]`. The pair
  form is now keyed off the field `(Amount, "schedule")` rather than off the
  value's shape. No existing output changes; the S1 round-trip and golden-file
  tests confirm it.
- **`cashkit/model/diagnostics.py`**: eleven new codes (`CK-E025`…`CK-E030`,
  `CK-W011` in Phase 9), and **`CK-W004` / `CK-I001` had their `suggested_fix`
  rewritten** to name no jurisdiction mechanic (ADR-0021). A test now asserts
  the catalogue is free of them.
- **`cashkit/engine/__init__.py`**: `ENGINE_VERSION = "1"`. It must change
  whenever a change could move a number and must *not* change for a refactor
  that cannot — the whole `at()` guarantee is keyed on it.
- **`cashkit/model/reports.py`**: `OutcomeDiff`, `RevisionDiff`, `WorkingState`,
  `Reproduction`.
- **`cashkit/stores/frames.py`**: `serve_quack()` and `_quack_start()`, so the
  CLI never imports `duckdb` and the "only the frame store imports duckdb"
  guarantee survives `cashkit serve`.
- **`tests/test_wall_clock_lint.py`**: widened from five directories to the
  **whole package**, with exactly one allowlisted file (`stores/clock.py`) and a
  companion test proving only `git_store.py` and `lock.py` reach `wall_clock()`.
  Strictly stronger than before.
- **`tests/test_scenarios.py`**: the positional-segment-patching guard is scoped
  to the modules that can write an overlay, paired with a **new** test proving
  only those modules can construct an `ItemOverlay` — so the guard covers the
  whole write path over a smaller, provably complete surface. Needed because
  `trace()` must read a segment list to explain it (ADR-0013 requires exactly
  that) and `validate()` must read one to check every amount's sign.
- **`pyproject.toml`**: `pygit2` joins the **dev** group (same argument as
  `duckdb` in D-P8-15 — all four Phase 9 gates run through the git store, and a
  gate that skips is not a gate); `[project.scripts] cashkit`.

## Decisions recorded

**D-P9-01…15** — the four-operation interface over path→text; the linear ref
grammar (`HEAD`, `HEAD~n`, id — `HEAD^` deliberately refused); no merge path;
`.cashkit/config.toml` tracked, closing D-P2-01; the working tree *is* the
working state; one allowlisted wall-clock read; three schema generations and
what each changed; `diff_revisions()` semantic with the path diff separate;
`commit()` returning a report carrying `Revision | None`; reproduction's two
failure modes at different severities; `blame()` counting creation as a change;
`CK-E030` for a revision-bound kit; the lock spanning the whole commit; the
canonical emitter's tuple rule; `pygit2` in the dev group.

**D-P10-01…12** — `validate()` runs the engine; the three-way catalogue
partition as a test; one mistake, one code; a trace's value is the engine's;
no `None` in a `Trace`; `why_zero()`'s `"not_zero"` and `also`;
`describe_book()` enumerates; the CLI's decimal strings; `serve --quack`
refusing by default; the segment guard's scope; holiday resolution in the CLI;
**and D-P10-12, the removal of the coverage diagnostics**.

No new PRD conflicts. C-P1-01 and C-P8-01 remain the only two.

## The scope change, stated plainly

ADR-0020's coverage diagnostics (`CK-I010`…`CK-I015`) **were implemented in this
session and then removed**, on the ruling recorded in ADR-0021. ADR-0021's
consequences section says they were "caught before implementation"; that is not
quite what happened, and the difference matters only in that the removal was a
real deletion rather than a skipped task.

Removed: the six catalogue codes, `COVERAGE_MECHANICS` and its tag vocabulary,
`tax_coverage()`, `TaxCoverage` / `CoverageLine`,
`BookDescription.tax_coverage_tags`, `CashKit.tax_coverage()` and
`cashkit validate --coverage`. Nothing else in Phase 10 depended on them.

Kept, with jurisdiction-free wording: `CK-W004` and `CK-I001`. Both predate this
session and both describe the **model** — a settlement term whose counter-leg
the engine does not generate; a regime that schedules only what it accumulates —
rather than any jurisdiction's rules. Their old `suggested_fix` strings named
IRES/IRAP/INPS/TFR and "F24", which was the same content in a different place,
so both were rewritten and a test now guards the boundary.

## First thing S6 (or the orchestrator's verification) should check

Re-run `uv run pytest` — **1,007 tests must pass** (~31 s). Then check the one
thing this session could not check itself: **`ENGINE_VERSION` discipline.**

`cashkit/engine/__init__.py` now carries `ENGINE_VERSION = "1"`, and the whole
`at()` guarantee rests on it changing exactly when evaluation semantics change
and never otherwise. Nothing enforces that — it cannot be enforced mechanically,
because "could this change move a number" is a judgement. A future session that
touches `engine/` and leaves the string alone will make `reproduce()` report
`reproduced=True` for runs that no longer reproduce, which is precisely the
silent failure ADR-0006 exists to prevent. The next thing anyone does to
`engine/` should start by deciding whether that string moves.

Four more things worth knowing:

- **`validate()` costs a full run** (17 ms on the 50-item book) by design
  (D-P10-01). Anything tempted to make it cheaper by re-deriving the engine's
  diagnostics should read that entry first: a validator that disagrees with the
  engine is worse than none.
- **`Trace.reconciles` is the only guard on the one second implementation in the
  codebase** — a generated cell's arithmetic is rendered rather than evaluated,
  because there is no expression to evaluate. If a change to `expand.py` moves
  the canonical rounding order, the introspection gate fails loudly, which is
  the intended coupling. Do not weaken that assertion to make a test pass.
- **The revision store's second implementation is plausible but unwritten.**
  ADR-0018's test is that an append-only SQLite revision table *could* satisfy
  the protocol; D-P9-01 sketches the two tables. Nobody has written it, so the
  seam is argued rather than demonstrated. If offline mobile (ADR-0017
  configuration B) becomes real, that is the first thing to build, and the
  interface should need no change.
- **`.cashkit/config.toml` is tracked and holds engine settings only**
  (D-P9-04). Store backends stay constructor arguments. Anything that wants to
  add a machine-local setting must not put it in that file.

## Open items, deliberately not done here

- **Per-cell `status`** (D-P8-06) and **per-event tags on frame rows**
  (D-P5-09) — both still waiting on the same engine change, both still correctly
  described in S4's handoff. Phase 10 did not touch them.
- **The construction SDK is still partial.** PRD §6.1 lists `create_book`,
  `add_item`, `add_derived`, `set_param`, `retag`, `add_tax_regime`,
  `set_cutover`; only `validate` landed here. `cashkit init` builds a Book
  directly because there is no `create_book()` to call. This is the largest
  remaining hole in the SDK surface, and it is the one an app layer will hit
  first.
- **`reconcile()`** (PRD §6.2) is not implemented; `set_cutover` has no SDK
  entry point, so the monthly-close recipe has no path through the SDK.
- **Test coverage is still not measured** (open since S2): the ≥90% target on
  `engine/` and `model/` needs `pytest-cov`, which would change the dependency
  set. Every session has declined to make that call; someone should.
- **`km/notes/architecture-deck.html`** remains untracked and was not written by
  this session. Left alone.
