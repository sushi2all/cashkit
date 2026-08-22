# SPEC — CashKit MLP: consumer web app + mobile app

**Version** 0.1 · **Date** 2026-08-22 · **Status** draft for implementation
**Audience**: the implementing agent(s). Read with: ADR-0022…0030, `km/notes/intent-schema-draft.md`, `QUICKSTART.md`, `proto/TESTLOG.md`.

This document specifies the MLP defined by ADR-0022 (five features), delivered per ADR-0027 (hosted consumer app first, single user per book) on the execution model of ADR-0028/0029/0030. Styling is out of scope here; ADR-0023/0024 define the visual identity separately. Screen sections give structure and elements only.

---

## 1. Product scope

One sentence: **you describe your money in plain words; a deterministic engine turns it into an exact, explainable forecast.**

The five MLP features (ADR-0022), identical on web and mobile:

| # | Feature | Intents |
|---|---|---|
| F1 | Ask about cash; as-of date always visible | R1–R6 |
| F2 | Say what changed; typed confirmation card before EVERY write | M1–M5, M9 |
| F3 | Forecast at a glance; tap any number to trace it | R7–R8 |
| F4 | What-if: fork and compare scenarios | M7, R9 |
| F5 | Actuals with append-only corrections and validate() diagnostics | M5 (actual channel, §5-F5), M6, M8, R10 |

Explicitly OUT of the MLP (ADR-0022 + this spec): bank sync (ADR-0026), budgets-and-advice (ADR-0015), formula authoring, multi-user and shared books, VAT, multi-currency (EUR only), multiple books per user, push notifications (alerts render in-app), TTS voice output (input dictation only), offline mobile (ADR-0017 config B is post-v1).

## 2. Architecture

### 2.1 Topology

```
apps/client   (one Expo codebase → iOS/Android app + web app via react-native-web)
      │  HTTPS/JSON + SSE (OpenAPI-generated TS client)
apps/service  (FastAPI, Python ≥3.11, EU region)
      ├─ cashkit SDK in-process (one book dir per user, existing file stores)
      ├─ agent layer (OpenRouter, flash-class, staged harness per ADR-0030)
      └─ Postgres (users, sessions, turns, proposals — never book content)
```

- **Shared**: everything the user sees is one codebase (`apps/client`); all business logic, the agent layer, and all computation live in `apps/service`. **The client never computes a money number.** It renders Decimal strings the service produced, verbatim.
- **Dedicated (platform adapters only)**: voice dictation (native speech-to-text on mobile, Web Speech API on web), file pick/save for import/export (web-primary; mobile uses the OS share sheet), secure token storage (SecureStore vs cookie/localStorage), deep links for magic-link auth.
- Monorepo: this repo, `apps/service/` + `apps/client/` + `packages/api-types/` (generated from the service's OpenAPI schema; regenerating it is a build step, never hand-edited). `cashkit/` remains the engine and keeps its non-negotiables — nothing under `cashkit/` gains an LLM or server dependency (ADR-0016/0021).

### 2.2 Book storage

- One directory per user on a persistent volume, managed by the existing file stores (YAML+git revisions, SQLite ledger). One writer per book (ADR-0027) makes this safe: the service serializes writes per book.
- **Concurrency rule** (proto finding, `km/notes/2026-08-22-proto-webapp-findings.md` §4): a kit instance is thread-bound. The service keeps one asyncio lock per book id and opens/uses/closes the kit inside the request task; no kit crosses threads. Long agent turns hold the lock only during apply, not during model calls.
- The Postgres-backed revision store (ADR-0018 seam, anticipated by ADR-0027) is **deferred**. Trigger to build it: >2,000 active books, or a second service node, whichever comes first. The API layer must not leak storage details so the swap stays invisible. This deferral amends ADR-0027's stated hosted architecture and is recorded in `DECISIONS.md` (app-track section).
- Backups: nightly volume snapshot + `git bundle`/SQLite copy per book to S3-compatible object storage, EU.

### 2.3 Agent layer (ADR-0028/0029/0030)

Model: `google/gemini-3.7-flash` via OpenRouter for every turn (ADR-0028). Zero-retention routing required; the model provider appears on the subprocessor list (§9). Transport hardening is mandatory and already proven in `proto/llm.py`: JSON response format, first-object `raw_decode`, bracket-stack repair, temperature bump on repair retries. Any model change must pass the ported trial suite (§10) first.

The turn pipeline (server-side, per ADR-0030):

1. **Interpret** (1 call): system prompt = intent grammar + compact book state; output = `{reply, intents[]}` where `intents` conform to the 21-intent schema (`km/notes/intent-schema-draft.md`). `as_of` is host-filled (ADR-0019 rule 2). The two `[SDK gap]` intents (R5 `top_categories`, R6 `item_total`) are composed host-side from `frame()` — the model still emits one intent; composition never reaches the model.
2. **Guard** (ADR-0029, structural): read intents execute immediately. Mutation intents are NEVER auto-applied: the service dry-runs them on a scratch overlay, computes deltas (closing balance, min cash, runway, affected items), and returns a **proposal**. Feature F2 makes this universal: every write gets a typed confirmation card.
3. **Confirm**: the client posts accept/discard for the proposal id. On accept the service applies the intents to the working overlay of the active scenario (ADR-0013: nothing writes base directly; M9 `save` = `commit()`).
4. **Verify** (triggering intents, enumerated: M3, M4, and any M1/M2 carrying escalation): one bounded call with the instruction, the applied intents, and `trace()` receipts; output is confirmation or corrective intents (which become a new proposal). Diagnostics from the SDK feed at most one repair round, as in the proto. (ADR-0030's formula-bearing class is vacuous in the MLP — formula authoring is out of scope — so macros and escalation are the verification triggers here.)
5. **Q&A tool loop** (read turns that need more than the state snapshot): up to 4 read-only calls. The exact tool surface is `R1–R12` plus one host tool `query_ledger` (wrapping `query_events`, per ADR-0030 stage 3; R7/R8 already carry `trace`/`why_zero`). No mutation verb ever appears in a prompt. The model quotes engine numbers, never derives them.
6. **Import** is the only free-running loop (§7): author a section, run, reconcile against the sheet's own subtotal rows, investigate mismatches with `trace()`, repeat; hard cap 20 model calls per import.

Every turn is logged (§11) with intents, model, tokens, cost, latency, and outcome.

### 2.4 State, drafts, and revisions

- All mutations land in the **working overlay** of the active scenario. The book header shows a dirty indicator whenever `status().clean == false`.
- **Save** (M9) commits with a message; **Discard** reloads from HEAD. History (R12) lists revisions read-only; time travel UIs beyond the list are post-MLP.
- **Active scenario is app state**: `books.active_scenario` in Postgres, set by the activate endpoint, effective book-wide (every device) on the next request. An explicit `?scenario=` query parameter overrides it for that read only; turns with no `scenario` use the active one.
- **The WHAT-IF rule (single definition; §5-F4, §10-T15 and the PROMPT reference this paragraph).** Base is the plan of record. Any figure NOT from the committed state of `base` — a non-base scenario (active or not), a throwaway overlay, or a dry-run including pending changes — carries the WHAT-IF stamp: payload field `what_if: {stamped: true, reason: "scenario"|"overlay"|"pending", scenario?: id}`, and a rendered stamp element (ADR-0024). The Home header and sparkline always show base committed figures, in neutral form, even while a fork is active; a fork's own figures render stamped with the fork's name.

### 2.5 Proposals: contents, origins, staleness

- A proposal's payload is a list of **operations**: model **intents** (the 21-intent schema) and/or **host ops**. Host ops are typed, enumerated, and NEVER appear in a model prompt: `set_horizon`, `set_opening_balance`, `remove_event` (refused on actuals — corrections only), `edit_schedule_date` (add/change/remove one explicit date on a schedule item), and the record-actual channel of §5-F5. This mirrors the R5/R6 host-composition precedent: the model's surface stays the 21 intents; the UI's surface is intents + host ops, one proposal pipeline for both.
- Proposal **origins**: `turn | cell_edit | onboarding | import | settings | button`. Non-turn origins are created by `POST /book/edits` (§3); `turn_id` is null for them.
- **Staleness**: every proposal stores the revision id and a working-overlay fingerprint it was dry-run against. Accept re-checks both; on mismatch the service re-runs the dry-run and returns a **refreshed proposal** (old one becomes `superseded`) for re-confirmation. Save, Discard, scenario activation, and any accept mark all other pending proposals `superseded`. Time expiry stays at 15 minutes. The card the user confirms is always the card that applies — ADR-0029's guarantee.

## 3. Service API (v1 sketch — the implementing agent owns the exact OpenAPI)

Auth: email magic link. `POST /auth/link {email}` → mail with deep link; `POST /auth/verify {token}` → bearer session. Deep-link mechanics: a custom URL scheme in development builds (the associated-domains entitlement needs the paid Apple Developer account); universal links arrive with the TestFlight track. The web app uses a plain HTTPS link throughout. Web may exchange for an httpOnly cookie; mobile stores the bearer in SecureStore. Policy: link tokens are single-use, TTL 15 minutes; sessions 30 days (mobile) / 7 days (web) with sliding renewal; a new session does not revoke others; `DELETE /me` revokes all sessions.

| Endpoint | Purpose |
|---|---|
| `GET /me` / `DELETE /me` | Profile; full account deletion (books + rows, §9) |
| `GET /me/export` | Everything the user owns, one archive (GDPR) |
| `POST /books` | Create the (single) book: horizon, opening balance, currency=EUR, grain=month |
| `GET /book/state?scenario=` | Items, params, summary, months, per-item series, dirty flag, revision id, as_of, and server-computed `warnings` (projected negative months with depth; projected min cash + month) |
| `GET /book/forecast?scenario=&grain=&window=` | Grid payload for F3 |
| `GET /book/trace?item=&period=&measure=` | `trace()` for the tap-to-explain screen (R7) |
| `GET /book/why_zero?item=&period=` | R8 |
| `GET /book/events?where=&since=&until=` | Ledger view (F5) |
| `GET /book/reconcile?until=&scenario=` | Per-item forecast/actual/drift + settle status + `suggested_cutover` (F5, S8) |
| `GET /book/validate?scenario=` | R10: `validate()` model-consistency diagnostics, verbatim (F5, S7) |
| `GET /book/history?limit=` | R12: the read-only revision list (S15) |
| `POST /turns {text, scenario?, context?}` | The chat turn; `context: "actuals_record"` marks the record-actual channel (§5-F5); returns `{kind: answer\|proposal\|clarification, reply, receipts[], proposal?}` |
| `POST /book/edits {ops[], origin}` | UI-origin proposals (cell edits, settings, onboarding accept-step, "+ Add a date"); same dry-run pipeline, no model call |
| `POST /proposals/{id} {action: accept\|discard}` | ADR-0029 confirmation; accept re-checks the §2.5 staleness fingerprint, then returns fresh state + receipts, or a refreshed proposal on mismatch |
| `POST /book/save {message}` / `POST /book/discard` | M9 / revert working overlay |
| `GET /book/scenarios` · `POST /book/scenarios` · `POST /book/scenarios/{id}/activate` | F4. `POST /book/scenarios` returns a **proposal** carrying M7, not a scenario — ADR-0029 has no exception for a change that looks harmless. Activation is app state, not book content, and supersedes every pending proposal (§2.5) |
| `GET /book/compare?scenarios=a,b&metric=cash` | R9 payload |
| `POST /import` (xlsx, multipart) → `GET /imports/{id}/stream` (SSE) | §7; progress + reconciliation report |
| `GET /export?mode=ledger\|budget&months=&start=` | xlsx, as in the proto |

Response invariants: every payload that carries a computed number also carries `as_of`, `scenario`, `revision`, `engine_version`, and the `what_if` field of §2.4 (absent or `{stamped:false}` only for base committed figures). Money is Decimal strings, never floats: one canonical serializer ships every figure as `{exact, display}` — `exact` is the engine's lossless 4dp value, `display` the 2dp string for rendering, rounded the way the engine rounds. Sending only 2dp would truncate an engine number; sending only 4dp would make the client round one. Both invariants hold with both forms on the wire (D-MLP-06). Diagnostics are passed through verbatim (code, severity, message, suggested_fix) — the service never rewrites or suppresses them (ADR-0015).

## 4. Data model (Postgres — app data only, never book content)

- `users(id, email, created_at, deleted_at)`
- `sessions(id, user_id, token_hash, platform, expires_at, created_at, last_seen_at)`
- `login_tokens(id, email, token_hash, expires_at, consumed_at, created_at)` — magic-link tokens; §3's single-use rule is not enforceable without a row to burn (D-MLP-07)
- `books(id, user_id UNIQUE, storage_path, active_scenario, created_at)` — UNIQUE enforces one book per user
- `turns(id, user_id, book_id NOT NULL, request_id, input_text, kind, context, intents jsonb, model, prompt_tokens, completion_tokens, cost, latency_ms, outcome, diagnostics jsonb, created_at)` — per-turn aggregates
- `llm_calls(id, turn_id, seq, purpose interpret|repair|verify|qa|import, request jsonb, response jsonb, prompt_tokens, completion_tokens, cost, latency_ms, error, created_at)` — one row per model call; `request`/`response` carry the raw payloads and are **purged after 30 days** (they contain user financial data, §9); the numeric columns are kept. `DELETE /me` cascades through both tables.
- `proposals(id, book_id NOT NULL, turn_id NULLABLE, origin, context, scenario, ops jsonb, deltas jsonb, base_revision, overlay_fingerprint, status pending|accepted|discarded|expired|superseded, supersedes NULLABLE, expires_at, created_at, resolved_at)` — see §2.5 for origins, host ops, and staleness rules. `book_id` because supersession is per book; `context` carries the §5-F5 record-actual marker; `supersedes` links a refreshed card to the stale one it replaced (D-MLP-08)
- `import_jobs(id, book_id, status, report jsonb)`

## 5. Feature specifications

### F1 — Ask about cash
- Input: text or dictation into the ask bar; sent as a turn.
- Read turns answer inline as a receipt: the number(s), the as-of stamp, scenario, and a "how" affordance that deep-links to trace where applicable (R7).
- The model must quote engine numbers only (ADR-0030 stage 3). Acceptance: the T11-class trials pass through the service (§10).
- Clarification turns (`kind: clarification`) render the model's question; no state changes.
- **Voice rule for refusals and clarifications** (Luca, 2026-08-23): helpful and explanatory, but succinct — say what happened and what is needed, at most two short sentences; no apology boilerplate, no hedging.

### F2 — Say what changed (confirmation card)
- Every mutation turn returns a proposal. The card lists: each intent in typed, human-readable form ("Add expense · Rent · 900/month from 2026-03-01"); the dry-run deltas (closing balance, min cash, runway before → after); affected items; diagnostics if any.
- Actions, one vocabulary everywhere (card, API, tests): **Apply** (= API `accept`), **Discard** (= API `discard`), **Edit** (= a new turn quoting the proposal; the old proposal becomes superseded).
- **Crossing flags, computed in the dry-run** (decided 2026-08-23, supersedes threshold alerts): the deltas block marks every month the change turns negative and the min-cash movement (before → after). Warnings are structural and always on — there are no configurable thresholds in the MLP; nothing waits for a background job, every update checks immediately.
- Acceptance: no path exists where a model output mutates a book without a stored, user-accepted proposal (ADR-0029). This is a hard invariant test, not a UX nicety.

### F3 — Forecast + trace
- The MLP forecast is the designed monthly view (S3): chart + MONTH/IN/OUT/END table with a summary strip. There is no item×month grid in the MLP (web-only later enhancement).
- **The trace-row path is the MLP carrier of the ADR-0013 cell taxonomy**: tap a month row → month-scoped Trace (S5) → each contributing row (an item or event in a period) carries the taxonomy-appropriate affordance:
  - actual event → read-only + "record a correction" (M6, mandatory note; original shown struck with the correction linked — the scar is required structure);
  - forecast/committed event → edit amount in place (becomes a proposal via `POST /book/edits`);
  - generated (segment-backed) row → arithmetic shown (`amount × escalation × probability`); edit routes to the segment input (M2) or "convert to one-off" (M5);
  - derived row → read-only, bindings shown, navigate upstream.
- Zero/absent figures → `why_zero` explanation (R8).

### F4 — What-if
- Scenario list (base + forks), create = M7 via turn or button, activate switches the working context.
- Compare view: two columns of the same metric (R9), per period, with a delta column. Absent-vs-zero distinction preserved (`None` ≠ 0, engine contract).
- ADR-0024 invariants: the WHAT-IF rule of §2.4 applies verbatim (single definition — non-base or overlay figures are stamped; the header shows base committed figures even while a fork is active); Apply markers appear only where a change is real.

### F5 — Actuals
- Reconcile view over `GET /book/reconcile` (wrapping `reconcile(until)`): per item, forecast vs actual vs drift; bars encode percent-of-plan with a 100% tick (decision from the 2026-08-19 session), amounts stay on the row; unsettled = empty track, never a fake bar.
- **Record actual — the discriminator rule** (host-side extension of M5, recorded in §14; the model NEVER chooses status): an M5 intent maps to `status="actual"` if and only if the turn arrived with `context: "actuals_record"` (set by the client only on the Actuals record flow, §6-S7) AND the event date is ≤ `as_of`. A future-dated entry on that flow, or any M5 from any other surface, stays `forecast`. If the flow is `actuals_record` and the date is ambiguous or missing, the turn returns `kind: clarification` — never a guess.
- Corrections: M6 only, note mandatory, append-only scar UI (ADR-0012/0013).
- Cutover: after a reconcile, offer `suggested_cutover` as an M8 proposal.
- Diagnostics: R10 renders `validate()` info diagnostics verbatim — these are model-consistency diagnostics; the engine has no domain-coverage checks (ADR-0021 removed them). The ADR-0021 app-layer domain-coverage duty is explicitly deferred for the consumer MLP (§14) — the consumer persona has no tax-mechanics scope. No advice framing (ADR-0015).

## 6. Screens (structure and elements only — styling is ADR-0023's domain)

Shared inventory rules: every screen with computed figures shows the **as-of stamp** (eyebrow or subline) and **active scenario**; every screen with uncommitted changes shows the **dirty indicator + Save/Discard**; provenance stamps (item/event ids, commit hashes, engine version) are elements, not decoration; leader-dot receipt rows are the universal list pattern; no chat bubbles — user words render as a quote block, answers as receipt cards (ADR-0023 structure). The ask/mic input bar appears on Home, Alert variant, Actuals and Plan-vs-Actual; Forecast and Scenarios keep the mic only.

**Design reference**: `design.pen` at revision `9bdb617` (self-contained; ten top-level screens, inventoried 2026-08-22). They cover **S1–S11** below — S4's proposal card exists as the "Pending Card" nested inside the Home screen, and the three item variants are separate screens. **S12–S15 have no design.pen screens**; for them the element inventories in this section are normative-enough to build — the ADR-0023 styling pass is a separate, non-blocking workstream for all fifteen. The web app reuses the same structures with a sidebar in place of the tab bar and wider table layouts.

One adaptation: the designed `ON-DEVICE` chip on Home assumed ADR-0017 config B; the hosted MLP **drops the chip entirely** (decided 2026-08-23) — the eyebrow keeps book · scenario · as-of only. Security/trust badging returns with the public-launch trust work.

1. **Home / Chat** *(design.pen "Home - Chat")* — eyebrow stamp (book name · scenario · as-of); **warnings banner element** (renders the state payload's standing `warnings` — projected negative months, min cash — hidden when clear); balance row (big committed figure + "today"); horizon sparkline with low-point dot and labels (`NEXT 6 MONTHS`, `LOW <amount> · <date>`); divider; chat stack — user quote row, **answer card** (verdict line; leader-dot rows for the key figures; footer stamp `WHAT-IF · INCLUDES PENDING <x>` when hypothetical, ADR-0024; `TRACE ›` link), **proposal card** (see S4); ask input row (text + mic).
2. **Alert / negative what-if** *(design.pen "Alert - Negative What-if"; a Home state variant, not a separate route)* — same shell; answer card with negative verdict, negative figures, footer stamp carrying the engine diagnostic code verbatim (e.g. `WHAT-IF · CK-… · NEGATIVE CASH`); actions **Discard / Keep as scenario** instead of Edit/Apply. "Keep as scenario" composes host-side, with NO new model call: one proposal containing M7 (fork) plus the host-derived M-intents equivalent to the R1 hypothetical delta (e.g. M5 with the delta amount/date), dry-run on the fork, confirmed through the normal card.
3. **Forecast** *(design.pen "Forecast")* — header (title + scenario chip); subline stamp (window · as-of); **chart card**: recorded band, cutover line, computed area, min drop + dot, `RECORDED` / `COMPUTED · CUTOVER <date>` labels; **table card**: MONTH / IN / OUT / END rows with inline notes on notable months (min, applied one-offs); footer note `ALL FIGURES COMPUTED · TAP A ROW FOR THE TRACE` + mic. Tap a row → Trace scoped to that month. (An item-level grid is a web-only later enhancement; the designed monthly table is the MLP forecast.)
4. **Proposal card** *(design.pen "Pending Card", componentized; used on Home, onboarding, import)* — label `PENDING · <OPERATION>`; leader-dot rows describing the change (name, kind, date, tags, amount); dry-run deltas block (closing, min cash, runway before → after); diagnostics if any; **Apply / Edit / Discard** actions (§5-F2 vocabulary; the design shows Edit/Apply — Discard is an added required action); resolved state renders an applied/discarded/superseded badge; expiry note.
5. **Trace / Explain** *(design.pen "Trace")* — top bar (back + `TRACE` crumb); the question as text ("Why <amount> on <date>?"); subline stamp (scenario · period · as-of); **receipt card**: opening row, one leader-dot row per contributing item/event with a meta id line (`item:rent · monthly`, `event:one-off · travel`), divider, total row; narrative paragraph (from trace steps, rendered — not model-generated); **engine panel**: `ENGINE <version> · DETERMINISTIC`, `ROUNDING: CANONICAL ORDER · 4DP`, `BOOK REVISION <hash>`; **Reproduce** button (maps to `reproduce()`, shows reproduced/engine-moved verdict). Why-zero variant: cause + suggested fix verbatim (R8). Edit affordances follow the ADR-0013 cell taxonomy.
6. **Scenarios** *(design.pen "Scenarios")* — header; subline stamp `COMPARE · SAME BOOK · AS-OF <date>`; scenario chips (check = selected for compare, `+ New`); **compare chart card**: legend, recorded band, zero line, negative region, cutover line, one curve per scenario, diverge dot + label (`DIVERGE <date> · <cause>`); **compare table**: MONTH / <A> END / <B> END rows, inline note on first-negative month with the diagnostic code; footer note + mic. WHAT-IF stamps on every non-base figure (ADR-0024).
7. **Actuals** *(design.pen "Actuals")* — header (title + month chip); subline stamp `CUTOVER <date> · LEDGER AUTHORITATIVE`; **recorded card** (`RECORDED · <window>`): ledger rows with per-row source meta (`ledger · import bank`, `ledger · voice`), signed amounts, and — where corrected — an inline **correction annotation** (`↳ corrected <date> · was <amount> · note: <note>`; original visible, append-only per ADR-0012); divider; `Balance today` total row; **computed card** (`STILL COMPUTED · <window>`): forecast rows with item-id metas; month-end row; ask input row (correction-example placeholder + mic). Record-actual and corrections flow through proposals like every write; correction notes are mandatory.
8. **Plan vs actual** *(design.pen "Plan vs Actual")* — header (title + month chip); subline stamp (`FORECAST <rev> · ACTUALS <window> · Δ = ACT − FC`); view toggle `By category / By item`; **compare card**: PLAN and ACTUAL summary bars; per group: category header row with subtotal, item rows each with amount, **bar zone** (track, fill = % of plan, plan tick at 100%), meta row (`plan <amount> · <recurrence>` + delta badge `ON PLAN / <n> OVER / <n> UNDER / NOT SETTLED`); unsettled rows show an empty track, never a fake bar; total row (`<month> to date · <delta> vs plan`); legend line (`BAR = % OF PLAN · TICK = PLAN · TAP A ROW TO TRACE`); ask input row.
9. **Item — recurring** *(design.pen "Item - Recurring")* — top bar (back + `ITEM` crumb); title + tag chip; subline stamp (`item:<id> · SCENARIO <s> · AS-OF <date>`); type selector RECURRING / ONE-OFF / CUSTOM (navigational between the three variants, not a mutator); **rule card**: amount, repeats, starts, ends, escalation rows + next-occurrences line; **segments card** (`SEGMENTS · CHANGES KEEP HISTORY`): one row per segment with window, `seg:<n>` meta, amount — history never collapses; **"Change amount from a date…"** button (pre-fills the ask bar → M2 proposal); **provenance panel**: item id, created commit + date, last-change commit + date.
10. **Item — one-off** *(design.pen "Item - One-off")* — same shell with `EVENT` crumb; subline stamp with `evt:<id>`; **event card**: amount, date, direction, note rows; **status card**: `STATUS: FORECAST` + explainer (ledger takes over on settlement); actions Edit / Remove (proposals; remove of an actual is refused — corrections only); **provenance panel**: event id, creation channel (`VIA CHAT · <date>`), commit.
11. **Item — custom sequence** *(design.pen "Item - Custom Sequence")* — same shell; **sequence card** (`DATES · EXPLICIT — NO RULE`): one row per scheduled date with `installment n of m` meta and amount, total row; **impact card**: 12-bar mini chart with baseline and month letters; **"+ Add a date"** button (M-proposal); **provenance panel**: item id, `RULE: NONE · <n> EXPLICIT DATES`, last-change commit. (Maps to schedule amounts.)
12. **Auth** *(no design.pen reference)* — email field, send-link button, "check your mail" state, deep-link/verify handler, expired-link error state.
13. **Onboarding (first book)** *(no design.pen reference)* — 3 steps: (a) horizon + opening balance → `POST /books` creates the (empty) book immediately; (b) optional "describe your money in a few sentences" free text = a normal `POST /turns` against that book, yielding a normal proposal card (S4) with the derived items; (c) apply → book **populated** → Home. Skip path after (a): empty book. `turns.book_id` is therefore always NOT NULL.
14. **Import / Export** *(no design.pen reference; web-primary)* — dropzone/file pick; progress stream (stage, section, reconciliation checks passing/failing live); final reconciliation report (per sheet row: matched / mismatched + delta / skipped, with the 1-cent parity label where applicable); accept-into-book = one proposal card; export controls (mode, window, start) + download. Mobile: export via share sheet; import points to the web app in the MLP.
15. **Settings + History** *(no design.pen reference)* — account (email, delete account with confirmation phrase, export-my-data); book settings (horizon, opening balance via `set_horizon`/`set_opening_balance` host ops, cutover via M8 — every edit a proposal through `POST /book/edits`); privacy page (subprocessor list); about (engine version, current revision); revision history list (R12: message, author, timestamp, id — read-only).

Empty/loading/error states are required for every screen; empty states carry one example ask ("try: can I afford …").

## 7. Import pipeline (the one agentic loop, ADR-0030 stage 4)

1. Parse xlsx (values + formulas, as `proto/server.py:sheet_text`).
2. Loop: propose intents for a section → dry-run → compare engine totals against the sheet's own subtotal/total/balance rows → mismatch: investigate with `trace()`, revise; match: next section. Cap: 20 model calls, then present partial result honestly.
3. Target rule: on an **empty book**, import authors into base. On a **non-empty book**, import always authors into a fresh fork named from the filename — never into base — and the accept step lands there; the user promotes changes to base later through ordinary proposals. Import never merges silently and never destroys existing items.
4. Output: one big proposal (origin `import`) + reconciliation report. Nothing lands without the user applying the proposal.
5. Parity note for the report: 1-cent divergences on chained multiplications are expected and labeled (engine 4dp fixed-point vs Excel float; `km/notes/2026-08-22-proto-webapp-findings.md` §3).

## 8. Non-functional requirements

- **Latency budgets** (measured basis: proto bench 2026-08-22): read/answer turn p50 ≤ 4 s, p95 ≤ 12 s; proposal turn p50 ≤ 6 s; accept (apply+run) p95 ≤ 1 s; forecast/trace endpoints p95 ≤ 300 ms; import ≤ 90 s with streamed progress.
- **Cost guardrails**: per-user daily model budget (default $0.50/day) enforced server-side; over budget → turns refuse politely with a retry-tomorrow message; usage logged per turn.
- **Rate limits**: 30 turns/hour/user; 5 imports/day.
- **Availability**: single-node acceptable for MLP; nightly backups (§2.2); restore procedure documented and tested once before beta.
- **Determinism surface**: `engine_version` + revision id on every payload; a support question must be reproducible from (revision, engine_version, as_of).

## 9. Compliance gate (ADR-0026 rule 4 — blocks launch)

Before ANY external user: DPA template; subprocessor list published on the privacy page (hosting provider, Postgres host if managed, OpenRouter, Google — model, email provider, Sentry — error tracking, EU org, Grafana Cloud — metrics, EU region, **and the speech-recognition path**: prefer on-device recognition where the platform offers it; any server-side speech provider joins the list or dictation ships disabled on that platform); privacy policy + ToS; account deletion (`DELETE /me` erases Postgres rows AND the book directory and its backups within 30 days) and data export; EU-region hosting and storage; zero-retention model routing verified; encryption at rest for volume and backups; **log retention stated in the privacy policy**: raw model request/response payloads purge after 30 days (§4 `llm_calls`), request logs after 90, and account deletion cascades through all of it. One hour with an Italian fintech lawyer is flagged (ADR-0026) — before public launch, not before beta.

## 10. Testing and gates

- **Port the proto trial suite** to service level: T01–T12 equivalents run against `POST /turns` + `/proposals` (accepting proposals programmatically), asserting final book state numerically. These are the model-behavior gate (ADR-0028): any prompt or model change reruns them.
- **New invariant trials**: T13 mutation-requires-applied-proposal — attempt every bypass path INCLUDING the §2.5 staleness paths (accept after save/discard/activate/another-accept must supersede, never apply blind); T14 question-turn-never-writes (T11 scenario through the service); T15 the §2.4 `what_if` payload field is present and truthful on every non-base/overlay figure (single-rule definition); T16 import round-trip through the API (T06/T07 class) including the non-empty-book fork rule; T17 correction leaves the scar (original retrievable, note present); T18 record-actual discriminator (context flag + date rule; future date → forecast; ambiguous → clarification).
- **Contract tests**: OpenAPI schema → generated client compiles; response invariants (§3) checked by middleware in test mode.
- **E2E**: Playwright on web (auth → onboard → ask → confirm → forecast → trace → save); Maestro smoke on iOS simulator (same path).
- **CI**: engine tests stay untouched; app tests run on the app workspaces only. Trials that call OpenRouter run on a nightly schedule + pre-release, not per-commit (cost + flake control).

## 11. Observability

Three layers, one correlation chain: `request_id → turn_id → llm_calls.seq → proposal_id` appears in every log line and payload envelope, so one user report is traceable end to end.

- **Product** — the turn log (§4) is the primary instrument. Dashboards (simple SQL views are fine for MLP): turns/day, proposal accept rate, clarification rate, repair-round rate, p50/p95 turn latency, cost/user/day, import success rate, reconciliation mismatch rate. The proposal accept rate is the MLP's core product metric: it measures whether interpretation is trusted.
- **LLM** — `llm_calls` (§4) records every model call individually: purpose, tokens, cost, latency, error, and (for 30 days) the raw request/response for misinterpretation debugging. Alarms: global daily spend above a configured ceiling, and repair-round rate above 20% over 24h (the prompt is regressing or the provider changed something — rerun the trial suite).
- **API and infra** — structured JSON request logs (request_id, route, status, duration; Logfire/OTel instrumentation is fine as a local library, hosted backend only with confirmed EU residency + §9 listing); per-endpoint p50/p95 and error-rate metrics compared against the §8 budgets. **Metrics, alert rules, and the uptime probe live OFF the VM: Prometheus remote-write to Grafana Cloud free tier (EU region)** — alarms that die with the box are not alarms. Hard rule: no user identifiers in metric names or labels; metrics stay content-free. Unhandled exceptions to Sentry (or equivalent) with the request_id attached.
- **Deliberately not adopted at launch**: hosted LLM-observability platforms (Langfuse and peers). `llm_calls` is the system of record (purge + deletion wired in); a SaaS copy of prompts is a second retention regime for a UI not yet needed. Adoption trigger: prompt iteration becomes routine (multiple prompt versions per week, human review queues); prefer an EU-hosted vendor with a DPA, added to §9 first.

## 12. Deployment

Hetzner Cloud, EU region: one VM, Docker Compose (Caddy TLS → service; Postgres container with volume; backup cron to S3-compatible object storage; a metrics agent remote-writing to Grafana Cloud EU per §11). Staging = second, smaller VM with its own key and a spend cap. Mobile: Expo EAS builds; TestFlight/internal track for beta. Secrets via environment (OpenRouter key, mail provider key, DB URL); never in the repo.

## 13. Open items the implementing agent must NOT decide alone

1. Anything that would relax an invariant in §5-F2, §6, or §9.
2. Any change under `cashkit/` — engine repo rules apply unchanged (CLAUDE.md, incl. ENGINE_VERSION discipline).

Resolved 2026-08-23 (see DECISIONS.md D-MLP-05): the Home trust chip is dropped for the MLP; warnings are structural update-time checks with no thresholds; refusal/clarification copy follows the voice rule in §5-F1. Final copy review before beta stays with Luca (ADR-0025).

## 14. Known gaps, host-side extensions, and their owners

- R5/R6 single-call SDK verbs: host-composed for MLP; the SDK review owns the real verbs (intent-schema-draft note).
- **Host-side extensions of the v0 intent schema** (all §2.5; never exposed to the model; the schema note gains a matching section via `km/adr/pending-spec-updates.md`): the M5 record-actual channel (§5-F5 discriminator), `set_horizon`, `set_opening_balance`, `remove_event`, `edit_schedule_date`, and the `query_ledger` read tool.
- **ADR-0021 app-layer domain-coverage duty**: deferred for the consumer MLP (no tax-mechanics scope for the consumer persona); the B2B/SME track owns it. Recorded in `DECISIONS.md`.
- Postgres revision store: deferred with trigger (§2.2), ADR-0018 owns the seam; recorded in `DECISIONS.md`.
- Push notifications, multi-book, VAT, formula authoring, offline mobile, configurable warning thresholds: all post-MLP; do not scaffold for them (YAGNI — the seams above are the only sanctioned ones).
