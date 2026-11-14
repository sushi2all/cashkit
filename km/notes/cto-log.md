# CTO log

Running log of considerations, decisions and tasks. Newest entry on top. Append, do not rewrite: a changed mind is a new entry that names the one it replaces.

- **Decisions** are numbered `CTO-D-nn`. They are mine to make and mine to reverse. Anything that touches a settled PRD §2 decision or an accepted ADR still goes through an ADR.
- **Tasks** are numbered `CTO-T-nn` and keep their number for life. Status: `open`, `doing`, `done`, `dropped`.
- **Questions** for the founder are numbered `CTO-Q-nn`.

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
