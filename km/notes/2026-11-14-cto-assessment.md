# CTO assessment — state of CashKit, SWOT, and the plan to mid-February

**Date** 2026-11-14 · **Covers** the quarter 16 Nov 2026 – 14 Feb 2027 · **Working log** [`cto-log.md`](cto-log.md) (evidence, decisions, backlog, open questions)

> **Superseded in part, 2026-11-22.** The founder chose to keep CashKit internal for now ([ADR-0038](../adr/0038-internal-first.md)). The state table and the SWOT below still describe the repository as it was on 14 Nov. The roadmap, the proposed targets and most of the founder decisions do not apply; the current plan is [`2026-11-22-internal-first-plan.md`](2026-11-22-internal-first-plan.md).

## Bottom line

CashKit is further along technically than "an SDK and some designs", and further behind commercially than that label suggests.

- **What exists:** a finished, well-tested calculation engine and SDK; a complete consumer app (service plus one web/mobile client) that passes its tests; a compliance and operations kit; a desktop design for finance controllers; a landing page.
- **What does not exist:** a deployment, a way for an outside person to sign in, a single user, or any measurement of whether people want this.

The engineering risk is low and the market risk is untouched. Work since the start of October has widened the product (more designs, a new landing page, an evaluation suite) and has not moved it closer to a user. The quarter's job is to reverse that: **put the existing consumer web app in front of real people by mid-December, measure it, and decide in February what to build next.** No new surface gets built until then.

## Where things stand

| Area | State | Evidence |
|---|---|---|
| Engine and SDK | Done to v1. Sound. | 1,131 tests pass, 96.6% coverage (gate is 90%), performance budgets met. Re-run today. |
| Consumer service (API, agent layer) | Built, never deployed. | 503 tests pass, re-run today. Live-model trials and infrastructure drills not re-run (need a model key and Docker). |
| Consumer client (web + mobile, 12 screens) | Web works end to end. Mobile has never run on a device. | 32 unit tests pass today; 49 browser tests were green in CI on 1 Oct. No Android or iOS build has ever completed. |
| Compliance and ops | Written and tested; the launch checklist is "not green". | 11 of 13 items done. Open: no email provider, nothing deployed. |
| Desktop app (controllers, CFOs) | Design only: 28 frames and a requirements document. | Depends on 12 SDK and service features that do not exist yet. |
| Landing page | Built. Sign-up form posts to a placeholder address. | `site/index.html` and `site/v5.html`. Whether the live site captures emails is unverified. |
| Automated checks (CI) | Do not reflect reality. | See Weaknesses. |

## SWOT

**Strengths**

- **The numbers can be trusted, and that is provable.** Exact integer arithmetic, a second independent engine that must agree to the cent, and every figure traceable to its inputs.
- **The AI never does the math and never writes without consent.** Every change is a card the user confirms; tests try every bypass path. This is the right answer to "AI gets numbers wrong".
- **Decisions are written down.** 37 architecture records and about 300 logged decisions. Handoff notes name what failed instead of rounding up.
- **More is built than the MVP label says.** The consumer app, account deletion that verifiably erases, backups with a tested restore, alarms, a privacy policy checked against the code.
- **Cheap to run.** The pinned model handles the full behaviour suite for about $0.30; small models write correct SDK calls once given the reference document.

**Weaknesses**

- **No users, no deployment, no market evidence.** Nothing in the repository says anyone outside has tried it.
- **One person.** Every commit is the founder's or an AI session's. There has never been a pull request. Almost all of the app layer (about 28,000 lines) was written in two days in August by agent sessions; how much a human has read is unknown.
- **The automated checks are not telling the truth.**
  - The engine's own test suite is not run by any pipeline.
  - The client pipeline has failed on every run inspected (the last four of seven). One early step fails, so type checks, lint and unit tests are skipped, which hides a real lint failure on the current code.
  - The nightly model check has failed on each of the 25 most recent nights (82 runs in total): no model key was ever configured. It has never actually run in CI.
- **Too many directions for one person.** Three customer types (consumer, small business, enterprise controller), three surfaces (web, mobile, desktop), and four visual styles. The style recorded as chosen (`design-purple.pen`) is not in the repository, nor are several strategy notes the decision records cite.
- **Known gaps before a first user:** no email sender, the web login token is stored where a script injection could read it, answers take 8 seconds against a 4-second target, import jobs die on restart.
- **The consumer product leaves out what the engine is best at** (VAT, payment terms, withholding) **and what consumer budgeting apps treat as standard** (bank connection).

**Opportunities**

- **Building a budget by describing it** was found to be an open gap in the founder's August research. Not re-verified here.
- **Italian freelancers and micro-businesses.** VAT regimes, withholding and tax-payment schedules are already native to the engine, and the landing page already speaks to them ("Freelancer", "VAT set-aside"). The founder has an existing B2B channel.
- **The SDK as a tool for other people's AI agents.** The evaluation suite is ready-made proof that models can drive it accurately.
- **Privacy-sensitive businesses** that will not send finances to a cloud: an on-premises setup needs no engine change.
- **The desktop design can be sold before it is built.** Showing the 28 frames to a handful of controllers costs days, not months.

**Threats**

- **Incumbents adding a chat layer.** Budgeting apps, business banks and spreadsheet assistants can all add natural-language entry. The window is open now and will not stay open. Needs a proper competitive review; assumed, not checked.
- **Retention without bank sync.** Manual entry is where consumer budgeting apps lose people.
- **Dependence on one model through one router.** The privacy claim (no retention of prompts) rests on a contract that could not be independently verified.
- **Trust is fragile.** Financial data under GDPR, no lawyer review yet, and one wrong number in a public screenshot would be very costly.
- **Momentum.** Five commits since 1 October (a design file, a landing page, a test fix, the evaluation suite and its README), none of them on the app. The landing page promises "early access, autumn 2026", which lapses in five weeks. The founder also runs another business.

## Roadmap, 16 Nov 2026 – 14 Feb 2027

One goal: **real users on the consumer web app, and a decision backed by their behaviour.**

| When | Phase | What gets done | Done when |
|---|---|---|---|
| 16–29 Nov | **1. Make the checks honest** | Engine suite in CI; client pipeline fixed and the lint failure resolved; nightly given its key or paused; changes go through pull requests with required checks. Missing design and strategy files committed. One visual direction frozen. Landing form wired to a real list, with privacy-friendly analytics. README status corrected. | Main is green on all pipelines. The repository contains everything the decisions cite. A sign-up on the landing page lands in a list. |
| 30 Nov – 20 Dec | **2. Closed beta live** | Email provider chosen and wired. Staging and production deployed in the EU. Login moved to a protected cookie. Security review of sign-in, per-user isolation and the spreadsheet import. Answers streamed so the wait feels short. Usage metrics visible (see below). Compliance checklist green. First 10–20 invited users, web only. | An outside person signs in, builds a budget and gets a forecast, on production. Checklist reads "green". |
| 21 Dec – 31 Jan | **3. Learn and harden** | Invite in weekly batches toward 50–100 users. Fix the top friction each week. Restore drill on production data. Second model qualified as a fallback. First Android and iOS test builds. Five to eight interviews with freelancers; three to five controllers shown the desktop frames. Lawyer hour. | Weekly numbers exist for four consecutive weeks. A restore from a production backup has succeeded. |
| 1–14 Feb | **4. Decide** | Review the numbers against the thresholds below. Choose one direction for the next quarter: public consumer launch, a freelancer/small-business tier, or a desktop pilot. First pricing test. | A written decision with its evidence, and a plan for the following quarter. |

**Deliberately not this quarter:** building the desktop app, offline or on-device mobile, bank connections, shared books, the database-backed storage rewrite, model fine-tuning. Each has a recorded trigger or waits for the February decision.

**Proposed targets for the February review** (starting points to agree, not benchmarks):

- 50 or more outside users who built a budget and confirmed at least one change
- at least 70% of proposed changes accepted as drafted
- at least 25% of activated users still active in their fourth week
- zero incidents of a wrong computed figure
- every pipeline green on main for 30 consecutive days

## Decisions needed from the founder

These block the plan and are not mine to make. Details and options are in the log.

1. **Email provider and hosting account** (EU, with a data-processing agreement). Blocks the beta outright.
2. **Who the first 20 users are**, and whether the beta leans toward freelancers. Also: is the live landing page collecting emails today?
3. **The visual direction.** Confirm `design-purple.pen` is final and commit it.
4. **Time and money.** How many hours a week the founder can give this, and whether there is budget for a second engineer or a part-time security reviewer.
5. **Slow answers:** stream them (my recommendation) or relax the target.
6. **Is there a real pilot customer** behind the enterprise design, or is it speculative?
