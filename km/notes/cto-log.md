# CTO log

Running log of considerations, decisions and tasks. Newest entry on top. Append, do not rewrite: a changed mind is a new entry that names the one it replaces.

- **Decisions** are numbered `CTO-D-nn`. They are mine to make and mine to reverse. Anything that touches a settled PRD §2 decision or an accepted ADR still goes through an ADR.
- **Tasks** are numbered `CTO-T-nn` and keep their number for life. Status: `open`, `doing`, `done`, `dropped`.
- **Questions** for the founder are numbered `CTO-Q-nn`.

---

## 2026-11-26 — Merge, nightly stopped, bank movements, the book updated in Drive

The founder's instructions, verbatim: *"Merge. Stop the model check. Get bank movements from Qonto and Wise. Keep the book in GDrive. Update it according to the following notes."* The notes are "CashKit – Progress Lab S.r.l. – Piano di cassa nov 2026 → giu 2027", updated 26/11, from the "Libro cassa" sheet, revision 58. They are not copied here: Progress Lab's figures stay out of this repository (ADR-0038 decision 6).

### What I found that I did not know

- **A book already existed in Drive.** Folder "Progress Lab – Cassa fine 2026 (libro CashKit)". A zip built on 22/11 from data as of 14/11 (shorter horizon, 6 scenarios, 20 movements), with a README and a report written by an earlier session. Its conventions were sound and I kept them: events on container items, single-date items for whatever a scenario changes, net + VAT only where the split is documented, no VAT regime, a `certezza` tag.
- **Wise is linked inside Qonto** as an external account, so one connection reads both banks. There is no separate Wise connector, and none is needed.
- **Both bank balances match the opening balance in the notes.** Everything settled up to 25/11 is already in it, and nothing has settled since, so there were no actuals to import into the plan's horizon.

### Decisions

| # | Decision | Why |
|---|---|---|
| CTO-D-21 | **The book lives in that Google Drive folder**, as a `.tar.xz` of the whole book directory, plus `build_book.py`, a README and a report as Google Docs, and the bank export as a Google Sheet. Replaces CTO-D-19's "separate private repository". | Founder's instruction. Drive keeps file versions; the archive keeps the book's own git history inside it. |
| CTO-D-22 | **Rebuilt rather than edited in place.** The 22/11 book was moved, untouched, to "archivio (superato)", so its forecast stays reproducible from its own archive. | The notes change the horizon, the opening date and balance, most movements and every scenario. Editing in place would have meant voiding nearly every event. It would also have meant moving a larger history archive through this session, where every byte is copied by hand. |
| CTO-D-23 | **Dates exactly as in the notes**, with no weekend shift (the 22/11 book shifted F24s to Monday). | The notes are the founder's source. Month-end figures are unaffected; the report says so. |
| CTO-D-24 | **Where the notes give a lever without a date or duration, pick the obvious one and say so in the report and the README.** "Moves to January" becomes the same day of the month a month later; "can move again" becomes one month. The three cases are listed in the Drive README. | Each is one line to change. Asking would have blocked the whole update on three what-ifs. |

### Evidence

Figures are in the Drive report ("Report – Piano di cassa (al 26-11)"), not here (ADR-0038 decision 6).

- **The book reproduces the notes.** Every month-end balance, the year-end balance, the low point and its date, the final balance and the total owed to the founder match the notes. `validate()` is clean; one commit.
- **The archive reopens:** unpacked, `CashKit.open` gives the same closing balance and a clean status. Its sha256 is in the Drive file's description.
- **The bank export reconciles.** Six-plus months of movements for both accounts, totals in Decimal. Qonto's running balance has no breaks and ends exactly on the current balance. Wise has no running balance in the API, so only the count and total are checked.
- **The loan in the notes is the bank's.** The instalment amount and numbering in the notes continue the sequence the bank shows.

### Findings to put to the founder

1. **The notes understate how long the balance is negative.** They say mid-December to the end of March. Day by day, the base scenario is positive for a single day in mid-March and then negative again until the end of April.
2. **In one lever scenario the notes name one hole at the end of December; there are three more** inside January and February. Month-ends are all positive, so the month-end view hides them.
3. **One social-security payment is dated before the pay it is computed on is paid**, which contradicts the rule stated in the 22/11 README. Kept as in the notes; a question for the payroll consultant.

These are the first value the engine has added over the sheet: they come from looking at every day rather than every month-end. Details and dates are in the Drive report.

### Repository

- PR #2 opened. On its first CI run the engine suite, the eval self-tests and the web E2E were green.
- The client checks job then got past the lint-rule step (CTO-T-02 fix confirmed in CI), TypeScript and ESLint, and failed at unit tests. Cause: the lockfile was generated on macOS and lists only rollup's darwin binary (the npm optional-dependency bug), so vitest could not load on Linux. Fixed by declaring `@rollup/rollup-linux-x64-gnu` 4.62.5 as an optional dependency (`af299af`). Verified with `rm -rf node_modules && npm ci && CI=true npm run verify`.
- Nightly schedule removed (`25d170e`). Manual runs still work; restore instructions are in the workflow header. The drills shared the trigger and stopped too.

### Task status changes

| # | Task | Status |
|---|---|---|
| CTO-T-01, 02, 03 | Engine CI, client CI, ChartAxis lint | done; proven in CI on PR #2 (client unit tests after `af299af`) |
| CTO-T-04 | Nightly model check | done: schedule stopped (founder). The restore drill failure on the hosted runner stays undiagnosed. |
| CTO-T-33 | Build the Progress Lab book | done: from the 26/11 notes, in Drive |
| CTO-T-34 | Reproduce six months against the bank | **open, next.** The export is in Drive. The book's horizon starts 25/11, so the reproduction needs a second book (or a history horizon) from May. |
| CTO-T-42 | Answer findings 1–3 with the founder; update the notes or the book | open |
| CTO-T-43 | Make the book update a routine: each week, read new Qonto movements, import them as actuals with `ext_id` = transaction id, move the cutover, re-run, re-upload | open, from step 3 |

Questions: CTO-Q-15 (bank data) and CTO-Q-17 (where the book lives) are answered. CTO-Q-16 is answered (merge). CTO-Q-11 is answered (stop).

---

## 2026-11-22 — Internal first is confirmed; first fixes made

The founder's words: *"it was born for an internal need. If it stays there and works, fine for me. Let's just go with internal for now."* Recorded as [ADR-0038](../adr/0038-internal-first.md). Plan: [`2026-11-22-internal-first-plan.md`](2026-11-22-internal-first-plan.md). The roadmap in the 14 Nov assessment is marked superseded.

### What changed from the 15 Nov proposal

Two things, both because of how the founder put it.

- **"If it stays there and works, fine."** A public release is no longer a goal with a date, so the risk I named on 15 Nov ("polished enough has no end") stops being a risk: there is no end being waited for. The exit-criteria task for going public (CTO-T-39) is dropped. What replaces it is a plain definition of "it works for Progress Lab" (CTO-T-40).
- **"Internal for now."** The 15 Nov proposal carried one client book, operated by us, as a quarter goal. That is out. No client data this quarter; it reopens only through a new ADR (ADR-0038 decision 5).

Market risk is accepted by the founder, not answered. I am not carrying it as an open worry; it is written into ADR-0038 so it is not forgotten.

### Decisions

| # | Decision | Replaces |
|---|---|---|
| CTO-D-13 | **Objective: Progress Lab plans its cash on CashKit every week and has retired the spreadsheet.** No public release date. | CTO-D-01 |
| CTO-D-14 | Desktop stays unbuilt and parked. If it is ever built, Progress Lab's weekly routine is its reference user. | CTO-D-03 |
| CTO-D-15 | An engine change is allowed when the real book needs it, and only after the version guard (CTO-T-08) exists. | CTO-D-06 |
| CTO-D-16 | The outside security pass and the login-cookie change wait **while the service is off the public internet and nobody outside Progress Lab has a login**. Either condition failing puts them first. | CTO-D-11 |
| CTO-D-17 | SDK first, app second. An exclusion in the app (VAT, terms, split payments) is lifted only from recorded friction in weekly use, one ADR each. | new |
| CTO-D-18 | No client data this quarter. | new |
| CTO-D-19 | Progress Lab's book lives in a separate private repository. Nothing derived from it is committed here; a fixture that a real case inspires is rebuilt with invented figures. | new |
| CTO-D-20 | I now change code. Work goes on the session branch and reaches main through a pull request, which I open when the founder asks. | CTO-D-12 |

Still standing from 14 Nov: D-02 (web first, now meaning the private deployment), D-04, D-05, D-07, D-08, D-10. D-09 (one visual direction) is moot while the restyle is parked.

### Work done today

All on branch `claude/modest-cray-wby51g`, not yet on main.

**CTO-T-02 — why the client pipeline has been red since August. Found and fixed.**
The lint-rule tests feed code to the linter as strings. When the environment has `CI=true`, the TypeScript parser assumes a one-off command-line run and reads each file's types from disk (`inferSingleRun` in `@typescript-eslint/typescript-estree` 8.67). The in-memory code then has no type information, the type-dependent cases report nothing, and the test fails with "Should have 1 error but had 0". It passes on any machine where `CI` is unset, which is why every local run was green and every CI run was red. Reproduced locally with `CI=true npm run test:lint-rules`. Fix: `disallowAutomaticSingleRunInference: true` in the test's parser options (`tooling/eslint-plugin-cashkit/test/money.test.js`). The app's own lint was never affected, because there the files are real files.

Because that step failed first, CI never reached TypeScript, ESLint or the unit tests. So the gate has not run in CI since it was written.

**CTO-T-03 — the three ESLint errors in `ChartAxis.tsx`. Fixed.**
The rule was right and I was wrong to call it a near false positive on 14 Nov: outside `src/money/plot.ts` it bans every `Math.*` call on purpose (D-MLP-42), so that no file but one does arithmetic at all. The gutter-width sum now lives in `plot.ts` as `gutterWidth(labelLengths, fontSize, floor)`, with a unit test; `ChartAxis.tsx` only measures label lengths and calls it. The `allowIn` list was not widened.

**CTO-T-01 — engine pipeline. Written.**
`.github/workflows/engine.yml`: the engine suite with the coverage gate, and the eval suite's self-tests, on every change under `cashkit/`, `tests/`, `evals/`, `pyproject.toml`, `uv.lock`. The performance budgets run on demand only: they are wall-clock assertions (one is 4.5 ms against 5 ms) and would flicker on a shared runner.

**CTO-T-07 — README status. Done.** The table now says what exists and adds a short map.

Verified locally:

| Check | Result |
|---|---|
| `CI=true npm run verify` (drift, typecheck, lint-rule tests, ESLint, unit tests) | exit 0 |
| `npx vitest run` in `apps/client` | 33 passed (one new) |
| `uv run pytest -m "not benchmark" --cov`, as the new workflow runs it | exit 0, 96.63% |
| `uv run pytest evals/tests` | 248 passed |

Not verified: none of this has run in GitHub Actions. The workflows trigger on main and on pull requests, and there is no pull request yet. **Until one is opened, "fixed" means "fixed on the branch".**

### Task status

| # | Task | Status |
|---|---|---|
| CTO-T-01 | Engine CI workflow | done on branch; unproven in CI |
| CTO-T-02 | Client CI: lint-rule step | done on branch; unproven in CI |
| CTO-T-03 | ESLint errors in `ChartAxis.tsx` | done on branch |
| CTO-T-04 | Nightly: key or pause; diagnose the restore drill on the hosted runner | open — needs CTO-Q-11 |
| CTO-T-05 | Branch protection, pull requests | open — needs the founder |
| CTO-T-06 | Commit the cited-but-missing files | open — founder |
| CTO-T-07 | README status | done on branch |
| CTO-T-08 | `ENGINE_VERSION` guard, with a short ADR | **open — next** |
| CTO-T-12 | Deployment — now private network only | open, step 3 |
| CTO-T-17 | Written zero-retention confirmation | open — founder |
| CTO-T-19 | Full gate set on a machine with Docker and a key | open, before step 3 |
| CTO-T-22 | Restore drill on the real book | open, step 3 |
| CTO-T-23 | Fallback model | open, low priority |
| CTO-T-25 | Service fixes that delete client workarounds | open, step 4 |
| CTO-T-30 | February review | open, step 5 |
| CTO-T-32 | Plan for the following quarter | open, step 5 |
| CTO-T-33 | Build the Progress Lab book through the SDK, in a private repository | open, step 2 |
| CTO-T-34 | Import six months of bank movements and reproduce them; write up every gap | open, step 2 |
| CTO-T-35 | Private deployment, backups, logins for two people | open, step 3 |
| CTO-T-36 | Weekly cash review on CashKit, with a friction log | open, step 3 |
| CTO-T-37 | Decide which app exclusions to lift, from the friction log; ADR each | open, step 4 |
| CTO-T-40 | Agree the definition of "it works for Progress Lab" (draft in the plan) | open — founder + me |
| CTO-T-41 | Take down the "early access · autumn 2026" line on the landing page | open — founder |
| CTO-T-09, 10, 11, 13, 14, 15, 16, 18, 20, 21, 24, 26, 27, 28, 29, 31, 38, 39 | Public-beta work, client book, public exit criteria | dropped or parked by ADR-0038 |

### Questions for the founder, current list

Answered or removed by the decision: CTO-Q-01, 02, 05, 06, 07, 08, 09, 10, 13, 14 (Q-14 is accepted in ADR-0038; the written confirmation is still CTO-T-17).

| # | Question | Blocks |
|---|---|---|
| CTO-Q-15 | Where do Progress Lab's bank movements come from, and may I read them? This workspace has a connection to a business-banking provider; if that is Progress Lab's bank, six months of movements can be read directly once you say so. | Step 2 |
| CTO-Q-12 | Who else at Progress Lab would run the weekly update? | Step 3 |
| CTO-Q-16 | Shall I open a pull request for today's fixes? And may main be protected so nothing lands unchecked? | Proving T-01 to T-03 |
| CTO-Q-11 | Nightly model check: add the key (about $9 a month) or pause it? | CTO-T-04 |
| CTO-Q-17 | Where does the private repository for the book live, and which private machine or network hosts the app? | Steps 2 and 3 |
| CTO-Q-03 | Can the missing files be committed (`design-purple.pen`, `QUICKSTART.md`, the launch brief, the privacy note)? No longer urgent; still wanted. | CTO-T-06 |
| CTO-Q-04 | Hours per week you can give this. The plan assumes a few hours in step 1 and a standing weekly slot from step 3. | Pace |

### Next time

- Start with CTO-T-08. It is the one piece of engineering that must exist before the real book does.
- If a pull request has been opened, read its checks before anything else. The engine and client workflows have never been seen green; expect at least one surprise.
- Step 2 cannot start without CTO-Q-15 and CTO-Q-17.

---

## 2026-11-15 — Reframe considered: internal first (Progress Lab, then some of its clients)

**Status: proposed by the founder as a question, not confirmed.** Nothing in the 14 Nov entry is replaced until it is. If confirmed, the `CTO-D` changes below take effect, ADR-0027 needs an amending ADR, and the roadmap in the assessment gets rewritten.

### What I checked

- No file under `apps/service/cashkit_service` mentions VAT or withholding. The app cannot express either; the engine can (`cashkit/engine/vat.py`, `tax.py`).
- `books.user_id` is unique: one book per login, one login per book.
- No bank-file import in the service. The SDK has `import_events`, idempotent on `(source, ext_id)`, and the eval suite exercises it.

### Considerations

**It changes which product is being tested, more than how fast.** Progress Lab is a business. Its cash plan needs VAT settlement, invoices on 30/60-day terms, payroll and tax dates, and actuals from the bank. The consumer app was specified without all of those (SPEC §1). The engine has them. So dogfooding points away from the consumer mobile app and toward the persona in `ERP-pilot-guide.md` and the desktop design. CTO-Q-07 (which wedge) and CTO-Q-06 (is there a real pilot) mostly answer themselves: business first, and the pilot is us.

**The fastest honest start skips the app.** Build the Progress Lab book with the SDK, driven by an agent using the eval-proven reference document. Import bank actuals. Then do what the pilot guide says to do first: reproduce the last six months and compare with the bank statement. Every figure the engine has produced so far came from synthetic books. This is the first test against reality and it needs no deployment, no email provider and no UI work.

**The app comes second, as the daily surface, and its gaps become the backlog.** Deploy it privately, use it for questions and what-ifs, and let real use decide which exclusions to lift (VAT and payment terms in the intent grammar first, I expect). That is a better way to prioritise than the S6 backlog.

**What relaxes.**

- The public compliance gate, for Progress Lab's own data: no outside data subject, no public privacy policy or terms, lawyer hour deferred.
- Email provider: company mail or an allow-list is enough for a handful of users.
- The outside security pass and the login-cookie change: deferrable **only while the service is on a private network**. That condition is the whole basis for deferring.
- Landing page, analytics, waitlist, pricing, native mobile builds, the restyle.
- Cohort metrics: with three users they mean nothing. Replace with a friction log and forecast-versus-actual drift.

**What does not relax, or tightens.**

- Phase 1 (honest CI, pull requests) is unchanged.
- The `ENGINE_VERSION` guard (CTO-T-08) becomes urgent: real books are saved from day one, and "what did we forecast in November" must stay answerable.
- Backups and the restore drill: it is real company data.
- Model routing: Progress Lab's finances will pass through the router and the model. The founder can accept that risk for his own company. He cannot accept it on a client's behalf.

**Clients are not "internal".** The moment a client's figures go in, Progress Lab is processing someone else's financial data: the DPA, the subprocessor list and the zero-retention confirmation all come back. Two ways to keep that light. (a) Progress Lab operates the tool for the client as part of an existing engagement, and the client never logs in; check what the engagement contract already covers. (b) Run it on the client's own machine, which is ADR-0017 configuration A and keeps ADR-0026 rule 3 true. I prefer (a) to start: it needs the least polish, it suits a consultancy (sell the weekly forecast, not the software), and a wrong number is caught by us before the client sees it. Not legal advice.

**The one-book-per-login limit will bite.** Several books (Progress Lab plus each client) and more than one person per book (founder, admin, accountant) are both outside ADR-0027. Do not build multi-user now. Use one login per book, note every time it hurts, and let that evidence size the work.

**Risks this path adds.**

- The builder is the user. A founder using his own tool proves little and forgives everything. The real test is a second person at Progress Lab who did not build it doing the weekly update unaided.
- "Until it's polished enough" has no end. Exit criteria and a review date have to be set now, or internal use becomes a place to stay.
- A wrong forecast shown to a client costs the main business, not just this one.
- Market risk is postponed, not reduced. Internal use tests whether the product works, not whether strangers want it. If natural-language authoring is an open window (ADR-0025), each quarter spent inside spends some of it.
- Real use will produce a stream of wishes. One person cannot follow them all; cap it weekly.

### How the decisions would change

| # | Change |
|---|---|
| CTO-D-01 | Objective becomes: **Progress Lab runs its own cash planning on CashKit every week and has retired the spreadsheet; one client book is operated by Progress Lab; then decide in February.** |
| CTO-D-03 | Desktop stays unbuilt, but Progress Lab is now its design partner; the frames get judged against our own weekly routine. |
| CTO-D-06 | Engine freeze loosens: a change that a real book needs is allowed (candidates: issue #1 directed rounding, SR-1 terms from a param, SR-2 tax-payment day anchor), **after** the version guard exists. |
| CTO-D-11 | Security pass moves to "before any login from outside Progress Lab's network, or any client self-service". Until then the service is not reachable from the internet. |
| new | Business features (VAT, payment terms, split payments) enter the app's intent grammar when weekly use shows the need. Needs an ADR; it reverses a SPEC §1 exclusion. |
| new | Client books are operated by Progress Lab under a signed data-processing addendum. No client login this quarter. |
| unchanged | D-02 (web first), D-04, D-05, D-07, D-08, D-09, D-10. |

### Tasks, if confirmed

- **Dropped or parked:** CTO-T-09 (landing; but the "early access, autumn 2026" line should come down), T-11 as scoped, T-14, T-16, T-18, T-20, T-24, T-27, T-28, T-31. T-13 and T-15 move behind real-use friction.
- **Kept as is:** T-01 to T-08, T-12 (private deployment), T-17, T-19, T-22, T-23, T-25, T-26 (controllers become our own clients), T-30, T-32.
- **New:**

| # | Task | Owner |
|---|---|---|
| CTO-T-33 | Build the Progress Lab book through the SDK: items, VAT regime, terms, payroll, tax dates. Kept in a private repository, not this one. | F + C |
| CTO-T-34 | Import six months of bank actuals (ADR-0026 route: file export or own bank API key) and reproduce them; write up every gap between book and bank. | F + C |
| CTO-T-35 | Private deployment with backups and one restore drill; logins for the founder and one non-builder colleague. | C |
| CTO-T-36 | Weekly cash review held on CashKit, with a friction log; forecast-versus-actual drift recorded each week. | F |
| CTO-T-37 | From the friction log, decide which SPEC §1 exclusions to lift first; ADR. | C |
| CTO-T-38 | First client book, operated by us: check the engagement contract, sign the addendum, confirm zero-retention routing in writing first. | F |
| CTO-T-39 | Write the exit criteria for "ready for the public" and agree them. Draft: eight straight weeks without falling back to the spreadsheet; a non-builder runs the weekly update unaided; two or three client books run a full month with no correction incident; then the deferred public gate (security pass, compliance checklist, email) is the release checklist. | C + F |

### Questions this adds or removes

- **Removed:** CTO-Q-01 (email provider), Q-02, Q-09, Q-10; Q-05 loses urgency; Q-06 and Q-07 are answered by the reframe.
- **CTO-Q-12** Who at Progress Lab, other than you, would run the weekly update?
- **CTO-Q-13** Which client first, and does the current engagement contract cover handling their financial data?
- **CTO-Q-14** Are you comfortable with Progress Lab's own finances passing through the model router before the written zero-retention confirmation arrives?
- **CTO-Q-15** Where do Progress Lab's bank actuals come from today, and in what form can they be exported?

---

## 2026-11-14 — First assessment

Report: [`2026-11-14-cto-assessment.md`](2026-11-14-cto-assessment.md). This entry is the working behind it.

### 1. How I assessed

Read: `CLAUDE.md`, `README.md`, `SPEC-mlp-consumer.md`, the ADR index and ADRs 0014, 0017, 0025, 0026, 0027, 0036, `handoff-s5-6`, `handoff-mlp-s6`, `compliance/SPEC9-checklist.md` (first half), the PRD review, the proto findings, the eval first-results note, `pending-spec-updates.md`, `design/desktop/REQUIREMENTS.md` §8–9, the head of `ERP-pilot-guide.md`, the three CI workflows, the landing page copy, and commit messages since 23 Aug. Skimmed `BENCHMARKS.md` headings. Did **not** read `PRD-cashkit.md` or `DECISIONS.md` end to end.

Ran, on this container (Python 3.13, Node 22, Postgres 16 standing in for 17):

| Check | Command | Result |
|---|---|---|
| Engine suite, with performance budgets | `uv run pytest -o addopts="" -q` | **1,131 passed** in 32 s |
| Engine coverage gate | `uv run pytest -m "not benchmark" --cov` | **96.63%** (gate 90%), exit 0 |
| Service suite + deterministic trials | `uv run pytest apps/service/tests apps/service/trials -q` | **503 passed**, 51 deselected (live-model and drill markers) |
| Client: schema drift, typecheck, lint-rule tests | first three steps of `npm run verify` | pass |
| Client: ESLint | `npm run lint` | **fails, 3 errors**, all in `apps/client/src/screens/components/ChartAxis.tsx` lines 33 and 40 (`cashkit/no-money-arithmetic` on `Math.max`/`Math.ceil` over label lengths; introduced by `ffe963b`) |
| Client unit tests | `npx vitest run` in `apps/client` | **32 passed** (needed the rollup native module installed by hand; an npm optional-dependency quirk of this container) |

Read from GitHub (Actions, PRs, issues):

- **MLP nightly:** 82 runs. The 25 most recent all failed. In the latest, the live-trials job stops at "There is a key, or say so plainly" (no `OPENROUTER` secret), and the drills job fails at "Restore from backup, end to end" after 30 s. The model-behaviour gate has never run in CI.
- **MLP client:** 7 runs; the 4 most recent all failed. In the latest (1 Oct, `6922bc2`), the "web E2E" job is **green** and the checks job fails at "Lint rules do what they claim", so TypeScript, ESLint and unit tests were **skipped**. That step passes locally, so the failure is environment-specific. It has been masking the ESLint failure above.
- **MLP service:** 4 runs, 3 green, last green 1 Oct on `6922bc2`.
- **No workflow runs the engine suite.** `mlp-service.yml` triggers on `cashkit/**` but says in its header that engine tests are not its business.
- No pull request has ever been opened. One open issue (#1, formula language lacks directed rounding).

**Not verified, and why:**

- Live-model trials (41 tests) and the three drills: need a model key and a Docker daemon. Last claimed green by S6 on 23 Aug, on the founder's machine.
- Web E2E locally: relied on the green CI job of 1 Oct. Client code has not changed since.
- `evals/tests` (247 tests): not re-run. Results in the eval note are from 5 Nov.
- Any native mobile build. None has ever completed (S6 ran out of disk).
- The deployed landing page, if one is deployed. The committed files post to `https://sibforms.com/serve/PLACEHOLDER`.
- Security. I read no auth or isolation code. Everything in the report on security is from the handoff notes.
- Market claims (the "open gap" in ADR-0025, competitor behaviour). Taken from the repository or assumed; no research done.
- The clone is shallow (50 commits, from 22 Aug). The engine's own history (19 Jul – 7 Aug) is known to me only from handoff notes.
- External tools (CRM, meeting notes, mail, analytics) were not consulted. If customer or pilot conversations exist, they are not in this picture.

### 2. Considerations

**The inventory is bigger than the brief said.** I was told "an SDK and designs for a desktop and a mobile app". The repository also holds a FastAPI service (11k lines), an Expo client for web and mobile (12 routed screens, 16.5k lines), a compliance set and a deployment stack. So the mobile app is not a design, it is a built app that has never run on a phone; the desktop app is the only pure design.

**The gap is validation, not engineering.** The engine is the strongest asset and it is finished. The app is built and tested. What is missing is small and mostly non-code: an email provider, a server, DNS, a model key in CI, a form endpoint. Those have been open since 23 Aug with the founder named as owner on each. Meanwhile the work that did happen (two more mobile style explorations, the desktop design, landing v5, the eval suite) added surface. I read this as avoidance of the launch step, not as a capacity problem, and the plan is built to make the launch step the only thing on the table.

**The speed of the build is a risk to price in.** The service and client went from nothing to "six sessions, all gates passed" on 22–23 Aug. The handoffs are unusually candid, and the tests are real (I ran them). But tests written by the same sessions that wrote the code share their blind spots, and no human review is recorded. Before strangers put financial data in, an independent pass over sign-in, per-user isolation and the import path is warranted. I would rather find an authorisation hole than be told about one.

**CI has been red for so long it has stopped meaning anything.** A nightly that fails 82 times and a client job that never goes green teach everyone to ignore red. Worse, the client failure hid a genuine lint error on main for seven weeks. The repo's own rule is "a gate clause that could not run is a finding"; by that rule the nightly should be paused with a note, or given its key. And the engine, the one component where a silent wrong number is the stated worst case, has no pipeline at all.

**`ENGINE_VERSION` rests on discipline alone.** `caccc4f` changed 200 lines across `engine/expand.py`, `graph.py`, `run.py` and `tax.py` and left the version at `"1"`, on the commit's own claim that no number moves. The dual-engine tests make that claim credible. But `at()` and `reproduce()` depend on it, CLAUDE.md admits nothing enforces it, and once users have saved books a missed bump is a silent wrong historical figure. A golden-figures test (a fixed corpus of books whose full output is hashed and pinned beside the version string) would turn the discipline into a check. Cheap now, expensive later.

**Focus.** For one person: three personas, three surfaces, two hosting models (hosted, local-first) and four visual directions (`design.pen`, `app.pen` S1b, `garden.pen`, and `design-purple.pen`, which commit `6922bc2` calls the chosen one and which is not in the repository). ADR-0027 already sequenced this correctly: consumer hosted app first. The plan holds that line.

**But the consumer wedge has a tension worth naming.** The consumer MLP excludes VAT, payment terms and withholding, which are exactly where the engine beats a spreadsheet. It also has no bank sync, which ADR-0027 itself calls table stakes in consumer budgeting. And the landing page shows a "Freelancer" persona with invoice tranches and a "VAT set-aside" line, which the MLP cannot produce natively (ADR-0025 rule 3 is about arithmetic, but the spirit applies: do not show what the product does not do). My instinct is that the sharper first market is Italian freelancers and micro-businesses: manual entry is normal for them, tax timing is their actual pain, and the engine already models it. That would be a change to ADR-0027 and is the founder's call. I am not making it now. I am making sure the beta produces the evidence to make it in February: recruit a deliberate share of freelancers, and interview them.

**Desktop.** ADR-0036 and its requirements document are good work, and they list 12 SDK/service requests and 19 open questions. Building it this quarter would split a one-person team across two products with zero users on either. The frames are enough to test demand in conversations.

**What I would not change.** The architecture is right for the stage: per-user file stores on one node, Postgres for app data only, storage behind an interface, a deferral with a numeric trigger for the database-backed store. The proposal-before-write invariant and the "client never computes money" rule are the product. The decision-record habit is worth its cost. No rewrites.

**Documentation load.** `DECISIONS.md` is 240 KB. The discipline is an asset, but nobody new (human or agent) can absorb that. Not urgent; an index by topic would help when a second person joins.

**Vendor exposure.** One pinned model (`google/gemini-3.7-flash`) through one router. ADR-0028 makes the trial suite the boundary for any model change, which is the right mechanism, but it has only ever been exercised on one model in the service. Qualifying a second before public launch turns a deprecation or an outage from an emergency into a config change.

### 3. Decisions

| # | Decision | Why |
|---|---|---|
| CTO-D-01 | **The quarter's single objective is real users on the existing consumer web app, then a direction decision in February.** No new product surface is started before that review. | Market risk is the only large unknown left; everything else is built. |
| CTO-D-02 | **Beta is web-first.** Native Android/iOS test builds happen in phase 3 and do not gate the beta. | The web path is the only one proven end to end (49 browser tests). No device build has ever succeeded. Apple enrolment is already deferred. |
| CTO-D-03 | **Desktop stays design-only this quarter.** It is tested by showing frames to controllers, not by building. | 12 unmet SDK/service requests, 19 open questions, no confirmed customer. |
| CTO-D-04 | **Main must be green and must mean it.** Add an engine workflow. Fix the client workflow. A scheduled job that cannot run is paused with a dated note, not left red. | A permanently red pipeline hid a real failure for seven weeks. |
| CTO-D-05 | **Changes land through pull requests with required checks**, including agent sessions' work, even with one human. | Creates the review point that does not exist today, and makes CI status visible per change. |
| CTO-D-06 | **The engine is frozen except for defects and for SDK additions that delete an app-layer workaround** (S6 backlog items 5–10). `ENGINE_VERSION` gets a mechanical guard before any engine change lands. | Protects the strongest asset while attention is on launch. |
| CTO-D-07 | **Keep the current architecture.** No Postgres revision store, no second node, no queue, until the recorded triggers fire. Exception: make an in-flight import survive a restart if beta shows it matters. | The deferrals are sound and have numeric triggers. |
| CTO-D-08 | **The repository is the only source of truth.** Files that decisions cite are committed, or the citing record is amended. | `design-purple.pen`, `QUICKSTART.md`, the launch brief, the privacy note and `design/web/engine.js` are referenced and absent. Bus factor is already 1; it should not be less. |
| CTO-D-09 | **One visual direction for the quarter.** Once the founder confirms it, the others are archived and the shipped client is restyled only as far as the beta needs. | Four directions for one app is three too many. |
| CTO-D-10 | **Keep the pinned model for the beta; qualify one fallback through the trial suite before public launch.** | Removes the single point of failure without reopening ADR-0028. |
| CTO-D-11 | **An independent security pass precedes the first outside user**: sign-in and sessions, per-user book isolation, the import path (file parsing and prompt content), rate and spend limits. The login-token move to an httpOnly cookie (D-MLP-44) is part of it. | The app layer has had no human review, and it will hold financial data. |
| CTO-D-12 | **This session changes no code.** It is an assessment. Fixes start with CTO-T-01. | Asked for a report; and CTO-D-05 says fixes go through review. |

### 4. Tasks

Ordered by phase. "F" in the owner column means it needs the founder; "C" means me.

**Phase 1 — make the checks honest (16–29 Nov)**

| # | Task | Owner | Status |
|---|---|---|---|
| CTO-T-01 | Add an engine CI workflow: default suite plus the coverage gate, on every change under `cashkit/`, `tests/`, `pyproject.toml`, `uv.lock`. | C | open |
| CTO-T-02 | Find why "Lint rules do what they claim" fails in CI and passes locally; fix it so later steps run. | C | open |
| CTO-T-03 | Resolve the three ESLint errors in `ChartAxis.tsx`. The code measures label width, not money; either move it into the quarantined geometry module (D-MLP-42) or teach the rule the difference. Do not blanket-disable. | C | open |
| CTO-T-04 | Nightly: add the model key as a repository secret, or pause the schedule with a dated note. Separately diagnose why the restore drill fails on the hosted runner. | F (key) / C | open |
| CTO-T-05 | Turn on branch protection for `main` with required checks; adopt PRs. | F (repo admin) / C | open |
| CTO-T-06 | Commit the cited-but-missing files (CTO-D-08), or amend the records that cite them. | F | open |
| CTO-T-07 | Update the README status table (it still lists phases 2–10 as pending) and add a one-paragraph map of the repository. | C | open |
| CTO-T-08 | `ENGINE_VERSION` guard: pinned hash of full outputs over a fixed corpus, stored beside the version; the test fails if outputs move without a version change, or the version changes without the pin. Needs a short ADR. | C | open |
| CTO-T-09 | Landing page: replace the placeholder form endpoint; add cookieless analytics; check every figure and feature shown against what the beta does (the VAT set-aside line in particular). Confirm what is live today. | F / C | open |
| CTO-T-10 | Record the visual-direction decision as an ADR (amending 0023) once CTO-Q-03 is answered. | C | open |

**Phase 2 — closed beta live (30 Nov – 20 Dec)**

| # | Task | Owner | Status |
|---|---|---|---|
| CTO-T-11 | Email provider: one `Mailer` implementation, the key in the env file, the vendor on `compliance/subprocessors.md` (the compliance test enforces the last). | F (vendor) / C | open |
| CTO-T-12 | Provision EU hosting, DNS, object storage, metrics account; deploy staging then production per `ops/DEPLOY.md`. | F (accounts) / C | open |
| CTO-T-13 | Move the web session to an httpOnly, Secure, SameSite=Strict cookie (D-MLP-44). Touches `auth.py`, the client token store and the browser specs. | C | open |
| CTO-T-14 | Security pass per CTO-D-11. Prefer an outside reviewer if budget allows (CTO-Q-04); otherwise a fresh-context review session with no access to the build sessions' notes. | C | open |
| CTO-T-15 | Read-turn latency: stream the answer (BENCHMARKS.md option; D-MLP-132), pending CTO-Q-05. Measure time to first visible text. | C | open |
| CTO-T-16 | Product metrics from the `turns` and `proposals` tables: sign-ups, activation (book created and one proposal applied), proposal accept rate, clarification rate, weekly retention, cost per active user. SQL views are enough. | C | open |
| CTO-T-17 | Get written confirmation of zero-retention routing from the router (handoff-mlp-s6 §7.3) and file it. | F | open |
| CTO-T-18 | Walk `compliance/SPEC9-checklist.md` to green; re-date it. | C | open |
| CTO-T-19 | Run the full gate set on a machine with Docker and a key (commands in handoff-mlp-s6 §10) before the first invite. | C | open |
| CTO-T-20 | Invite the first 10–20 users; a feedback channel; a one-page "what to do when something looks wrong" for them and a runbook for us. | F / C | open |

**Phase 3 — learn and harden (21 Dec – 31 Jan)**

| # | Task | Owner | Status |
|---|---|---|---|
| CTO-T-21 | Weekly review of the CTO-T-16 numbers; fix the top friction each week; log what changed. | C | open |
| CTO-T-22 | Restore drill against a real production backup. | C | open |
| CTO-T-23 | Qualify a fallback model through the trial suite (CTO-D-10); record cost and latency. | C | open |
| CTO-T-24 | First Android build via EAS, then the Maestro flow; iOS test build once Apple enrolment is decided. | F (Apple) / C | open |
| CTO-T-25 | Service fixes that delete client workarounds: typed diagnostics on four payloads, grouping on reconcile, nullable `actual`, `GET /book/items/{id}` (S6 backlog 6–9). | C | open |
| CTO-T-26 | Interviews: 5–8 freelancers on the beta; 3–5 controllers shown the desktop frames. Write up each. | F / C | open |
| CTO-T-27 | One hour with an Italian fintech lawyer (ADR-0026). | F | open |
| CTO-T-28 | Competitive review: who lets you author a budget or forecast in plain language today, consumer and small-business. | C | open |
| CTO-T-29 | Decide whether an in-flight import must survive a restart (D-MLP-83), from beta evidence. | C | open |

**Phase 4 — decide (1–14 Feb)**

| # | Task | Owner | Status |
|---|---|---|---|
| CTO-T-30 | Quarter review against the targets; written recommendation on direction (public consumer launch / freelancer and small-business tier / desktop pilot). ADR if it changes 0027. | C + F | open |
| CTO-T-31 | First pricing test. | F / C | open |
| CTO-T-32 | Plan for the following quarter. | C | open |

**Parked, with what would unpark them**

- Desktop build — a named design partner and the February decision.
- Offline / on-device mobile (ADR-0017 config B) — its own initiative; not before a hosted product has users.
- Bank connections — ADR-0026 requires a new ADR. File import (CSV, CAMT.053) is the cheaper next step if beta users ask.
- Postgres revision store — more than 2,000 active books or a second node (SPEC §2.2).
- SDK as a product for other people's agents (open core, MCP server) — worth a note after February; the eval suite is the proof asset.
- Small/local model scoring through `evals/` — when the on-premises configuration has a taker.
- Issue #1 (directed rounding in formulas) — an engine change; wait for a user who needs it.
- An index for `DECISIONS.md` — when a second person joins.

### 5. Questions for the founder

| # | Question | Blocks |
|---|---|---|
| CTO-Q-01 | Which email provider (EU, with a DPA), and can the hosting, DNS, storage and metrics accounts be created now? | CTO-T-11, T-12; the beta |
| CTO-Q-02 | Who are the first 20 users? Is there a waitlist anywhere? Is a landing page live, and is it capturing emails or posting to the placeholder? | CTO-T-09, T-20 |
| CTO-Q-03 | Is `design-purple.pen` the final direction? Can it and the other missing files be committed? | CTO-T-06, T-10 |
| CTO-Q-04 | Hours per week you can give CashKit this quarter; budget for a second engineer or an outside security review; runway. | Everything's pace; CTO-T-14 |
| CTO-Q-05 | Slow answers: stream (my recommendation), allow the one-call path for simple questions, or relax the 4 s target? | CTO-T-15 |
| CTO-Q-06 | Is there a real pilot customer behind the desktop design ("the pilot customer reads 1.400,00"), or is it a persona? | CTO-D-03, T-26 |
| CTO-Q-07 | Would you steer the beta toward freelancers and micro-businesses, given the engine's tax strengths and the landing page's own framing? | Recruitment mix; possibly ADR-0027 |
| CTO-Q-08 | Which legal entity is the data controller named in the privacy policy and terms? | CTO-T-18 |
| CTO-Q-09 | Apple developer enrolment: now, or after February? | CTO-T-24 |
| CTO-Q-10 | Any thinking on pricing and on the SDK's licence (currently proprietary)? | CTO-T-31 |
| CTO-Q-11 | May I add the model key to CI, and what monthly ceiling applies? (Nightly is about $9 a month.) | CTO-T-04 |

### 6. Things to check next time I open this

- Has anything in §5 been answered? The plan's dates assume answers to Q-01 to Q-04 in the first week.
- Is main green? If phase 1 slipped, do not start phase 2 work on top of a red main.
- Re-run the four commands in §1 before trusting any number in this file; they take under four minutes together.
- The proposed February targets are my starting points, not agreed thresholds. Agree them with the founder before the first invite, so the review cannot be argued after the fact.
