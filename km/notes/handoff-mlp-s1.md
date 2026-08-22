# Handoff — MLP session S1 (deterministic service core)

**Date** 2026-08-23 · **Scope** `apps/service/` · **Model calls made: zero.**

S1 built the whole deterministic core of the MLP service: Postgres schema, magic-link auth, book lifecycle, the engine-wrapper read endpoints, per-book locking, and the entire proposal machinery. No prompt text, no model key and no model client exists anywhere in `apps/service/`; a test greps for it.

---

## 1. What was built

### Foundations

| File | What it does |
|---|---|
| `apps/service/pyproject.toml` | `uv` workspace member; its own pytest config, so app tests resolve it and the root config stays untouched |
| `cashkit_service/config.py` | Every environment value in one typed place; nothing reads `os.environ` directly |
| `cashkit_service/clock.py` | `Clock` protocol, `SystemClock`, `FixedClock`. The one wall-clock reader (D-MLP-12) |
| `cashkit_service/money.py` | **The** money serializer: `{exact, display}` (D-MLP-06). Raises on a float; raises rather than rounding past 4dp |
| `cashkit_service/envelope.py` | Provenance envelope and the SPEC §2.4 WHAT-IF rule, quoted verbatim |
| `cashkit_service/middleware.py` | Request-id correlation; test-mode response-invariant check |
| `cashkit_service/errors.py` | Service failures as status codes. Diagnostics are never HTTP errors |
| `cashkit_service/db.py`, `migrate.py`, `migrations/0001_init.sql` | SPEC §4 schema plus `login_tokens`; plain SQL migrations (D-MLP-09) |
| `cashkit_service/books.py` | `BookRuntime`: one asyncio lock and one kit per book, thread-confinement guard, overlay fingerprint, `scratch_copy` |
| `cashkit_service/serialize.py` | SDK objects to payloads. Money through `money()` only; diagnostics verbatim |
| `cashkit_service/reads.py` | Scenario resolution and envelope construction, in one place |

### Endpoints (21 paths, all `async def`)

`POST /auth/link` · `POST /auth/verify` · `GET /me` · `DELETE /me` · `GET /me/export` · `POST /books` · `GET /book/state` · `GET /book/forecast` · `GET /book/trace` · `GET /book/why_zero` · `GET /book/events` · `GET /book/reconcile` · `GET /book/validate` · `GET /book/history` · `GET /book/scenarios` · `POST /book/scenarios` · `POST /book/scenarios/{id}/activate` · `GET /book/compare` · `POST /book/edits` · `POST /proposals/{id}` · `POST /book/save` · `POST /book/discard` · `GET /export`

### Proposal machinery

| File | What it does |
|---|---|
| `ops/schema.py` | The grammar: 9 mutation intents + 5 host ops as one typed union, plus the 12 read-intent names. Money is always a string; `status` is not a slot; `as_of` is not a slot |
| `ops/applier.py` | One op onto one kit. Every failure a Diagnostic. Proto normalizations. The record-actual discriminator |
| `ops/dryrun.py` | Runs ops on a throwaway copy; deltas + crossing flags |
| `proposals.py` | Storage, the staleness `Stamp`, supersession, expiry |
| `intents/read.py` | The twelve read intents, executed deterministically. R5/R6 host-composed |

---

## 2. Exact commands to reproduce every gate

Run from the repository root.

```bash
# Setup, once. The test fixture starts the container itself if it is not up,
# so the compose line is optional.
uv sync --all-packages
docker compose -f apps/service/docker-compose.dev.yml up -d --wait

# The whole S1 suite: integration tests + trials.
uv run pytest apps/service/tests apps/service/trials -q      # → 194 passed

# The engine suite is untouched and still the default.
uv run pytest --collect-only -q | tail -3                    # → only tests/…
```

Per gate:

| Gate | Command | Expected |
|---|---|---|
| G1 workspace, schema, auth, `/me` | `uv run pytest apps/service/tests/test_auth.py apps/service/tests/test_me.py apps/service/tests/test_workspace.py -q` | 18 passed |
| G2 book lifecycle, lock, confinement | `uv run pytest apps/service/tests/test_books.py apps/service/tests/test_concurrency.py -q` | 13 passed |
| G3 reads + SDK parity | `uv run pytest apps/service/tests/test_sdk_parity.py apps/service/tests/test_state.py apps/service/tests/test_response_invariants.py apps/service/tests/test_export.py -q` | 48 passed |
| G4 proposals, host ops, discriminator | `uv run pytest apps/service/tests/test_proposals.py apps/service/tests/test_staleness.py apps/service/tests/test_host_ops.py -q` | 33 passed |
| G5 trials + OpenAPI | `uv run pytest apps/service/trials apps/service/tests/test_openapi.py -q` | 50 passed |

Regenerate the schema (the drift test compares against the committed copy):

```bash
uv run python -m cashkit_service.openapi     # → wrote apps/service/openapi.json
```

Run the service by hand:

```bash
export CASHKIT_DATABASE_URL=postgresql+asyncpg://cashkit:cashkit@localhost:55432/cashkit
export CASHKIT_BOOKS_ROOT=/tmp/cashkit-books
uv run python -m cashkit_service.migrate
uv run uvicorn cashkit_service.app:app --port 8000
```

---

## 3. What each gate proves, against the PROMPT's wording

> **Integration tests for every endpoint**

All 21 paths have integration tests. `tests/test_openapi.py` asserts the published schema covers every SPEC §3 endpoint S1 owns, and that `/turns` and `/import` are absent — S2 and S5 own those.

> **A concurrency test proving thread confinement (no sqlite cross-thread error under parallel requests)**

`tests/test_concurrency.py`. 24 parallel `GET /book/state` requests all return 200 and agree on revision and closing balance; 8 reads interleaved with 8 proposal writes all succeed. A test walks every route (through nested routers — a flat scan finds nothing in this FastAPI version and would pass vacuously) and asserts each handler is `async def`, since a `def` handler is threadpooled and breaks confinement. Reaching a kit from another thread raises `ThreadConfinementError` by name rather than surfacing as a SQLite error deep inside a query.

> **Every money figure and diagnostic in an endpoint payload string-equal to the canonically serialized value from the direct SDK call on the same book/revision/as_of (envelope fields excluded)**

`tests/test_sdk_parity.py`. Each test opens its **own** kit on the same book directory and asks the SDK itself, then compares string-for-string against the endpoint payload. Nothing re-reads a service-computed number and compares it to itself. Covered: state (summary, closing series, per-item cash and accrual columns, opening balance), warnings, forecast rows, trace (value, every binding, every step), why_zero, events, reconcile (every line and total), validate, history, compare. Diagnostics are compared field by field — code, severity, message, suggested_fix, item_id, field — so a paraphrase fails the suite.

> **T13 (no unproposed/stale mutation — bypass and staleness attempts fail)**

`trials/t13_no_unproposed_mutation.py`, 14 tests. An inventory of every mutating route; a grep for a debug or admin shortcut in production code; unconfirmed, discarded and expired cards; another account's proposal; a card whose dry-run had errors; and all four §2.5 staleness paths — accept after save, after discard, after activation, after another accept. Each supersedes; none applies blind. A card whose fingerprint no longer matches comes back `refreshed` and still needs confirming.

> **T17 (correction scar)**

`trials/t17_correction_scar.py`, 8 tests. Original retrievable through `include_voided`, note mandatory and verbatim, correction links back, only the correction counts in the numbers and in `reconcile`, and a correction can itself be corrected without collapsing the chain.

> **T18 (record-actual discriminator)**

`trials/t18_record_actual_discriminator.py`, 22 tests. The rule as a ten-case table over the function, then again through `POST /book/edits`. Future-dated stays forecast; off-flow always forecast; missing date returns `kind: clarification` and stores nothing. The grammar has no `status` slot.

> **green with zero model calls**

Nothing in `apps/service/` imports or names a model client; `tests/test_workspace.py::test_the_service_never_imports_a_model_client` greps for it.

> **OpenAPI schema published**

`apps/service/openapi.json`, 21 paths, committed. `tests/test_openapi.py` fails on drift. It builds without a database, so publishing the contract does not need infrastructure up.

---

## 4. Decisions recorded in `DECISIONS.md`

`D-MLP-06` … `D-MLP-22`, in the `## App track (MLP consumer)` section under **Session S1**. The load-bearing ones for later sessions:

- **D-MLP-06** money is `{exact, display}` — S3 renders `display`, and `exact` where full precision belongs (the trace engine panel).
- **D-MLP-12** the clock is a dependency — S2's trials must install `FixedClock` too.
- **D-MLP-15/16** dry-run copies the book; accept rehearses before applying.
- **D-MLP-17** the fingerprint is configuration digest + ledger row digest.
- **D-MLP-20** read intents are built and tested; S2 wires them to the model.

**SPEC amendments** made this session are listed at the end of the same section: §3 gained `GET /book/validate` and `GET /book/history`, the money sentence, and the scenarios note; §4 gained `login_tokens` and wider `proposals`/`sessions` column lists.

---

## 5. Known gaps and deferrals

| Gap | Owner / trigger |
|---|---|
| `POST /turns`, the agent layer, prompts, `llm_calls` rows | **S2.** The tables exist and are empty |
| `POST /import`, `GET /imports/{id}/stream`, `import_jobs` rows | **S5.** The table exists and is empty |
| The generated TS client and `packages/api-types` | **S3**, from `apps/service/openapi.json` |
| Mail delivery is `ConsoleMailer` | **S6.** `Mailer` is a protocol; the provider key is SPEC §12 |
| The magic-link URL host is hard-coded in `routers/auth.py` | **S3/S6**, when the deep-link scheme and domain are fixed |
| `users.deleted_at` unused; backup purge | **S6** (D-MLP-22, SPEC §9 30-day window) |
| `llm_calls` 30-day purge job | **S6** |
| Rate limits and the per-user daily model budget (SPEC §8) | **S2** — they gate model spend, so they belong with the model layer |
| Latency measurement against SPEC §8, into `BENCHMARKS.md` | **S6** on staging |
| Postgres revision store | Deferred with trigger (D-MLP-01, SPEC §2.2) |
| `GET /book/forecast`'s `grain` parameter is accepted and echoed but does not re-bucket | **S4**, if the Forecast screen ever offers a grain other than the book's. The MLP forecast is the designed monthly view (SPEC §5-F3) |

Pre-existing untracked files at the repository root — `QUICKSTART.md`, `budget-scenarios/`, `km/notes/2026-08-21-privacy-compliance.md`, `km/notes/architecture-deck.html`, `km/notes/cashkit-launch-brief.md`, `.vscode/`, `.cursorindexingignore` — were untracked before S1 started and were left alone. They are not S1's to commit.

---

## 6. The first thing S2 should verify

**Run the suite before writing anything**, per the session protocol:

```bash
uv sync --all-packages
uv run pytest apps/service/tests apps/service/trials -q     # → 194 passed
```

Then verify the one thing S2 builds directly on top of: **`POST /turns` must reach the book only through `proposals.create()`**, exactly as `POST /book/edits` does. Read `cashkit_service/routers/book_edits.py::create_edit_proposal` first — it is 25 lines, and the turn path differs from it only in where the operations come from. In particular:

- pass the turn's `context` straight through; the record-actual discriminator is already implemented and tested, and S2 must not re-implement it (`ops/applier.py::discriminate_event_status`);
- a clarification returns `kind: "clarification"` and stores **no** proposal — T18 asserts the row count stays zero;
- read intents already execute (`intents/read.py`); the Q&A loop quotes their output rather than computing anything;
- the model must never hold the book lock. `BookRuntime.acquire()` is a context manager — do the model call outside it.

Re-run T13 after wiring turns, as the S2 gate requires: it must still pass unchanged with turn-originated proposals in play.
