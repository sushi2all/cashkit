# Internal-first plan — 23 Nov 2026 to 14 Feb 2027

**Date** 2026-11-22 · **Decision** [ADR-0038](../adr/0038-internal-first.md) · **Replaces** the roadmap in [`2026-11-14-cto-assessment.md`](2026-11-14-cto-assessment.md) · **Working log** [`cto-log.md`](cto-log.md)

## The goal

**Progress Lab plans its cash on CashKit every week and no longer opens the spreadsheet.**

Nothing else is a goal this quarter. There is no public release date, no client data, and no new product surface. If it works for Progress Lab and stays there, that is a good outcome.

## How we will know it works

Proposed; to be agreed before step 2 starts (CTO-T-40).

- The last six months of Progress Lab's cash are reproduced from the book and compared with the bank statement, and every difference is explained in writing.
- The weekly cash review has been held on CashKit for six consecutive weeks without falling back to the spreadsheet.
- Someone at Progress Lab who did not build it has run the weekly update unaided at least twice.
- A restore from backup has been done on the real book, and the restored book gives the same figures.
- Every pipeline has been green on main for 30 consecutive days.

## Steps

| When | Step | What gets done | Done when |
|---|---|---|---|
| 23 Nov – 6 Dec | **1. A base we can trust** | Engine suite in CI and client pipeline fixed (both written on 22 Nov, waiting to be merged). Nightly model check given its key or paused. A mechanical guard on the engine version, so a saved forecast can always be reproduced. README corrected. In parallel, the founder gathers the inputs: six months of bank movements, and a list of what recurs (customers and terms, payroll, rent, tax dates, loans). | Main is green. The guard is in. The inputs exist. |
| 7 – 20 Dec | **2. The book** | Build the Progress Lab book through the SDK, in a private repository. Import the bank movements. Reproduce the six months. Write up every gap between the book and the bank, and what caused it. | The reproduction is done and the gaps are explained. |
| 21 Dec – 17 Jan | **3. Weekly use** | Hold the weekly cash review on CashKit. Deploy the app on a private network with backups; run one restore drill. Bring in the second person. Keep a friction log: every time the tool could not say something, was slow, or sent someone back to the spreadsheet. | Four weekly reviews held. Restore drill passed. Second person has done one update. |
| 18 Jan – 7 Feb | **4. Close the top gaps** | Take the three or four worst items from the friction log and fix them. Expected: VAT and payment terms in the app's vocabulary, and the service fixes that remove client workarounds. Each lifted exclusion gets its own decision record. | The friction log's top items are closed or consciously declined. |
| 8 – 14 Feb | **5. Review** | Check the list above. Decide what the next quarter is for: keep polishing for Progress Lab, extend to a first client, or stop here and just use it. | A written review and a plan. |

Christmas falls inside step 3. That is deliberate: it is the lightest step for building and the one that mostly needs the routine to exist.

## What is parked

Landing page, waitlist, analytics, pricing, native mobile builds, the visual restyle, the desktop build, the public compliance gate, the outside security review. Each comes back only if the direction changes.

Two conditions keep the parked security and compliance work safe to park. If either stops being true, that work comes first:

1. The service is not reachable from the public internet.
2. No one outside Progress Lab has a login, and no client's data is in it.

## What the founder is asked for

1. **Bank movements for the last six months**, and where they come from. If Progress Lab banks with the provider already connected to this workspace, they can be read directly once you say so.
2. **The second person**: who else at Progress Lab would run the weekly update.
3. **A pull request for the fixes made on 22 Nov**, so they reach main; and a yes or no on protecting main so every change is checked first.
4. **The nightly model check**: add its key (about $9 a month) or pause it.
5. **Where things live**: the private repository for the book, and the private machine or network for the app.
6. **Housekeeping**: commit the files the decision records cite and the repository lacks (`design-purple.pen`, `QUICKSTART.md`, the launch brief, the privacy note), and take down the "early access · autumn 2026" line on the landing page.
