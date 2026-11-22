# ADR-0038 — Internal first: CashKit serves Progress Lab before anyone else; public release has no date

**Date** 2026-11-22 · **Status** accepted · **Type** strategy · **Source** founder decision in the CTO session of 2026-11-22; considerations in `km/notes/cto-log.md` (entries 2026-11-15 and 2026-11-22)

## Context

ADR-0027 sequenced validation through a hosted consumer web app, with business tiers later. By mid-November the consumer app was built and tested and had never been deployed; the remaining steps to a public beta (email provider, hosting, compliance gate, security review, a waitlist) had been open for three months.

The founder's position, stated on 2026-11-22: CashKit was born for an internal need at Progress Lab. If it stays internal and works, that is an acceptable outcome. Go internal for now.

Progress Lab is a business. Its cash plan needs VAT settlement, invoices on payment terms, payroll and tax dates, and bank actuals. The engine models all of these. The consumer app does not: no file under `apps/service/cashkit_service` mentions VAT or withholding, and SPEC-mlp-consumer §1 excludes them by design.

## Decision

1. **The first and, for now, only user is Progress Lab.** The measure of success is that Progress Lab plans its cash on CashKit every week and no longer needs the spreadsheet. A public release is not a goal with a date. It is reconsidered only if the founder asks for it.
2. **The business persona comes first.** Where the consumer scope of ADR-0027 and SPEC-mlp-consumer §1 conflicts with what Progress Lab's own book needs, the book wins. Each exclusion that gets lifted in the app (VAT and payment terms are the likely first two) is its own ADR, driven by recorded friction from weekly use, not by anticipation.
3. **SDK first, app second.** The Progress Lab book is built and maintained through the SDK. The first acceptance test is the one `ERP-pilot-guide.md` §1 names: reproduce the last six months and compare with the bank statement. The app is deployed privately as the daily surface once that holds.
4. **The service is not reachable from the public internet while this ADR stands.** That condition is what allows the public compliance gate (SPEC §9), the outside security review and the login-cookie change (D-MLP-44) to wait. Exposing the service, or giving a login to anyone outside Progress Lab, reopens all three first.
5. **Client data is not internal data.** A client's figures do not enter CashKit under this ADR. If that is wanted later, it needs: a check of the engagement contract, a signed data-processing addendum, written confirmation of zero-retention model routing, and Progress Lab operating the tool on the client's behalf with no client login. That step gets its own ADR.
6. **The book itself lives outside this repository.** Progress Lab's financial data is never committed here; this repository holds the product.

## What this amends

- **ADR-0027**, decision 1 (consumer web app first): suspended. Decision 2 (single user per book) stands for now and is expected to chafe; the friction is recorded before anything is built.
- **ADR-0025** (positioning): untouched but dormant. There is no public message while there is no public product.
- **ADR-0026** rule 4 (no hosted or LLM-touching feature ships before a DPA and subprocessor list exist): unchanged in meaning. Nothing ships to an outside party under this ADR; rule 4 applies again at decision 5.
- **ADR-0014 / ADR-0017**: the local-first business track they describe is now the nearer one.

## Alternatives considered

- **Closed public beta of the consumer app by mid-December** (the CTO plan of 2026-11-14): superseded. It tested market demand for a product the founder does not need, ahead of the one he does.
- **Internal use plus client books this quarter**: deferred. A wrong forecast shown to a client costs Progress Lab's main business; the tool earns that trust on our own numbers first.
- **Keep both tracks alive**: rejected. One person cannot carry two products.

## Risks and implications

- **The builder is the user.** A founder using his own tool forgives what a stranger would not. A second person at Progress Lab who did not build it must run the weekly update unaided before "it works" is claimed.
- **Market risk is accepted, not reduced.** Internal use says nothing about whether strangers want this. If natural-language authoring is an open window (ADR-0025), time spent inside spends some of it. The founder has accepted that trade.
- **Progress Lab's own finances pass through the model router and the model** when the app is used. The founder can accept that for his own company; the written zero-retention confirmation (handoff-mlp-s6 §7.3) is still worth getting.
- **Real books raise the cost of a missed `ENGINE_VERSION` bump** from theoretical to actual. The mechanical guard (CTO-T-08) precedes any engine change.
- **Work parked by this ADR:** landing page, waitlist, analytics, pricing, native mobile builds, the visual restyle, the desktop build. The landing page's "early access · autumn 2026" line should be taken down, since nothing now stands behind it.

Related: [[0014-ui-delivery-post-v1]], [[0017-local-first-adoption-requirement]], [[0025-positioning-nl-authoring]], [[0026-pilot-ingestion-no-aggregator]], [[0027-consumer-first-validation]]
