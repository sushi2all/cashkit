# Handoff — MLP session S5 (import, export, onboarding)

**Date** 2026-08-23 · **Scope** `apps/service/cashkit_service/imports/`, `POST /import`, `apps/client/` screens 13–14 · **Model** `google/gemini-3.7-flash` (pinned, ADR-0028)

S5 is the one session that spans both surfaces, because the import loop is a single feature with a streaming contract in the middle. It built SPEC §7's agentic import loop and its two endpoints, the reconciliation report, SPEC §6 screen 14 (import and export) and screen 13 (the first-book wizard), and T16 on both sides of the wire.

Everything S1–S4 built still passes unchanged. Three service files gained additions (`agent/prompts.py`, `config.py`, `app.py`); one S3 file gained a function (`src/state/edits.ts::adopt`); one S3 file gained a routing rule (`app/_layout.tsx`). Nothing under `cashkit/` or `tests/` was touched.

---

## 1. What was built

### The import pipeline (`apps/service/cashkit_service/imports/`)

| File | What it does |
|---|---|
| `sheets.py` | Reads a workbook: values **and** formulas, per cell, as `proto/server.py:sheet_text` does. The one float→Decimal boundary in the whole import path. Finds the sheet's own subtotal, total and balance cells |
| `checks.py` | The reconciliation. `CheckSpec` is a cell reference plus a *meaning* and has **no slot for a figure**; `Figures` is the engine's side; `Figures.added()` is the fork basis; the 1-cent parity label |
| `loop.py` | The loop itself: the target rule, the guard-and-stamp, the section walk, the revise rounds, the drop of what the engine still refuses, and the one proposal |
| `jobs.py` | The in-process job registry and the SSE fan-out, with replay for a late or reconnecting listener |
| `runner.py` | One import as a background task, on its own database connection, and the two payload shapes (`ImportStarted`, `ImportDone`) |
| `routers/imports.py` | `POST /import` · `GET /imports/{id}/stream` · `GET /imports/{id}` |
| `agent/prompts.py` | Three new prompts: plan, author-a-section, revise-on-mismatch. No host operation and no raw SDK verb appears in any of them; `tests/test_prompt_surface.py` renders all three and checks |

### The client (`apps/client/`)

| Path | What it is |
|---|---|
| `src/screens/ImportExportScreen.tsx` | S14 — the dropzone, the live progress, the reconciliation report per sheet row, the card, and the export controls |
| `src/screens/OnboardingScreen.tsx` | S13 — the three-step first-book wizard |
| `src/state/importJob.ts` | The SSE reader, with a polling fallback for a platform with no streaming `fetch` |
| `src/importing/filePicker.{ts,native.ts}` | The file-pick adapter. Web mounts a real hidden `<input type="file">`; native says import is in the web app (SPEC §6-S14) |
| `src/exporting/download.{ts,native.ts}` | Web download; mobile share sheet (**written, never run** — §5) |
| `app/import.tsx` · `app/onboarding.tsx` | The routes. `app/_layout.tsx` sends a signed-in account with no book to the wizard |
| `e2e/web/import.spec.ts` · `e2e/web/onboarding.spec.ts` | The browser half of T16 and of the onboarding gate clause |

### Tests and trials

`apps/service/tests/test_imports.py` (31, scripted) · `apps/service/tests/workbooks.py` (the T06/T07 workbook builders) · `apps/service/trials/t16_import_round_trip.py` (6, live) · `apps/client/e2e/web/import.spec.ts` (6) · `apps/client/e2e/web/onboarding.spec.ts` (5) · `money.spec.ts` extended to both new screens.

---

## 2. Exact commands to reproduce every gate

Run from the repository root.

```bash
# Setup, once. The test fixture starts the container itself if it is not up.
uv sync --all-packages
npm ci
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
(cd apps/client && npx playwright install chromium)

# The service suites. Per-commit run; the live trials are excluded by marker.
uv run pytest apps/service/tests apps/service/trials -q     # → 361 passed, 37 deselected

# The model-behaviour gate, T01–T12 and T16. Needs OPENROUTER_API_KEY
# (the repo-root .env is read). ~$0.31, ~12 minutes.
uv run pytest apps/service/trials -m live_model -q           # → 37 passed  (12m20s measured)

# T16 alone, which is this session's gate. ~$0.15, ~5 minutes.
uv run pytest apps/service/trials/t16_import_round_trip.py -m live_model -q   # → 6 passed

# Drift, types, lint-rule suites, ESLint, unit tests.
npm run verify                                               # → exit 0 (24 unit tests)

# The browser gates. Playwright starts the harness; no model key is needed.
(cd apps/client && npm run e2e:web)                          # → 48 passed

# The engine suite is untouched and still the default.
uv run pytest --collect-only -q | tail -3                    # → only tests/…
```

Per gate clause:

| Gate clause | Command | Expected |
|---|---|---|
| T16 round-trips the T06/T07 workbooks through the API | `uv run pytest apps/service/trials/t16_import_round_trip.py -m live_model -q` | 6 passed |
| …and through the UI, with the report correct | `(cd apps/client && npx playwright test -c e2e/playwright.config.ts e2e/web/import.spec.ts)` | 6 passed |
| The non-empty-book fork rule | `uv run pytest apps/service/tests/test_imports.py -q -k "fork or plan or already"` + the live `test_an_import_into_a_book_that_has_a_plan_never_touches_base` | included above |
| Onboarding produces an applied proposal, never a silent book | `(cd apps/client && npx playwright test -c e2e/playwright.config.ts e2e/web/onboarding.spec.ts)` | 5 passed |
| Import call-cap honored | `uv run pytest apps/service/tests/test_imports.py -q -k cap` and `import.spec.ts`'s cap test | included above |
| Nothing else moved | `uv run pytest apps/service/tests apps/service/trials -q` | 361 passed |

Regenerate the schema and the client types when the service changes:

```bash
uv run python -m cashkit_service.openapi          # → wrote apps/service/openapi.json
npm run generate --workspace @cashkit/api-types   # → wrote packages/api-types/src/generated/schema.ts
npm run api:check-drift                           # → drift check OK
```

Poke it by hand:

```bash
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run python apps/client/e2e/harness/server.py --port 8099
cd apps/client && npm run export:web && open http://127.0.0.1:8099/import
```

---

## 3. What each gate proves, against the PROMPT's wording

> **T16 import round-trips (T06/T07 workbooks) through API and UI with reconciliation report correct**

`trials/t16_import_round_trip.py`, six tests on the pinned model, **run twice consecutively green** before committing — a trial suite that passes once is a coincidence (S2's rule).

**T06** authors a book deterministically through `POST /book/edits` (no model), exports it with `GET /export`, and imports that workbook into a **second account's empty book** whose opening balance is deliberately wrong. All twelve closing balances come back, which means the import recovered the opening balance from the export's own `Opening balance | meta` row. Proto T06's finding was that the round trip is format-limited before it is model-limited, so this is as much a test of the export as of the import.

**T07** imports the messy family budget — month-name headers, POSITIVE expenses, section and SUM rows, a starting-balance corner cell, a 13th-month salary, bimonthly utilities, one annual premium, a mid-year price rise — into an empty book and asserts **the closing balance of every month** against figures computed in the fixture from the sheet's own rows. Not the items: a December bonus is equally correct as a one-off or as a windowed line, and a trial that insisted on one would fail a right answer (S2's T07 lesson). The month-by-month balance is wrong the moment any construct is.

The workbook is generated, not committed, and the generator injects the cached formula values Excel stores (`<f>` **and** `<v>`), because openpyxl writes only the formula — without that the total rows, which are the entire point of a reconciliation, would have had no values to check against (D-MLP-86).

The **report** is asserted structurally as well: every row names its own cell, a row that is not skipped carries both figures and the delta, a parity row is always `mismatched` and always within the stated tolerance, and the three counts add up to the number of rows.

Through the UI: `e2e/web/import.spec.ts` uploads through the real `<input type="file">` and asserts the report on the screen — the summary line, the target line, the call count, and per row the status, the sheet figure, the engine figure and the delta. It also asserts the progress was rendered *as it happened* (`Reading budget.xlsx`, `Section 1 of 1`), not only at the end, which is what the harness change in D-MLP-95 makes possible.

> **including the non-empty-book fork rule**

Tested at three levels, because SPEC §7.3 is a data-safety rule and a single happy-path test would not be evidence.

- **The rule itself**, scripted: an empty book takes the import into base; a book with a plan gets a fork named from the file; the name never collides; the answer to `POST /import` already says which, before the first model call.
- **The rule under a model that argues**: a scripted provider that emits `"scenario": "base"` on a fork import does not get base — the host stamps the target after the guard.
- **The rule with a live model authoring into it**: `test_an_import_into_a_book_that_has_a_plan_never_touches_base` seeds a real book, imports the T07 workbook, applies the card, and asserts base's closing series and item ids are **identical**, that the fork exists and holds both the book's own lines and the imported ones, that the two scenarios genuinely differ, and that the ledger is still empty.

Three further consequences of the rule are enforced and tested, and all three were found by building it rather than by reading the SPEC (§4 below): no events on a fork, no book-level settings on a fork, and no imported line taking over an existing one.

> **onboarding produces an applied proposal, never a silent book**

`e2e/web/onboarding.spec.ts`, five tests. Step (a) creates the book and the test asserts through the **API** that it is empty — a screen showing nothing and a book holding nothing look the same, and only one of them is ADR-0029. Step (b) is an ordinary turn producing an ordinary card, and while that card is on the screen the book is still empty and `POST /proposals/{id}` has been called **zero** times, counted at the network. Step (c) applies it, the count becomes one, and the items appear. Also covered: a signed-in account with no book lands on the wizard; the skip path leaves an empty book and reaches Home; a discarded card leaves the book empty and the wizard usable; a clarification asks and stores nothing.

> **import call-cap honored**

`settings.import_max_llm_calls = 20`. The cap travels on a copy of the settings into `ask_json`, so the import reuses S2's tested call path and every retry counts (D-MLP-84). Two tests drive it: a scripted plan with ten sections and a provider that never reconciles reaches exactly 20 calls and stops, and the report says `capped: true`, `llm_calls: 20`, `partial: true` and carries the reason in the user's words. The browser test asserts `20 OF 20 ASSISTANT CALLS · CAP REACHED` on the screen. **The partial result is still a card**, and the book is still empty — a truthful partial beats a completed-looking guess. Every live T16 test also asserts `llm_calls <= 20`.

### Sabotage, not assertion

Every claim above that could be satisfied by an inert test was checked by breaking it:

- making `useEditProposal.adopt` accept the card it was handed fails **three** of the six import specs, including the one that counts confirmations at the network;
- removing the `sheet_value`/`delta` allow-list from `money.spec.ts` fails it, which proves the sheet figures really are money-shaped tokens on the screen and really are being checked;
- the first version of the fork test failed for a reason that turned out to be a real defect, not a test bug (§4).

---

## 4. What building it found

Three things the SPEC does not say, all found by writing the code and all now enforced structurally. They are in `DECISIONS.md` as D-MLP-74/75/76 and SPEC §7.3 was amended with each.

1. **An import into a fork cannot author an event.** The ledger is append-only and shared by every scenario, so an `add_event` "into a fork" is visible from base — the silent merge §7.3 forbids. On a fork a one-off is a line whose start and end are one month apart. The prompt says so *and* the host refuses the operation, because a prompt rule alone was demonstrably not enough once already.
2. **An import into a fork cannot move the horizon or the opening balance.** Both are book-level. The book keeps its own, and a sheet row outside the horizon is reported `skipped` with the reason. Widening base so a sheet fits is exactly the destruction the rule prevents.
3. **An imported line must not take over an existing one.** `add_item` on an existing id *replaces* that line, and on a fork that happens where the user cannot see it. Colliding ids are renamed to `<id>_imported` with an `info` diagnostic naming both, and `set_amount` is accepted only against a line this import authored.

And one that nearly cost the gate: **on a fork, an absolute reconciliation is meaningless.** The fork carries the book's own plan as well as the sheet's, so comparing its January total against the sheet's compares two different things — a mismatch that is not the import's fault, that the loop would burn its twenty calls chasing, and that it could only "fix" by authoring wrong lines to compensate. The comparison is now what the import *added* (fork minus base), every row names its basis, and a running-balance row is reported `skipped` with the reason rather than given a comparison that would not hold. Adding the sheet's opening balance back in to make the numbers line up was considered and rejected: that is a fudge that silently absorbs a real error.

---

## 5. Decisions recorded in `DECISIONS.md`

`D-MLP-73` … `D-MLP-97`, under **Session S5**, in two gate groups. The ones S6 needs:

- **D-MLP-77** `GET /imports/{id}` was added beside the stream; **SPEC §3 amended**.
- **D-MLP-83** the job registry is in-process and there is no job recovery. A job whose process is gone is gone, which is safe rather than lossy — an unfinished import applied nothing. **S6 should know this before it configures a restart policy or a second node.**
- **D-MLP-84** an import opens one `turns` row and its `llm_calls` rows hang off it, so import spend counts against the SPEC §8 daily budget and the §11 chain is unbroken. **S6's spend alarm therefore already covers imports.**
- **D-MLP-94** `expo-file-system` and `expo-sharing` were added and the native export uses `expo-file-system/legacy`. **S6 should move to the SDK 57 API when it first builds this for real**, and must run the share path on a device (D-MLP-48's class).
- **D-MLP-95** the E2E harness forwards SSE frame by frame. **The deployment must not buffer `/imports/*/stream`** — Caddy passes SSE through by default, but a `reverse_proxy` with buffering configured would break the screen without failing any test.

**SPEC amendments**: §3's import row (the three endpoints and what each does), §7.3 (the three fork consequences and the reconciliation basis), §7.5 (the flat tolerance and the label-not-match rule) and a new §7 item 6 (the model never supplies a check figure).

**No SPEC/ADR conflicts were found.**

---

## 6. Known gaps and deferrals

| Gap | Owner / trigger |
|---|---|
| **Mobile export has never run** — no simulator on this machine | **S6**, on a device, with dictation (D-MLP-48, D-MLP-94) |
| **`expo-file-system/legacy`** rather than the SDK 57 API | **S6** at first EAS build (D-MLP-94) |
| Import on mobile points at the web app | SPEC §6-S14's own decision. Post-MLP if the beta asks |
| **The polling fallback in `importJob.ts` has never been exercised** — the browser always has a streaming body | **S6**, if a native import is ever built. It is 15 lines and it is typechecked |
| **Latency**: an import of the T07 workbook takes 30–50 s end to end on a loopback client against the live model, inside SPEC §8's 90 s budget. Not measured on staging | **S6**, into `BENCHMARKS.md` |
| Import into a fork cannot express a one-off as a ledger event | Structural (D-MLP-74). Revisit only if ADR-0007 gains scenario-scoped events (D-MLP-55) |
| No drag-and-drop on the dropzone — the button opens the picker | Post-MLP polish. The element inventory in SPEC §6-S14 says "dropzone/file pick" |
| **Maestro flow still never executed**, and does not cover S4's or S5's screens | Unchanged from S3/S4. **S6** |
| `llm_calls` 30-day purge; nightly live-trial run; Sentry, Grafana, request logs | **S6**, unchanged |
| httpOnly cookie for the web session | **S6** (D-MLP-44) |

The untracked repository-root files S1–S4 left alone — `QUICKSTART.md`, `budget-scenarios/`, `km/notes/2026-08-21-privacy-compliance.md`, `km/notes/architecture-deck.html`, `km/notes/cashkit-launch-brief.md`, `.vscode/`, `.cursorindexingignore` — are still untracked and still not this session's to commit.

---

## 7. Measured model cost

Measured against OpenRouter's own key-usage figure, before and after each run.

| Run | Wall clock | Cost |
|---|---|---|
| T16 alone — six tests, ten imports | ~5 min | **$0.1460** (measured twice, identical) |
| The whole live suite, T01–T12 + T16 (37 tests) | 12 min 20 s | **$0.3138** |

T16 was run **three times green in total**: twice on its own before committing gate 1, and once more inside the full suite at the end of the session.

An import is the expensive turn in this product by an order of magnitude: a T07 import spends 4–8 model calls where an ordinary turn spends one to three. SPEC §8's $0.50/day budget covers roughly two or three imports plus a day of ordinary use, which is why the five-per-day import limit and the daily budget are both checked before `POST /import` starts anything (D-MLP-81).

---

## 8. The first thing S6 should verify

**Run everything before writing anything**, per the session protocol:

```bash
uv sync --all-packages && npm ci
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run pytest apps/service/tests apps/service/trials -q     # → 361 passed, 37 deselected
uv run pytest apps/service/trials -m live_model -q          # → 37 passed  (~$0.31, ~12 min)
npm run verify                                              # → exit 0
(cd apps/client && npm run e2e:web)                         # → 48 passed
```

Then verify the one thing S6's deployment can silently break: **`GET /imports/{id}/stream` must reach the browser unbuffered.** Everything else in this product is a request and a response, and a proxy that buffers one changes nothing the user notices. Buffering this one turns the import screen from "watch it happen" into "wait ninety seconds, then see all of it at once", and **no test in the repository will fail** — the E2E harness forwards frames because S5 made it (D-MLP-95), and the service is correct either way. Caddy passes SSE through by default; check it in the staging deploy with `curl -N` and watch the frames arrive one at a time, rather than assuming.

Three more things S6 inherits directly:

- **The job registry is in-process and single-node** (D-MLP-83). A restart mid-import loses the job, which is safe — nothing was applied — but the user sees the stream end. If S6 adds a second node or an aggressive restart policy, an import started on one node cannot be streamed from the other. The SPEC §2.2 trigger for the Postgres revision store (>2 000 books or a second node) is the same trigger for this.
- **Import spend is on the same meters as everything else** (D-MLP-84), so the §11 spend alarm needs no import-specific rule. What it does need is a threshold that expects an import to be 4–8 calls, or a user importing twice will look like a runaway loop.
- **`packages/api-types` regenerates from `apps/service/openapi.json`** and the drift check is in `npm run verify`. The import types are generated like everything else; `packages/api-types/src/index.ts` holds only aliases.
