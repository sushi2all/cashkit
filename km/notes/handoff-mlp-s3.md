# Handoff — MLP session S3 (client foundation)

**Date** 2026-08-23 · **Scope** `apps/client/`, `packages/api-types/`, `tooling/eslint-plugin-cashkit/`, `.github/workflows/mlp-client.yml` · **Model calls made: zero** (the browser gate scripts the provider).

S3 built the client foundation: the Expo monorepo (one codebase for iOS, Android and web), the generated API client with a drift check, magic-link auth with deep links, the voice-dictation adapters, and SPEC §6 screens 12, 1, 2, 4, 3 and 5. Two client invariants — no money arithmetic, no model access — are enforced by lint rules with their own test suites, not by review.

**One gate clause was not executed. See §6.**

---

## 1. What was built

### Workspaces

| Path | What it is |
|---|---|
| `package.json` (root) | npm workspaces over the three packages. Pins the hoisted `react-native`, `react` and `react-dom` (D-MLP-40) |
| `packages/api-types/` | The TypeScript client, **generated** from `apps/service/openapi.json` by `openapi-typescript`; `scripts/generate.mjs` also runs the drift check |
| `tooling/eslint-plugin-cashkit/` | `no-money-arithmetic` and `no-model-access`, with `test/` proving each reports what it claims |
| `apps/client/` | The Expo app |

### The client

| Path | What it does |
|---|---|
| `src/money/money.ts` | Renders `{exact, display}`. Never parses, never rounds, never compares. Sign by leading character |
| `src/money/plot.ts` | **The one quarantined module**: turns a figure into a unitless `PlotRatio` for chart geometry (D-MLP-42) |
| `src/ui/tokens.ts`, `src/ui/atoms.tsx` | The computed-receipt vocabulary from `design.pen` 9bdb617: quote rows, receipt cards, leader rows, stamps |
| `src/ui/provenance.tsx` | The WHAT-IF stamp (SPEC §2.4 quoted verbatim), the as-of line, the engine panel, and `DiagnosticList` — verbatim, every field |
| `src/ui/states.tsx` | Loading, empty (with the required example ask) and error states |
| `src/api/client.ts`, `src/api/tokenStore{,.native}.ts` | The typed client; SecureStore on mobile, `localStorage` on web (D-MLP-44) |
| `src/state/session.tsx` | Magic link, verify, sign-out. The only place a token is touched |
| `src/state/book.tsx` | **Committed base state only** — never a scenario override, never a pending change |
| `src/state/conversation.tsx` | The turn loop. No optimistic path; a refreshed card is re-presented, never retried |
| `src/voice/dictation{,.native}.ts` | Dictation adapters, fail-closed (D-MLP-45) |
| `src/screens/` | Auth (S12), Home/Chat (S1) with the Alert variant (S2), the proposal card (S4), Forecast (S3), Trace (S5) |
| `app/` | `expo-router` routes; `/auth/verify` is the deep-link landing for both link shapes |
| `e2e/harness/server.py` | The real service behind a scripted provider, serving the exported app on one origin (D-MLP-49) |
| `maestro/` | The iOS smoke flow — **written, not executed** (§6) |

### The one service change

`apps/service/cashkit_service/routers/auth.py` no longer hard-codes the magic-link host; `web_app_url`, `mobile_scheme` and `verify_path` are settings (D-MLP-51). S1's handoff §5 assigned this constant to S3/S6. Four tests were added to `tests/test_auth.py`, one of which asserts no host is hard-coded in the router.

---

## 2. Exact commands to reproduce every gate

Run from the repository root.

```bash
# Setup, once.
uv sync --all-packages
npm ci
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
(cd apps/client && npx playwright install chromium)

# The service suites, unchanged by this session except for the four new auth tests.
uv run pytest apps/service/tests apps/service/trials -q     # → 330 passed, 31 deselected

# The client's mechanical gates: drift, types, lint-rule tests, ESLint, unit tests.
npm run verify                                              # → exit 0

# The web E2E gate. Exports the bundle, then runs the specs; Playwright starts
# the harness itself. No model key needed — the provider is scripted.
npm run e2e:web                                             # → 9 passed

# The engine suite is untouched and still the default.
uv run pytest --collect-only -q | tail -3                   # → only tests/…
```

`npm run verify` is `api:check-drift && typecheck && test:lint-rules && lint && test:unit`. Individually:

| Gate clause | Command | Expected |
|---|---|---|
| Generated client, not hand-written | `npm run api:check-drift` | "drift check OK" |
| Lint rules are not vacuous | `npm run test:lint-rules` | both suites "all cases pass" (19 cases) |
| Zero client-side money arithmetic | `npm run lint` | "No issues found" |
| Money and geometry primitives | `npm run test:unit` | 13 passed |
| TypeScript strict | `npm run typecheck` | no errors |
| The gate path in a browser | `npm run e2e:web` | 9 passed |

To poke the app by hand:

```bash
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run python apps/client/e2e/harness/server.py --port 8099
cd apps/client && npm run export:web && open http://127.0.0.1:8099
```

---

## 3. What each gate proves, against the PROMPT's wording

> **Playwright web E2E: auth → book (API-seeded via POST /books is acceptable here) → mutation turn → apply proposal → forecast → trace → save**

`e2e/web/gate.spec.ts` walks exactly that, in one test, against the real service. Auth goes through the UI: the Auth screen sends a link, the token is read from the harness's mailer (the service returns a link token in no response, in any mode), and the deep-link route exchanges it. The book is API-seeded, as the gate permits. The mutation turn produces a card, and the test asserts the header figure and the clean flag are **unchanged while the card is pending** — the card is the change (ADR-0029). Apply is a separate act; the applied state is what the screen then renders. Forecast shows the table and provenance; tapping a month row opens the month-scoped trace, which shows the receipt rows, the applied item by id, and the engine panel with the canonical rounding order. Reproduce returns REPRODUCED. Save commits and the revision stamp appears.

Eight further specs run beside it: the four turn kinds (`turns.spec.ts` — including an `answer` with **empty receipts**, and a `refusal` asserted to arrive on a **200** with no error state on the screen), the guards (`guards.spec.ts` — a card the book has moved past is not applied and is not retried, discard leaves the book alone, an expired link shows the error state), and the money invariant (§4).

> **Maestro iOS-simulator smoke of the same path including one dictated turn**

**Not executed.** See §6. The flow is written at `apps/client/maestro/smoke.yaml`.

> **Zero client-side money arithmetic (lint rule + review)**

Three things, because a lint rule alone would be a claim about the mechanism and not about the result.

1. **The rule.** `cashkit/no-money-arithmetic`, type-aware, an error in CI. It reports converting a money figure to a number, arithmetic on one, `.toFixed()`, relational comparison, and `as any`/`as unknown` used to escape those. Outside the quarantined module it bans `Number`/`parseFloat`/`parseInt`/`Math.*` on **anything** — the narrow version leaves the obvious hole open, since reading `m.display` into a local makes it a plain string to the checker (D-MLP-50).
2. **The rule's own tests.** `tooling/eslint-plugin-cashkit/test/`. A rule that matches nothing passes every codebase in silence, so each invalid case is a way money could be computed, and each must be reported. Includes the laundering-through-a-local case and the cast escape.
3. **The result, in a browser.** `e2e/web/money.spec.ts` records every `display` and `exact` the service sent the page, then requires every money-shaped token in the rendered DOM to be one of them. **Verified by sabotage**: making the client drop the cents makes it fail, naming the five invented figures. The first version of this spec did *not* catch that, because its token pattern required decimals — a rounded figure was not a token at all. The pattern was widened and the sabotage re-run.

The review half: I read every money site in the client. The only conversion is in `src/money/plot.ts`, its output is a branded ratio, and no chart label is drawn from it — the low-point and min-cash labels are `warnings.min_cash.display`, and axis ticks that would require deriving a scale value were not drawn at all.

---

## 4. Decisions recorded in `DECISIONS.md`

`D-MLP-40` … `D-MLP-52`, under **Session S3**. The ones later sessions need:

- **D-MLP-42** the chart-geometry exception, why it is safe, and where it is (`allowIn` in `eslint.config.mjs`). Widening it should be argued, not assumed.
- **D-MLP-44** the web bearer is in `localStorage`; the httpOnly cookie is **S6's**, before any external user.
- **D-MLP-45** the speech path as configured **adds no subprocessor** — mobile is on-device-only, web is on-device or off. S6's §9 list needs this sentence, and must not enable `EXPO_PUBLIC_ALLOW_CLOUD_DICTATION` without adding the browser vendor.
- **D-MLP-46** there is **no `reproduce()` endpoint**; the client re-asks and compares strings. An escalation for the SDK/service review.
- **D-MLP-48** the dictated-turn clause is a **device** check. S6 owns it on TestFlight.
- **D-MLP-41** the client renders the cents; the design's whole-euro figures are a mock convention, not a target.

---

## 5. Known gaps and deferrals

| Gap | Owner / trigger |
|---|---|
| **Maestro flow never executed** — no Xcode.app, so no Simulator; no `maestro` binary | **The orchestrator**, then S6. `apps/client/maestro/README.md` has the exact steps |
| Dictation producing a real transcript | **S6**, by hand on a TestFlight device (D-MLP-48) |
| httpOnly cookie for the web session | **S6** (D-MLP-44) |
| `GET /book/reproduce` (the real SDK verb) | SDK/service review (D-MLP-46) |
| Screens 6–11, 15 | **S4**. The stamp, receipt and diagnostic elements they need are built and exported |
| Screens 13–14 (import/export, onboarding) | **S5**. `POST /books` is called from the E2E helper today; the wizard is S5's |
| Universal links, EAS builds, TestFlight | **S6**. `app.json` registers the `cashkit` scheme; `mobile_scheme` is a setting |
| `expo-speech-recognition` is an SDK 56 build on SDK 57 | It installs and typechecks; it has not run on a device. **S6** at EAS-build time |
| The web bundle is a single 1.2 MB chunk | **S6** if the SPEC §8 budgets show it. Not measured here |
| CI workflow is client-only | **S6** owns the full pipeline; `.github/workflows/mlp-client.yml` is the first one in the repo |

The untracked repository-root files S1 and S2 left alone — `QUICKSTART.md`, `budget-scenarios/`, `km/notes/2026-08-21-privacy-compliance.md`, `km/notes/architecture-deck.html`, `km/notes/cashkit-launch-brief.md`, `.vscode/`, `.cursorindexingignore` — are still untracked and still not this session's to commit.

---

## 6. The gate clause that was not run

The Maestro clause needs an iOS simulator. Checked, not assumed:

| Requirement | State |
|---|---|
| Xcode.app | **Absent** — `xcode-select -p` → `/Library/Developer/CommandLineTools` |
| `simctl` / Simulator | **Absent** — "not a developer tool or in PATH" |
| `maestro` | **Absent** — not on `PATH`, no `~/.maestro` |
| Java 17 | Present |

Command Line Tools do not include the Simulator, and `expo run:ios` needs Xcode too. Installing Xcode is a multi-gigabyte machine-level change a session subagent should not make on its own, so the flow was written and left runnable rather than half-run.

What exists: `maestro/smoke.yaml` walking the same path as the web gate against the same harness, with its JS steps, and `maestro/README.md` carrying the toolchain table and the exact commands. What is claimed: nothing. **This clause is open.**

Two caveats about it even once it runs, so it is not mistaken for more than it is. A simulator cannot speak, so the dictated turn asserts the control's state machine and completes as text (D-MLP-48). And the flow has never been parsed by Maestro, so expect to fix syntax on first run.

---

## 7. The first thing S4 should verify

**Run everything before writing anything**, per the session protocol:

```bash
uv sync --all-packages && npm ci
docker compose -f apps/service/docker-compose.dev.yml up -d --wait
uv run pytest apps/service/tests apps/service/trials -q     # → 330 passed, 31 deselected
npm run verify                                              # → exit 0
npm run e2e:web                                             # → 9 passed
```

Then verify the one thing S4 builds directly on: **the WHAT-IF stamp element and the base-committed-header rule already exist, and S4's T15 gate is about making them true everywhere.** Read `src/ui/provenance.tsx::WhatIfStamp` — it renders only when `what_if.stamped` is true, and it names the reason — and then read the comment at the top of `src/state/book.tsx`. The Home header reads from a provider that holds **committed base state only**, which is how SPEC §2.4's "the header shows base committed figures even while a fork is active" is kept true by the data layer rather than by care. S4's Scenarios screen introduces the first surface where a fork is genuinely active; the temptation will be to have the header follow the active scenario. It must not.

Four more things that will shape S4's screens:

- **`DiagnosticList` renders every field the service sent** — code, severity, item, field, message, suggested fix — and has no branch that shortens any of them. R10's verbatim-rendering gate should use it rather than a new component.
- **`LeaderRow` takes a `meta` line**, which is how a row says which engine object it came from (`item:rent · monthly`). The ADR-0013 cell taxonomy S4 implements hangs off that row and its `onPress`.
- **`ProposalCard` already renders crossings and the full deltas block**, so the "crossing flags in every proposal card" clause is satisfied by using the component rather than by adding anything.
- **The money rule will reject the obvious way to draw a Plan-vs-Actual bar.** A bar is `% of plan`, which is a ratio of two money figures. Do not convert in a component: extend `src/money/plot.ts`, keep the return branded and unitless, and render the amounts from `display` beside the bar — the design already puts them there.
