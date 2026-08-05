# CashKit

## Task

Implement CashKit as specified in `PRD-cashkit.md`, in Python, as an installable package with a complete test suite.

You are the **orchestrator**, running in a remote Claude Code session. You do not implement phases yourself: you spawn one **Opus 5 subagent** (model `claude-opus-5`) per session defined in §Execution model, in strict sequence, each in a fresh context, and you independently verify each session's gates before launching the next. Work autonomously — do not ask for approval between sessions; verify against the stated gates and continue.

## Repository

- Remote: `https://github.com/sushi2all/cashkit.git`. Clone it and work on `main`. Everything you and the session subagents need is in the repo: `PRD-cashkit.md`, this file, `CLAUDE.md`, `km/` (ADRs, notes).
- Push after each session's verification passes — the session's per-gate commits plus your verification state must reach the remote before the next session spawns, so progress survives the environment. Never force-push, never rewrite history.
- Commit rules: message names the phase or session; no `Co-Authored-By` trailers of any kind (see `CLAUDE.md`).

## Role and standard

You are building a financial modelling engine whose output people will use to decide whether to keep a company alive. Silent numerical error is the worst possible failure — worse than a crash, worse than missing features. Where correctness and convenience conflict, choose correctness and emit a diagnostic.

Write production Python: full type annotations, Pydantic v2 models, no `Any` outside deserialization boundaries, docstrings on every public SDK method stating what it returns and what diagnostics it can produce.

## Non-negotiable constraints

Violating any of these invalidates work downstream. If you find yourself wanting to break one, stop and write the reason into `DECISIONS.md` instead, then proceed within the constraint.

1. **No float for money.** int64 minor units at 4 decimal places in the engine core; `Decimal` at parse/serialize/display boundaries. Escalation factors `(1+r)^n` are computed in Decimal per distinct `(rate, n)` pair and applied as scaled-int64 multipliers (PRD §5.3) — no float in the money path at all. A float64 fast path is admissible only behind a property test proving byte-identity with the Decimal factor table, tie cases included.
2. **Formula semantics are `where`, not `if`.** Both branches always evaluate; selection is elementwise. Do not implement short-circuit conditionals. Every builtin must be expressible as a masked column operation.
3. **Nothing reads the wall clock during evaluation.** `cutover` is a stored field. None of `date.today()`, `datetime.now()`, `datetime.utcnow()`, `datetime.today()`, or `time.time()` may appear anywhere in `engine/` or `model/`. Add a lint check enforcing this.
4. **The union of Item-expansion and Event facts happens before derived evaluation.** If `agg()` cannot see actuals, every derived item is wrong.
5. **Actuals are immutable.** No code path allows a scenario overlay to modify an event with `status="actual"`.
6. **Errors are `Diagnostic` objects, not exceptions.** Exceptions are reserved for programmer error (wrong type, missing store, corrupt file). Anything a user or agent could plausibly do wrong returns a structured diagnostic with a `suggested_fix`.
7. **Git is never exposed in the SDK surface.** No method takes a git ref-spec other than the opaque `ref` string, and no method shells out to `git`. Use pygit2 against the object database — no worktree, no index, no checkout.
8. **`segments` is atomic in scenario overlays.** No positional patching, no segment-ID matching, no partial merge.

## Anti-patterns to avoid explicitly

- Writing the naive per-period × per-item Python loop and planning to optimize later. The vectorized design constrains the formula language; retrofitting is impossible. Build the SCC-condensation evaluator from the start. (Keep the naive loop **only** as a test oracle: it ships as `cashkit/reference/` and is exercised from `tests/property/`.)
- `yaml.dump()` on a Pydantic model. Write the canonical emitter first.
- Denormalizing tags into the fact table.
- Making `Recurrence` optional on `Segment` to handle one-offs. One-offs are Events.
- Adding a `lag` field alongside `Settlement.due`. `due` is the only representation.
- Treating a VAT credit as a cash inflow.
- Special-casing "the base scenario". Base is a scenario with `parent=None`.

---

## Execution model — sessions and subagents

Phases run inside **sessions**: one fresh-context Opus 5 subagent per session, strictly in sequence. Fresh context per session is deliberate:

- The numeric constraints (rounding order, canonical serialization, `where`-not-`if`) must be re-read verbatim each session, not survive as lossy summaries through context compaction — silent numerical error is the worst failure, and it is exactly what compaction breeds.
- A session boundary makes each gate a hard stop verified from committed evidence, not from the momentum of a long context.
- If a session cannot pick up the work from the repository alone, that is a documentation gap to surface and fix, not to work around. The project bets on `DECISIONS.md`, ADRs and tests being sufficient external state; sessions test that bet continuously.

| Session | Phases | Scope | Seam rationale |
|---|---|---|---|
| S1 | 1 | Models + canonical serialization | Self-contained foundation; the round-trip gate is what everything else trusts |
| S2 | 2–4 | Reference engine + formula front-end, vectorized engine, formula hardening | One semantic unit (ADR-0001); the dual-engine gate needs both evaluators in one context |
| S3 | 5–6 | Ledger, cutover, fact union; VAT and tax regimes | Facts entering evaluation; first cold-read test of the engine's documentation |
| S4 | 7–8 | Scenarios; frame store and views | Consumers of the engine, not modifiers of it |
| S5 | 9–10 | Version control; introspection and CLI | Read-side surfaces over stable internals |
| ~~S6~~ | ~~11~~ | ~~Agent skill package~~ | Removed from core — app-layer per ADR-0021 |

### Orchestrator protocol

1. Spawn the session's subagent with a brief containing: this file, `CLAUDE.md`, `km/adr/index.md`, the session's phase range, and the session protocol below. Model: Opus 5 (`claude-opus-5`); the orchestrator itself runs on Fable. One session at a time — never in parallel; the sequence is a dependency chain.
2. When the subagent returns, verify independently before launching the next session: re-run the full test suite yourself; confirm one commit per gate exists; confirm `DECISIONS.md` and `BENCHMARKS.md` are current; read the session's handoff note.
3. If verification fails, respawn the **same session** as a fresh subagent with the failure evidence in its brief. Do not patch implementation code in the orchestrator context — the repo must stay explainable by its own history.
4. The repository is the only channel between sessions. Never carry gate evidence forward in your own context; if the next session needs to know something, it belongs in the repo.

### Session protocol (goes verbatim into every subagent brief)

- **Start:** read `PRD-cashkit.md`, this file, `CLAUDE.md`, `km/adr/index.md`, and any handoff notes in `km/notes/`. Re-run the entire existing test suite before writing any code. A pre-existing failure ends the session immediately — report it to the orchestrator; never fix another session's work silently.
- **Work:** the phases in scope, in order. Each phase's gate must pass before the next phase starts. Commit at each gate with a message naming the phase.
- **End:** gate evidence committed (tests + fixtures); `DECISIONS.md` and `BENCHMARKS.md` current; working tree clean; write `km/notes/handoff-s<N>.md` stating what was built, what the gates proved, and the first thing the next session should verify.

---

## Phase plan

Phases group into the sessions above. Each phase has a gate. Do not proceed until the gate passes. Commit at each gate with a message naming the phase.

**Session S1 — fresh implementation subagent**

### Phase 1 — Models and canonical serialization

Implement every model in PRD §4. Implement the canonical YAML emitter: fixed field order per model, Decimals as quoted strings, dates as ISO, `None` omitted, no flow-style collections, LF endings, trailing newline.

**Gate:** Hypothesis property test generating arbitrary valid `Book` objects proves `parse(serialize(x)) == x` and `serialize(parse(s)) == s` byte-for-byte. 200+ generated cases, zero failures. Phantom diffs are a build failure, not a warning.

**Session S2 — fresh implementation subagent (Phases 2–4)**

### Phase 2 — Reference engine (the oracle)

Implement the naive per-period, per-item, `Decimal` evaluator described in PRD §5.2 as `cashkit.reference`. Correctness only; performance irrelevant. This is the oracle every later optimization is tested against, and it is the artifact that makes the rest of the project safe.

This phase includes the **minimal formula front-end** the oracle needs: the restricted-AST parser, symbol table and builtin semantics from PRD §5.4 (including `prev()` init values and masked-safe division), evaluated naively. Phase 4 does not introduce the language — it hardens this front-end.

**Gate:** Runs a 20-item fixture book with multi-segment items, escalation, settlement splits and a `prev()` feedback loop, producing hand-verified numbers. Hand-verify at least three periods against a spreadsheet you construct and commit as `tests/fixtures/hand_verified.csv`.

### Phase 3 — Graph, condensation, vectorized engine

Build the dependency graph including `prev()` edges. Compute strongly connected components (Tarjan) and the condensation. Trivial SCCs evaluate as int64 column expressions over the full horizon; non-trivial SCCs get the sequential fold over a pre-summed `net[t]` vector.

Implement vectorized segment expansion: date-index masking, escalation over a year-index vector, settlement as array shifts, `agg()` as row-sums over resolved slices.

**Gate:** Dual-engine equality. The vectorized engine and the reference engine produce **byte-identical** results on a corpus of ≥50 generated books covering: multi-segment items, all `Recurrence` anchors, business-day adjustment, all `DueTerm` shapes including `remainder` clamping, `prev(n>1)`, feedback loops, `agg()` selectors, empty-`due` accrual-only items, `probability < 1` weighting, withholding, and mixed-sign (credit-note) amounts. Zero tolerance — exact integer equality.

Performance gate: 50 items × 1826 periods cold run < 50 ms; delta recompute < 5 ms.

### Phase 4 — Formula language

Harden the formula front-end built in Phase 2. Whitelist node types; reject attribute access, comprehensions, lambdas, imports, and any call outside the builtin table. Complete the symbol table from PRD §5.4, including the selector grammar.

Resolve `agg()` selectors to concrete item IDs at graph-build time. Reject self-dependency with a diagnostic naming the cycle.

**Gate:** A fuzz corpus of malicious and malformed formula strings (attribute access, `__builtins__`, deeply nested calls, division by zero, unknown identifiers, circular refs) produces diagnostics, never exceptions and never execution. Every builtin has a vectorization test proving it operates as a column op.

**Session S3 — fresh implementation subagent (Phases 5–6)**

### Phase 5 — Ledger and events

SQLite store. `UNIQUE(source, ext_id)`. `import_events` is idempotent on re-import; a conflicting payload aborts the batch (PRD §6.2). Implement `void_event` tombstoning. Implement the fact union with generative expansion, and the cutover rule per PRD §3.2: before cutover, generation is suppressed for **all** items and the ledger is authoritative; from cutover forward, generation resumes and committed/forecast events apply; actuals dated on/after cutover raise `CK-W003`, never a dedup guess.

**Gate:** Re-importing the same 5,000-row CSV three times yields identical ledger state and an `ImportReport` reporting the skips. A book with cutover mid-horizon shows actuals before and forecast after with no double-count and no gap at the boundary — verified by a total-sum invariant test.

### Phase 6 — VAT and tax regimes

`VatSpec` per item, `TaxRegime` at entity level. Accumulate output and input VAT per regime period, net, emit one cash event at `period_end + payment_offset`. Credit carry-forward as a stock that offsets future liability. Withholding at settlement.

**Gate:** A fixture entity with mixed VAT rates, one exempt item, one reverse-charge item, 60-day customer terms and quarterly accrual-basis VAT reproduces a hand-computed F24 schedule. A second fixture with input > output for two consecutive quarters shows a credit stock, not a negative payment. Flipping `measure` to `"cash"` shifts the liability correctly and is covered by its own test.

**Session S4 — fresh implementation subagent (Phases 7–8)**

### Phase 7 — Scenarios

Sparse overlays, value-authored. Resolution walks the parent chain with item-level last-write-wins and atomic `segments`. `ChangeReport` reports only fields actually different. Macros expand immediately. `provenance()` reports which ancestor set each field.

**Gate:** `set_item()` with an unchanged item returns an empty `ChangeReport` and writes nothing. A three-level fork chain resolves correctly. Correcting `tags` in base propagates to a child that did not override tags, and does not propagate to one that did. Two scenarios reaching identical state by different routes show empty `diff()`.

### Phase 8 — Frame store and views

Tidy/long canonical format. DuckDB materialization with `DECIMAL(18,4)`. Tag dimension table joined on demand. Aggregation to coarser grain respecting `agg_rule`. Parquet export. `pivot()`, `frame(where=...)`, `summary()`.

**Gate:** Aggregating a day-grain frame to month, quarter and year preserves totals exactly for flows and takes last-in-period for stocks. A tag-sliced sum equals the sum of the corresponding items. Parquet round-trips without precision loss.

**Session S5 — fresh implementation subagent (Phases 9–10)**

### Phase 9 — Version control

pygit2 against the object database. `commit()`, `status()`, `discard()`, `history()`, `at()`, `diff_revisions()`, `blame()`. Ledger watermark on the book so historical runs see a truncated ledger. `.cashkit/version` and a forward-only migration path.

**Gate:** `at("HEAD~5").run(s).summary()` reproduces the summary committed at that revision, exactly, when the current engine version matches the snapshot's recorded `engine_version`; on mismatch the comparison surfaces the engine delta, never a silent failure. A reformat-only change produces an empty `diff_revisions()`. A fixture repo spanning three schema generations migrates and reproduces all historical runs. Two concurrent writers: the second fails loudly, never merges silently.

### Phase 10 — Introspection and CLI

`trace()`, `why_zero()`, `depends_on()`, `describe_book()`, `validate()` with the full diagnostic catalogue (PRD §10.1). CLI: `init`, `doctor --json`, `validate`, `run`, `status`, `commit`, `history`, `serve --quack` (feature-flagged per PRD §3.4).

**Gate:** `trace()` on any cell of a 50-item fixture returns formula, resolved bindings and arithmetic to depth 3 with no `None` fields. `why_zero()` distinguishes all five zero causes. `describe_book()` output is complete enough that a fresh agent, given only that output, writes a working `pivot()` call with no invalid field names.

**Session S6 — REMOVED FROM CORE (ADR-0021, 2026-08-05)**

### Phase 11 — Agent skill package — *moved to the app layer*

Per ADR-0021 the core is a pure logic/calculation engine: the agent implementation, its skill package, recipes, and all tax-content behaviour are the domain of applications built on top of the SDK. The Phase 11 gate (fresh-agent end-to-end transcript) moves to the app-layer backlog alongside the UI (ADR-0013/0014) and the intent grammar (ADR-0019, draft in `km/notes/intent-schema-draft.md`). **v1 of the core completes at Phase 10 / Session S5.**

---

## Deliverables

```
cashkit/
  model/          # Pydantic models, canonical serializer
  engine/         # graph, condensation, columns, fold, formula AST
  reference/      # naive Decimal oracle — kept forever, never deleted
  stores/         # config (YAML+git), ledger (SQLite), frames (DuckDB)
  sdk/            # the public API surface
  cli/
tests/
  property/       # Hypothesis: round-trip, dual-engine, invariants
  fixtures/       # hand-verified books and expected outputs
DECISIONS.md      # every design choice made under ambiguity, with reasoning
BENCHMARKS.md     # measured numbers per phase gate, on stated hardware
README.md
```

## Definition of done

All acceptance criteria in PRD §10 pass, excluding agent-skill criteria (app-layer per ADR-0021). Additionally:

- The reference engine still exists and still agrees with the vectorized engine.
- No `date.today()` in `engine/` or `model/` (enforced by lint).
- No `float` in money paths (enforced by a type-audit test).
- No LLM dependency anywhere under `cashkit/` (enforced by lint, ADR-0016).
- Test coverage ≥ 90% on `engine/` and `model/`.
- `DECISIONS.md` records every judgement call, including ones where you chose against an instinct.
- All five session handoff notes (S1–S5) exist in `km/notes/`, and the orchestrator has re-run the full suite green after S5.

## When you hit ambiguity

Applies to every session subagent. The PRD will not cover everything. When it does not:

1. Choose the option that preserves determinism and exactness.
2. Choose the option that produces a diagnostic over the one that guesses.
3. Choose the option that keeps the storage layer swappable.
4. Write the choice and its reasoning into `DECISIONS.md` immediately.
5. Continue. Do not stall for clarification.

If a PRD requirement turns out to be internally inconsistent, implement the interpretation that satisfies the §2 core decisions, and record the inconsistency in `DECISIONS.md` under a `## PRD conflicts` heading.
