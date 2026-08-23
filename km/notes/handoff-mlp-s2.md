# Handoff — MLP session S2 (model layer)

**Date** 2026-08-23 · **Scope** `apps/service/cashkit_service/agent/` + `POST /turns` · **Model** `google/gemini-3.7-flash` (pinned, ADR-0028)

S2 built the whole model-facing layer of the service on top of S1's proposal machinery: the hardened transport, the turn pipeline (interpret → guard → propose → verify), the bounded read-only Q&A loop, turn and model-call persistence with the SPEC §11 correlation chain, and the SPEC §8 cost and rate guardrails. The one surface addition is `POST /turns`.

Nothing S1 built was reinterpreted. Two S1 files gained additions — `intents/read.py` (R1 and R9 payloads, D-MLP-26) and `envelope.py` (an optional `overlay` argument) — and every S1 test still passes unchanged.

---

## 1. What was built

### The agent layer (`apps/service/cashkit_service/agent/`)

| File | What it does |
|---|---|
| `transport.py` | The hardened model transport: `json_object` response format, first-object `raw_decode`, bracket-stack repair, and zero-retention routing sent per call. **One call in, one completion out** — the retry loop is the pipeline's, so each call is one journal row |
| `journal.py` | The `turns` and `llm_calls` tables, and `log_chain()` — the SPEC §11 chain in every log line. Written on its own connection, so it outlives the failure it records |
| `budget.py` | SPEC §8: $0.50/day per user, 30 turns/hour, 5 imports/day, checked before the first model call |
| `snapshot.py` | The compact book state **plus the results block** — the engine's own closing balance per month, min cash, runway and per-item totals. Proto T11 is why it exists |
| `prompts.py` | The model's whole surface: the 21 intents plus `query_ledger`, one worked example each, the "quote, never recompute" rule, and the SPEC §5-F1 voice rule |
| `guard.py` | **ADR-0029 as code.** Sorts the model's artifact into reads, held mutations and deferred `save`. Host operations are unreachable by construction |
| `tools.py` | Executes read operations into receipts, and adds `query_ledger`, the one host read tool |
| `verify.py` | The bounded verification call: applies the operations to a throwaway copy and traces what moved, for the enumerated triggers M3/M4 |
| `pipeline.py` | `run_turn()` — SPEC §2.3 in order, with the book lock and the model call never overlapping |
| `routers/turns.py` | `POST /turns`. Thin on purpose: it reaches the book only through `proposals.create()` |

### Trials and tests

`trials/live.py` (live-model fixtures and helpers) · `trials/t01…t12` (ported, live) · `trials/t14` (new, scripted) · `trials/t13` (re-run with turn-originated cards) · `tests/fake_model.py` (the scripted provider) · `tests/test_transport.py` · `tests/test_guard.py` · `tests/test_prompt_surface.py` · `tests/test_turns.py` · `tests/test_guardrails.py` · `tests/test_read_intents_quoting.py`.

---

## 2. Exact commands to reproduce every gate

Run from the repository root.

```bash
# Setup, once. The test fixture starts the container itself if it is not up.
uv sync --all-packages
docker compose -f apps/service/docker-compose.dev.yml up -d --wait

# Everything that does not call a model. This is the per-commit run.
uv run pytest apps/service/tests apps/service/trials -q     # → 326 passed, 31 deselected

# The model-behaviour gate. Needs OPENROUTER_API_KEY (repo-root .env is read).
uv run pytest apps/service/trials -m live_model -q          # → 31 passed, 61 deselected

# The engine suite is untouched and still the default.
uv run pytest --collect-only -q | tail -3                   # → only tests/…
```

The live run costs about **$0.10** and takes about six minutes. The 31 deselected in the first command are exactly the 31 selected in the second: `apps/service/pyproject.toml` sets `addopts = "-m 'not live_model'"`, so a per-commit run excludes them and the scheduled run asks for them by name (SPEC §10, D-MLP-35). Without a key the live trials **skip**; they never fail for the want of one.

Per gate clause:

| Gate clause | Command | Expected |
|---|---|---|
| Ported trials T01–T12 on the pinned model | `uv run pytest apps/service/trials -m live_model -q` | 31 passed |
| T14 — question turns never write | `uv run pytest apps/service/trials/t14_question_turns_never_write.py -q` | 8 passed |
| T13 — re-run with turn-originated proposals | `uv run pytest apps/service/trials/t13_no_unproposed_mutation.py -q` | 23 passed |
| Cost + rate limits enforced | `uv run pytest apps/service/tests/test_guardrails.py -q` | 12 passed |
| One `llm_calls` row per call, chain intact | `uv run pytest apps/service/tests/test_turns.py -q` | 34 passed |
| The model's surface | `uv run pytest apps/service/tests/test_guard.py apps/service/tests/test_prompt_surface.py -q` | 41 passed |

Regenerate the OpenAPI schema (the drift test compares against the committed copy):

```bash
uv run python -m cashkit_service.openapi     # → wrote apps/service/openapi.json
```

---

## 3. What each gate proves, against the PROMPT's wording

> **Ported trials T01–T12 green against the live service on `google/gemini-3.7-flash`**

`trials/t01_…` to `trials/t12_…`, 31 tests, all through `POST /turns` and `POST /proposals/{id}`, asserting **final book state numerically**. Every expected figure is arithmetic over the sentence the user said, computed in `Decimal` inside the trial, so no assertion is a copy of what the engine happened to produce.

What each one guards, and why it was worth porting: T01 the exclusive-end window (proto T06 caught lite paying a line one month too long); T02 that an edit from a date splits the segment instead of rewriting the past; T03 the failure class money cannot tolerate — asked for the recursive overdraft rule, the model says the book cannot express it rather than authoring a flat line, and the grammar has no formula slot for it to author one through; T04 that a fork moves and base does not; T05 the export of a model-authored book; T06 that the export stays self-describing enough to rebuild from; T07 the messy sheet, asserted on the **closing balance of every month** rather than on the items, because a December bonus is equally correct as an event or as a windowed line and a trial that insisted on one would fail a right answer; T08 accrual apart from cash on net-45 terms, including December's invoice correctly dropping outside the horizon; T09 that the engine counts Mondays in the calendar (52 in 2026 from the 5th of January, not 365/7); T10 the whole path from sign-in to saved forecast; T11 and T12 numeric Q&A.

Two are ported in a narrowed form, deliberately and recorded as D-MLP-27: **T06 and T07 paste the table as text**, because the xlsx pipeline with its SSE progress and reconciliation loop is S5's (SPEC §7, gate T16), and what S2 owns is the model-behaviour half. **T10 walks the API path** an interface will drive, because the browser end-to-end is S3's gate.

> **T14 (question turns never write)**

`trials/t14_question_turns_never_write.py`, 8 tests. It does not test that the model behaves — its scripted provider answers the T11 question *and* emits two write operations, every time, on purpose. The book's own bytes are read through a second kit (revision + overlay fingerprint, not through the service) and do not move. Also covered: all twelve read intents plus `query_ledger` as turns, a write emitted mid-Q&A-loop, the loop's call bound, a model naming host operations, and a model asking to save. This is the structural half of ADR-0029; `t11_qa_affordability.py` is the live half.

> **T13 re-run including turn-originated proposals**

`trials/t13_no_unproposed_mutation.py`, 23 tests, of which 9 are new. A card the model produced is subject to the same confirmation, expiry, ownership and staleness rules as a card a button produced, because it is the same row in the same store: unconfirmed, discarded, expired, twice-applied, another account's, refused-on-error, superseded-by-save, and superseding a button card. The route inventory now names `/turns` and states what it does — produces a proposal, applies nothing.

> **Cost + rate limits demonstrably enforced**

`tests/test_guardrails.py`, 12 tests. Each limit is driven until it trips, and the assertion is that the transport was **not called** on the refused turn — the check runs before the spend, so a refused turn costs nothing. Covered: the hourly limit and its lifting with the hour, the daily budget and its reset the next day, that one user's spend does not limit another, that a refusal still writes a `turns` row with zero cost, the import counter (S5 wires the endpoint), the SPEC §8 defaults, and that the refusal copy obeys the §5-F1 voice rule — at most two sentences, no apology, no hedging.

> **Every model call lands one `llm_calls` row with the request_id correlation chain intact (SPEC §11)**

`tests/test_turns.py`. `transport.complete()` makes exactly one call and returns exactly one completion, so `journal.record()` writes exactly one row — including for a failed attempt, which is recorded with its error rather than swallowed. Tests assert: two calls give two rows with `seq` 0 and 1 and purposes `interpret`, `qa`; the raw request and response are stored (they purge after 30 days, S6) and the numeric columns are populated; a caller-supplied `x-request-id` reaches the response envelope, the `turns` row and the `proposals` row; a model that never returns JSON leaves three rows and a `model_unavailable` turn, because the journal is written on its own connection and survives the 502.

---

## 4. Decisions recorded in `DECISIONS.md`

`D-MLP-23` … `D-MLP-39`, in the `## App track (MLP consumer)` section under **Session S2**. The ones later sessions need:

- **D-MLP-24** a guardrail refusal is a fourth turn kind on a 200, not a status code — **S3 renders it as a sentence, not an error toast.**
- **D-MLP-26** R1 carries the closing balance per month before and after; R9 carries the delta column SPEC §5-F4 asks the compare view to show — **S4 renders both.**
- **D-MLP-25** verification runs inside the turn, before the card; corrective operations replace the card's operations rather than opening a second card.
- **D-MLP-28** `save` from a turn is reported, never executed — S3's Save button is still the only way to commit.
- **D-MLP-35** the live trials are marked and excluded per commit; **S6 schedules the nightly run.**
- **D-MLP-36** an unreadable model answer ends the turn as a `clarification`, not a 502 — **S3 renders it like any other clarification.**

SPEC amendments: §3's `/turns` row (the fourth kind and the response fields), §2.3 step 4 (where verification runs and why), §14 (two new `[SDK gap]` entries).

---

## 5. What the trial suite found, and what changed because of it

Running the ported trials three times against the live model before committing them found one real defect in the prompt, and a review pass over the diff found four in the code. Both are recorded here because the trials existing is not the same as the trials having been used.

**The prompt: a bare tag name as a selector.** On the third live run, T04 failed. Asked to scale "the revenue", the model tagged its lines `{"cat": "revenue"}` and then wrote `selector: "revenue"` — and the engine refused it, correctly, with `CK-E003`. The first two runs had passed. The response was not to loosen the assertion but to fix the prompt: the selector grammar now states the rule and the rejection together, that a selector is one item id or a `key:value` tag match with exactly one colon, and that a bare tag value is refused. This is the proto's own finding (`TESTLOG`, "what moved the needle", item 2) arriving again: the rule was in the prompt as prose, and it needed to be there as a worked failure.

**Four code defects, all fixed, all now tested** (D-MLP-36 … D-MLP-39): an operation whose `op` was a list raised `TypeError` and 500'd a turn, where every other malformed shape produced a diagnostic; a turn that failed any way other than a provider outage left its journal row at `outcome: "running"` forever, so its model spend never counted against the SPEC §8 daily budget; every loop was bounded but the bounds multiplied, with no single ceiling on a turn; and R1's `delta` reached the engine through `str()`, which walks around the money serializer's refusal of a float. None of them could have moved a book — the ADR-0029 invariant held throughout — but the third was a hole in a cost guardrail and the fourth was a hole in the no-float rule, and both of those are supposed to be absolute.

**The gate is stable.** The ported trials were run five times in total across the session, twice consecutively green before the fix round and twice consecutively green after it. A trial suite that passes once is a coincidence.

---

## 6. Known gaps and deferrals

| Gap | Owner / trigger |
|---|---|
| A read turn can answer straight from the snapshot's results block, leaving `receipts` empty. The prompt now asks for a read operation behind every quoted figure, but that is prompt pressure, not structure | **S3** — the answer card must render an answer with no receipts. Revisit if the accept-rate dashboard shows receipt-free answers are common |
| Split settlement ("half on signing, half after 60 days") has no v0 intent form; M1 carries one term | SDK review / ADR-0019 scoring. SPEC §14 amended |
| No single-call verb for the horizon difference between two scenarios; R9 is per-period, so the model quotes both closing balances | SDK review / ADR-0019 scoring. SPEC §14 amended |
| `POST /import`, the import loop's 20-call cap, `import_jobs` rows | **S5.** `budget.check_import()` exists and is tested; S5 wires it to the endpoint |
| The browser end-to-end (proto T10's real form) | **S3** (Playwright) and S3/S4 (Maestro) |
| `llm_calls` 30-day purge of `request`/`response` | **S6.** The index is in place (`llm_calls_created_idx`) |
| The nightly and pre-release live-trial run | **S6.** The command is in §2 above |
| Latency measurement against SPEC §8, into `BENCHMARKS.md` | **S6** on staging. Observed here on a loopback ASGI client: a read turn is two calls, a plain change turn one, a macro turn two, a repaired turn two |
| Sentry, Grafana Cloud, request-log middleware (SPEC §11's third layer) | **S6.** S2 emits the turn-scoped structured lines through the `cashkit.turn` logger |
| **Connection pool sizing.** A turn holds the request connection for its whole life and opens a second, short one per journal write (D-MLP-29). SQLAlchemy's defaults give 15 connections, so roughly seven turns in flight at once is the ceiling before one waits. Per-book locking serializes a single user, so this only bites across users | **S6** at deploy time: size `pool_size`/`max_overflow` against the VM's Postgres `max_connections`. Not a correctness problem — a waiting turn waits — but it is a latency cliff that SPEC §8's budgets would show |

The untracked files at the repository root that S1 left alone — `QUICKSTART.md`, `budget-scenarios/`, `km/notes/2026-08-21-privacy-compliance.md`, `km/notes/architecture-deck.html`, `km/notes/cashkit-launch-brief.md`, `.vscode/`, `.cursorindexingignore` — are still untracked and still not this session's to commit.

---

## 7. The first thing S3 should verify

**Run both suites before writing anything**, per the session protocol:

```bash
uv sync --all-packages
uv run pytest apps/service/tests apps/service/trials -q     # → 326 passed, 31 deselected
uv run pytest apps/service/trials -m live_model -q          # → 31 passed  (~$0.10, ~6 min)
```

Then verify the one thing the client is built against: **`apps/service/openapi.json` is current and `TurnResponse` is in it.** S3 generates the TypeScript client from that file and must never hand-edit it. `uv run python -m cashkit_service.openapi` regenerates it, and `tests/test_openapi.py` fails on drift.

Four things about the turn contract that will shape the Home screen, and that are easy to get wrong from the schema alone:

- **`kind` has four values.** `refusal` is a §8 guardrail and it carries `retry_after_seconds`. It arrives on a 200 and reads as a sentence; rendering it as an error is the wrong shape (D-MLP-24).
- **A `proposal` turn is stamped `pending` and an answer quoting an R1 hypothetical is stamped `overlay`.** Both are "not from the committed state of base" and both need the ADR-0024 stamp element. A plain answer on a clean base is unstamped.
- **`receipts[]` may be empty on an answer turn.** The reply is still the answer. See §5.
- **`diagnostics[]` on a turn is separate from `proposal.diagnostics`.** The first is what the host refused or deferred — a host operation the model reached for, a `save` it asked for — and the second is what the engine said about the change. Both pass through verbatim (ADR-0015).

And one rule that outlives this session: **`POST /turns` is not a write route.** It creates a proposal and nothing else. If a client ever needs the book to change, it posts to `POST /proposals/{id}`. T13's route inventory is the test that says so, and it fails the moment a new writing route appears without being named.
