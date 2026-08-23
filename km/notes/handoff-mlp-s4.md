# Handoff — MLP session S4 (the remaining screens)

**Date** 2026-08-23 · **Scope** `apps/client/`, `packages/api-types/src/index.ts` · **Model calls made: zero** (the browser gate scripts the provider) · **`apps/service/` unchanged.**

S4 built SPEC §6 screens 6–11 and 15: Scenarios and compare, Actuals with corrections and cutover, Plan vs actual, the three Item variants, and Settings + History. It also closed a latent WHAT-IF defect the Scenarios screen would have exposed, and gave R10 a gate that compares the screen against the live endpoint.

Nothing under `apps/service/` was touched. Six API gaps were found and every one is recorded as an escalation with an owner rather than patched (§5).

---

## 1. What was built

### Screens and routes

| Path | What it is |
|---|---|
| `src/screens/ScenariosScreen.tsx` | S6 — chips, fork-as-a-card, activation, the compare chart and table with per-column WHAT-IF stamps |
| `src/screens/ActualsScreen.tsx` | S7 — the ledger with the correction scar, record-actual, cutover, and the R10 diagnostics surface |
| `src/screens/PlanVsActualScreen.tsx` | S8 — reconciliation with percent-of-plan bars, the plan tick, and the empty track |
| `src/screens/ItemScreen.tsx` | S9 + S11 — the rule and segments read out of the engine's traces, the occurrence list, M2 and `edit_schedule_date` |
| `src/screens/EventScreen.tsx` | S10 — one-off events, status, remove-on-forecast, refused-on-actual |
| `src/screens/SettingsScreen.tsx` | S15 — account, book settings as proposals, privacy, about, and the R12 revision list |
| `app/scenarios.tsx` · `app/actuals.tsx` · `app/plan.tsx` · `app/item.tsx` · `app/event.tsx` · `app/settings.tsx` | The routes. Home is the hub and links to all of them |

### Components and state

| Path | What it does |
|---|---|
| `src/state/edits.ts` | `useEditProposal()` — the UI-origin write path. Propose, render the card, confirm, re-present a refreshed card. No optimistic path anywhere |
| `src/api/diagnostics.ts` | Narrows the four payloads that declare `diagnostics` untyped, verbatim, with a compile-time exhaustiveness guard (D-MLP-56) |
| `src/screens/itemRule.ts` | Assembles an item's rule and segments from the engine's own trace statements (D-MLP-66). Unit-tested |
| `src/screens/components/CompareChart.tsx` | Two curves on one shared scale, the zero line, the diverge dot |
| `src/screens/components/LedgerRows.tsx` | The ledger rows and the correction scar — original struck, correction linked both ways |
| `src/screens/components/CorrectionForm.tsx` | M6. Mandatory note, and a shape check on the amount (D-MLP-64) |
| `src/screens/components/PlanBar.tsx` | Percent-of-plan with a 100% tick; no fill at all when unsettled |
| `src/screens/components/MicButton.tsx` | The mic-only input SPEC §6 gives Scenarios |
| `src/money/plot.ts` | Gained `scaleTogether`, `percentOfPlan` and `bandBelow`. The `allowIn` list is unchanged (D-MLP-59) |

### Two changes to files S3 owned

- `src/state/book.tsx` now reads `GET /book/state?scenario=base` explicitly and exposes `activeScenario` separately. **This was a live defect** — see §3.
- `src/screens/TraceScreen.tsx` renders the WHAT-IF stamp and links a traced row to its Item screen (the ADR-0013 taxonomy's other half).

---

## 2. Exact commands to reproduce every gate

Run from the repository root.

```bash
# Setup, once.
uv sync --all-packages
npm ci
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
(cd apps/client && npx playwright install chromium)

# The service suites. S4 changed nothing here; the number must not move.
uv run pytest apps/service/tests apps/service/trials -q     # → 330 passed, 31 deselected

# Drift, types, lint-rule suites, ESLint, unit tests.
npm run verify                                              # → exit 0 (24 unit tests)

# The browser gates. Playwright starts the harness; no model key is needed.
npm run e2e:web                                             # → 36 passed

# The engine suite is untouched and still the default.
uv run pytest --collect-only -q | tail -3                   # → only tests/…
```

Per file, from `apps/client`, after `npm run export:web`:

```bash
npx playwright test -c e2e/playwright.config.ts e2e/web/<file>
```

| Gate clause | File | Expected |
|---|---|---|
| E2E for the F4 path | `e2e/web/scenarios.spec.ts` | 4 passed |
| T15 — `what_if` present **and** rendered | `e2e/web/whatif.spec.ts` | 5 passed |
| E2E for the F5 path, correction scar included | `e2e/web/actuals.spec.ts` | 4 passed |
| R10 verbatim against `validate()` | `e2e/web/diagnostics.spec.ts` | 3 passed |
| The bars, the tick, the empty track | `e2e/web/plan.spec.ts` | 3 passed |
| Items and Settings | `e2e/web/items.spec.ts` | 6 passed |
| No invented figure on any new screen | `e2e/web/money.spec.ts` | 3 passed |
| S3's, unchanged | `gate` 1, `guards` 3, `turns` 4 | 8 passed |

Two traps in that command, both of which cost this session time. **`--grep` filters on the test title, not the file name**, so `--grep "actuals.spec"` silently runs a subset — pass the file path instead. And **`e2e:web:only` does not rebuild the web bundle**, so a client change since the last `npm run e2e:web` is tested against the previous build; run `npm run export:web` first.

---

## 3. What each gate proves, against the PROMPT's wording

> **E2E for F4 and F5 paths**

**F4** — `e2e/web/scenarios.spec.ts`. Naming a fork produces a card and the book still has one scenario until Apply is pressed (ADR-0029, D-MLP-14); the compare table shows both columns per period plus the service's own delta, asserted string-equal to `GET /book/compare`'s payload; the diverge label lands on the month the fork parts company; activation switches `books.active_scenario`; and every cell the payload has no figure for renders a dash rather than a zero, checked cell by cell against the payload (SPEC §5-F4's absent-is-not-zero).

**F5** — `e2e/web/actuals.spec.ts`. Recording an actual through the screen's ask bar sends `context: "actuals_record"`, and the same words from Home send no context — asserted on the request bodies, because that flag is the client's entire share of SPEC §5-F5. A missing date comes back `kind: clarification` and the ledger stays empty. The cutover is offered as an M8 card and the book's cutover does not move until Apply.

> **T15 (`what_if` field per SPEC §2.4, present and rendered)**

`e2e/web/whatif.spec.ts`, five tests, both halves every time. On the wire: base committed is unstamped, a non-base scenario is `{stamped: true, reason: "scenario", scenario: "car"}`, and a proposal turn is `reason: "pending"`. On the screen: the fork's compare column carries the stamp and names the fork while base's carries none; the Trace screen stamps a fork's trace and not base's; a throwaway-overlay answer is stamped on its card; and the Home header keeps showing the base figure, unstamped, while the fork is active — with the fork proven to be numerically different, so the header agreeing is a decision and not a coincidence.

> **correction flow E2E shows the scar (original visible, note mandatory)**

`e2e/web/actuals.spec.ts`. The note is mandatory in the interface as well as in the schema: an amount alone leaves Apply disabled and the screen says why. After the correction applies, the original row is still on the screen with its own amount, `line-through` asserted from the computed style, carrying `CORRECTED · SEE <id>`; the correction row carries `↳ corrected <date> · was <amount> · note: <note>` with the original figure and the note in full. The API is checked too: the correction is a new row with `corrects` set, not an edit.

> **R10 diagnostics render verbatim (string-equality test against `validate()` output)**

`e2e/web/diagnostics.spec.ts`. The fixture records an actual dated after the cutover, which is a real `CK-W003` with a real message and a real suggested fix — the test asserts the endpoint returned something before it asserts anything about the screen. Then every field of every diagnostic `GET /book/validate` returned must appear in the rendered card: code, severity, the whole message, the whole suggested fix, the item id and the field. The count of rendered entries must equal the count the endpoint sent, so a filtered diagnostic fails as loudly as a rewritten one. A second test asserts a clean book is reported as silence and that the words "healthy", "looks good", "score", "you should" and "we recommend" appear nowhere (ADR-0015).

> **the standing-warnings banner on Home (server-computed, from the state payload) and crossing flags in every proposal card**

Both elements existed from S3 (`WarningsBanner`, `ProposalCard`'s `Crossings`) and S4's work was to use them everywhere rather than to add anything: every card on every new screen is `ProposalCard`, so the crossings block and the full deltas block come with it. There is no threshold surface anywhere, and `items.spec.ts` asserts the words are absent from Settings so one cannot appear by accident (D-MLP-70).

### Sabotage, not assertion

Every gate claim above was checked by breaking it. Removing `?scenario=base`, removing the compare column stamp, truncating a diagnostic message, hiding the superseded ledger row, drawing an unsettled row as a zero-length bar, offering Remove on an actual, and unlocking deletion without the phrase — each fails the suite. And making `useEditProposal.propose()` accept the proposal it just received fails **six** specs across four screens, which is the coverage "no optimistic apply" deserves.

One sabotage did **not** fail at first: an optimistic apply raced a DOM assertion and won. The item test now counts `POST /proposals/{id}` at the network — zero before Apply, one after (D-MLP-72).

---

## 4. Decisions recorded in `DECISIONS.md`

`D-MLP-53` … `D-MLP-72`, under **Session S4**, in three gate groups. The ones later sessions need:

- **D-MLP-53** the Home header is pinned to base by a query parameter, and `useBook()` exposes `activeScenario` for naming the context. **Do not remove the parameter.**
- **D-MLP-55** an event is book-level; only an item change diverges two scenarios. Any fixture that needs two scenarios to differ must change an item.
- **D-MLP-56** four payloads type `diagnostics` as `unknown[]`; `src/api/diagnostics.ts` narrows them. Delete it when the service fixes the annotation.
- **D-MLP-62 / D-MLP-63** no category subtotal and no absent-vs-zero on reconciliation lines — both worked around, both escalated.
- **D-MLP-66 / D-MLP-67** the Item screen reads its rule from traces because the authored configuration is not on the wire, and per-item change attribution is not either.
- **D-MLP-72** optimistic apply is caught at the network, not at the DOM.

**No SPEC amendments were needed.** **No SPEC/ADR conflicts were found.** Nothing under `cashkit/`, `tests/` or `apps/service/` was changed.

---

## 5. Escalations — four API gaps, worked around at the app layer

Per the session brief, a service gap is an escalation and not an edit. All four are recorded in `DECISIONS.md` with reasoning; here they are in one list.

| Gap | Consequence today | Owner / trigger |
|---|---|---|
| **No endpoint exposes an item's authored configuration** (segments, recurrence, escalation, schedule) | The Item screen reconstructs the rule from `GET /book/trace`, which can only see inside the horizon; the screen says so (D-MLP-66) | Service/SDK review — a `GET /book/items/{id}` returning the authored `Item` |
| **No per-item change attribution** in `GET /book/history` | The provenance panel shows the book revision and states that the item's own creating/last-changing revision is not exposed (D-MLP-67) | Same review |
| **`GET /book/reconcile` has no grouping** | The Plan-vs-Actual category view groups rows and shows no subtotal, because a subtotal is a sum of money (D-MLP-62) | Service/SDK review — a `group_by` parameter, or R5's aggregation as an endpoint |
| **`ReconciliationLineOut.actual` is not nullable** | Absent and zero are indistinguishable in the payload; the screen reads settlement from the ledger instead (D-MLP-63) | Service — make it nullable, as `ComparePeriod.values` already is |
| **Four payloads declare `diagnostics: list`** rather than `list[DiagnosticOut]` | The client narrows them verbatim in `src/api/diagnostics.ts` (D-MLP-56) | Service — one annotation each; then delete the narrowing |
| **An event ignores its `scenario` slot** in the sense a user would mean | The compare view can only show item-level and parameter-level differences (D-MLP-55) | SDK review, with ADR-0007 |

`GET /book/reproduce` (D-MLP-46, S3's) is still open and unchanged.

---

## 6. Known gaps and deferrals

| Gap | Owner / trigger |
|---|---|
| Screens 13–14 (import/export, onboarding) | **S5.** The proposal-card path they need is `src/state/edits.ts`, already used by five screens |
| **Maestro flow still never executed** — no Xcode on this machine | Unchanged from S3. **S6** |
| The Maestro flow does not cover the S4 screens | **S6**, when the flow is first run for real. `maestro/smoke.yaml` still walks the S3 path only |
| The `MicButton` on Scenarios has never produced a real transcript | **S6**, by hand on a device (D-MLP-48). On web, dictation is on-device or off (D-MLP-45) |
| The Item screen makes one `GET /book/trace` per non-zero period, capped at 24 | Fine against SPEC §8's 300 ms trace budget on a 12-month horizon; revisit if the horizon grows or the budget is measured and missed. **S6** on staging |
| No month picker on Actuals or Plan vs actual — both show the month containing `as_of` | Post-MLP unless the beta asks for it. SPEC §6 specifies a chip, not a picker |
| httpOnly cookie for the web session | **S6** (D-MLP-44) |
| Latency measurement into `BENCHMARKS.md` | **S6** on staging |

The untracked repository-root files S1, S2 and S3 left alone — `QUICKSTART.md`, `budget-scenarios/`, `km/notes/2026-08-21-privacy-compliance.md`, `km/notes/architecture-deck.html`, `km/notes/cashkit-launch-brief.md`, `.vscode/`, `.cursorindexingignore` — are still untracked and still not this session's to commit.

---

## 7. The first thing S5 should verify

**Run everything before writing anything**, per the session protocol:

```bash
uv sync --all-packages && npm ci
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run pytest apps/service/tests apps/service/trials -q     # → 330 passed, 31 deselected
npm run verify                                              # → exit 0
(cd apps/client && npm run e2e:web)                         # → 36 passed
```

Then verify the one thing S5 builds directly on: **`src/state/edits.ts` is the UI-origin write path, and the import accept-step is one more caller of it.** Read `useEditProposal()` first — it is 120 lines and five screens use it. Three things about it that will shape the import and onboarding screens:

- **`propose()` returns the whole `ProposalResponse`,** including `kind: "clarification"`, which stores nothing. Onboarding's step (b) is a normal turn, so it comes back through `useConversation()` instead — but the accept step is a card like any other.
- **`resolve()` is bound to the rendered pending card,** not to whatever was proposed last. That is deliberate: it makes an optimistic apply hard to write by accident, and a sabotage that tried failed to do anything at all.
- **A refreshed card is stored as the new pending card and re-presented.** Import raises one big proposal after a long loop, so it is the most likely place for the ground to have moved underneath — do not retry it.

And one thing about the harness: **`npm run e2e:web:only` does not rebuild the web bundle.** If the client changed, run `npm run export:web` first or the specs test the previous build. That is worth knowing before debugging a failure that is not there.
