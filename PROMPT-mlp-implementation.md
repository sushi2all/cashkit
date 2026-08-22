# CashKit MLP — implementation prompt

## Task

Build the CashKit MLP — hosted consumer web app + mobile app — as specified in `SPEC-mlp-consumer.md`, on the existing `cashkit` engine, in this repository under `apps/`.

You are the **orchestrator**. You do not implement sessions yourself: you spawn one fresh-context subagent per session defined in §Execution model, in strict sequence, and you independently verify each session's gates from committed evidence before launching the next. Work autonomously; do not ask for approval between sessions.

## Read first, every session

`SPEC-mlp-consumer.md` (the contract) · ADR-0022…0030 (`km/adr/`) · `km/notes/intent-schema-draft.md` (the 21 intents) · `QUICKSTART.md` (SDK surface) · `proto/TESTLOG.md` + `proto/llm.py` + `proto/server.py` (proven learnings: transport hardening, applier normalizations, thread confinement, results-for-Q&A). The proto is reference material, not a starting codebase: MLP code is written fresh under `apps/`, importing the lessons, not the files.

## Role and standard

You are building the first user-facing product on an engine whose whole value is that numbers are exact and explainable. The app must never launder a model guess into a fact: engine numbers pass through verbatim, provenance stays attached, and nothing mutates a book without a user-accepted proposal. Where convenience and these properties conflict, the properties win.

TypeScript strict mode in `apps/client`; full type annotations and Pydantic v2 in `apps/service`; OpenAPI is generated from the service and the TS client is generated from OpenAPI — neither is hand-written.

## Non-negotiable constraints

1. **The client never computes a money number.** It renders service-produced Decimal strings. No float arithmetic on amounts anywhere in `apps/client`.
2. **No mutation without an accepted proposal** (ADR-0029, SPEC §5-F2). There is no debug flag, admin path, or test shortcut that bypasses it in production code.
3. **Every computed figure ships with provenance** (`as_of`, scenario, revision, engine_version) and the UI has an element for it (SPEC §3, §6).
4. **WHAT-IF separation** (ADR-0024): the single rule is SPEC §2.4 — any figure not from base committed state carries the `what_if` payload field and the rendered stamp; the header shows base committed figures even while a fork is active. Use SPEC §2.4's wording verbatim wherever the rule is restated.
5. **Diagnostics pass through verbatim.** Never rewritten, summarized, or suppressed; never turned into advice (ADR-0015).
6. **Nothing under `cashkit/` changes in this track.** An engine gap is an escalation (write it to `DECISIONS.md`, work around at app layer), never an edit. R5/R6 are host-composed per SPEC §14.
7. **One kit, one thread, one lock per book** (SPEC §2.2). No kit instance crosses threads; model calls never hold the book lock.
8. **`as_of` is host-filled** (ADR-0019 rule 2). The model never supplies it; the engine never reads the clock.
9. **Model calls only via the hardened transport** (SPEC §2.3) and only server-side. The client has no model key and no model call.
10. **Compliance gate blocks exposure** (SPEC §9): no external user before the checklist is green.

## Anti-patterns to avoid explicitly

- Rebuilding the proto with more files. The proto proved the loop; the MLP is a product: auth, proposals, invariants, tests.
- A chat UI. There are no bubbles; there are quotes, receipts, and proposal cards (ADR-0023 structure, SPEC §6).
- Client-side "optimistic" application of proposals. The service applies; the client renders what came back.
- Free-form tool access for the model. The model's surface is the 21 intents plus the SPEC §2.3 read tools; raw SDK MUTATION verbs never appear in a prompt (ADR-0030). Host ops (SPEC §2.5) exist only on the UI→service path.
- Scaffolding for post-MLP features (multi-book, VAT, push, offline). The only sanctioned seams are listed in SPEC §14.
- Hand-editing generated API types.

## Execution model — sessions and gates

One fresh subagent per session, strict sequence, gates verified from committed evidence (tests in CI, not claims in prose). Push after each verified session. Subagent model: Opus 5 (`claude-opus-5`), the house convention from `PROMPT-fable5-implementation.md`.

### Orchestrator protocol

1. Spawn the session's subagent with a brief containing: `SPEC-mlp-consumer.md`, this file, `CLAUDE.md`, `km/adr/index.md`, the session's scope row, and the session protocol below. One session at a time — the sequence is a dependency chain.
2. When the subagent returns, verify independently before launching the next session: run the test suites and E2Es yourself; confirm one commit per gate exists; confirm `DECISIONS.md` (app-track section) is current; read the session's handoff note.
3. If verification fails, respawn the **same session** as a fresh subagent with the failure evidence in its brief. Do not patch implementation code in the orchestrator context — the repo must stay explainable by its own history.
4. The repository is the only channel between sessions. Never carry gate evidence forward in your own context; if the next session needs to know something, it belongs in the repo.

### Session protocol (goes verbatim into every subagent brief)

- **Start:** read the full §Read-first list of this file plus any `km/notes/handoff-mlp-*.md`. Re-run the existing app test suites before writing any code. A pre-existing failure ends the session immediately — report it to the orchestrator; never fix another session's work silently. Engine tests are not yours to run or touch.
- **Work:** the session's scope, gate-ordered. Commit at each gate with a message naming the session and gate.
- **End:** gate evidence committed; `DECISIONS.md` current; working tree clean; write `km/notes/handoff-mlp-s<N>.md` stating what was built, what the gates proved, and the first thing the next session should verify.

| Session | Scope | Gate (all must hold) |
|---|---|---|
| S1 | **Deterministic service core** (no model call anywhere): Postgres schema, magic-link auth, book lifecycle, engine wrapper endpoints (state, forecast, trace, why_zero, events, reconcile, scenarios, compare, save/discard, export), per-book locking, and the whole proposal machinery — applier, host ops, dry-run deltas, `POST /book/edits`, `POST /proposals/{id}` with §2.5 staleness/supersession, record-actual discriminator rule | Integration tests for every endpoint; a concurrency test proving thread confinement (no sqlite cross-thread error under parallel requests); every money figure and diagnostic in an endpoint payload string-equal to the canonically serialized value from the direct SDK call on the same book/revision/as_of (envelope fields excluded); deterministic invariants T13 (no unproposed/stale mutation — bypass and staleness attempts fail), T17 (correction scar), T18 (record-actual discriminator) green with zero model calls; OpenAPI schema published |
| S2 | **Model layer only**, on top of S1's proposal machinery: turn pipeline (interpret → guard → propose → verify), Q&A read loop (R1–R12 + `query_ledger`), transport hardening, turn persistence, cost guardrails | Ported trials T01–T12 green against the live service on `google/gemini-3.7-flash`; T14 (question turns never write); T13 re-run including turn-originated proposals; cost + rate limits demonstrably enforced |
| S3 | Client foundation: Expo monorepo (RN + web), generated API client, auth + deep link, voice-dictation adapters (native STT on mobile; web where the platform allows, per SPEC §9 speech rule), SPEC §6 screens 12 (Auth), 1 (Home/Chat), 2 (Alert variant), 4 (Proposal card), 3 (Forecast), 5 (Trace) | Playwright web E2E: auth → book (API-seeded via POST /books is acceptable here; the UI path is session S5's gate) → mutation turn → apply proposal → forecast → trace → save; Maestro iOS-simulator smoke of the same path including one dictated turn; zero client-side money arithmetic (lint rule + review) |
| S4 | SPEC §6 screens 6–11 and 15: Scenarios + compare, Actuals + corrections + cutover, Plan vs actual, the three Item screens, Settings + History; server-side evaluation of the two seeded alert-rule kinds rendering on the Home banner + read-only list in Settings; R10 diagnostics render on the Actuals/F5 surface | E2E for F4 and F5 paths; T15 (`what_if` field per SPEC §2.4, present and rendered); correction flow E2E shows the scar (original visible, note mandatory); R10 diagnostics render verbatim (string-equality test against `validate()` output) |
| S5 | SPEC §6 screens 13–14: xlsx import loop with SSE progress and reconciliation report, export (web download + mobile share sheet), NL first-book onboarding wizard | T16 import round-trips (T06/T07 workbooks) through API and UI with reconciliation report correct, including the non-empty-book fork rule; onboarding produces an applied proposal, never a silent book; import call-cap honored |
| S6 | Compliance + hardening + beta: §9 checklist, deletion/export, backups + restore drill, deploy (staging + prod on Hetzner EU), EAS/TestFlight build, dashboards | §9 checklist item-by-item with evidence; restore-from-backup executed once successfully; `DELETE /me` verifiably erases Postgres rows and the book directory; nightly trial run scheduled; staging→prod procedure documented |

Session boundaries are hard stops. If a session cannot proceed from the repository alone, that is a documentation gap in the SPEC — fix the SPEC (or file the question in `DECISIONS.md` if it is Luca's to answer, per SPEC §13), then continue.

## Deliverables

```
apps/
  service/            # FastAPI, agent layer, applier, stores wiring
    trials/           # ported T01–T12 + T13–T18 invariants (SPEC §10)
  client/             # Expo app: RN + react-native-web, one codebase
packages/
  api-types/          # generated from the service OpenAPI — never hand-edited
km/notes/handoff-mlp-s*.md
DECISIONS.md          # app-track section: every judgement call, with reasoning
BENCHMARKS.md         # app section: measured latencies vs SPEC §8 budgets
```

## Definition of done

Every session gate green, and additionally:

- The trial suite (T01–T18) passes against staging on the pinned model, and a nightly run is scheduled.
- SPEC §8 latency budgets measured on staging and recorded in `BENCHMARKS.md`; a budget miss is a finding to fix or a recorded decision, never silence.
- Mechanical enforcement in CI, not review habit: no float arithmetic on money and no model key in `apps/client` (lint); no raw SDK mutation verb in any prompt template (grep gate); `packages/api-types` regenerates clean (drift check).
- SPEC §9 compliance checklist complete with evidence; restore-from-backup executed once.
- All session handoff notes exist; the orchestrator has re-run everything green after S6.

## When you hit ambiguity

Applies to every session subagent. The SPEC will not cover everything. When it does not:

1. Choose the option that preserves exactness and provenance (engine numbers verbatim, stamps intact).
2. Choose the option that keeps every mutation behind an applied proposal.
3. Choose the option that surfaces a diagnostic or clarification over the one that guesses.
4. Write the choice and its reasoning into `DECISIONS.md` (app-track) immediately.
5. Continue. Do not stall for clarification — except on SPEC §13 items, which are Luca's; file those and proceed on the rest.

If the SPEC contradicts an ADR, the ADR wins; record the conflict in `DECISIONS.md` under `## SPEC conflicts`.

## Commit rules

Message names the session and scope. No `Co-Authored-By` trailers of any kind (see `CLAUDE.md`). Never force-push.
