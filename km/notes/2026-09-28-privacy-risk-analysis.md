# Privacy & regulatory risk analysis — CashKit public launch

**Date:** 2026-09-28 · **Scope:** the hosted CashKit service (`apps/service`),
the web + Android client (`apps/client`), the assistant pipeline, and the
compliance pack in `compliance/`. **Not legal advice.** This is the brief for
the ADR-0026 "one hour with an Italian fintech lawyer before public launch":
it is written so that hour is spent on decisions, not on discovery.

**Baseline.** The existing pack is well above the norm for a pre-launch
product: hard deletion that reaches every table *and* the backups, export that
is proved to contain what deletion removes, retention periods tested against
the running config, identifier-free metrics and logs, Sentry scrubbing,
on-device-only dictation, no trackers, no bank connection. This analysis does
not repeat that work. It covers what the pack **does not** address, plus two
places where it states something that would not survive regulator scrutiny.

The SPEC9 checklist gates the **beta**. The gaps below gate a **public**
launch.

---

## 1. What the service processes (as built)

| Data | Where | Personal-data character |
|---|---|---|
| Email, session hashes, login-token hashes | Postgres | Identifying |
| The book: item names, tags, amounts, dates, event notes, actuals, every revision | YAML + ledger on the Hetzner volume | **Financial data** (WP248: "highly personal"). Free text can **reveal special categories** (a "psychotherapist" item, "union dues", "church donation", "IVF clinic") and **third-party data** ("rent to Mario Rossi", "salary — Giulia") |
| Assistant turns (user text, model reply, intents) | Postgres, **kept until account deletion** | As above, plus behavioural data |
| Raw model payloads: the snapshot built in `agent/snapshot.py` (item names, tags, segments, amounts, event notes, per-scenario balances and runway) | Postgres, blanked at 30 days; **transmitted to OpenRouter (US) → Google** | Full financial profile, cross-border |
| Request logs, metrics, Sentry events | Local / Grafana EU / Sentry EU | Pseudonymous at most (checked mechanically) |
| Imported spreadsheets | Import jobs → book | Business pilots (ERP guide): payroll, supplier and customer data, i.e. **employee and counterparty personal data** |

---

## 2. Applicable regimes

| Regime | Applies? | Why it matters here |
|---|---|---|
| **GDPR** (EU 2016/679) + **Codice Privacy** (D.Lgs. 196/2003 as amended by D.Lgs. 101/2018) | **Yes**: an EU establishment (Italy); lead authority is the **Garante** | Everything below |
| **EU AI Act** (2024/1689) | **Yes, lightly.** The assistant is an AI system that talks directly to people. It is **not** high-risk: it does no creditworthiness assessment (Annex III §5(b)) | **Art. 50(1)** transparency applies from **2 Aug 2026**, so it applies now. **Art. 4** AI literacy has applied since Feb 2025. Check the final Digital Omnibus text for any change to the timing |
| **Italian AI law** (L. 132/2025) | Yes | Under-14s need parental consent to use AI systems; this reinforces an age floor |
| **ePrivacy** (Dir. 2002/58, art. 122 Codice Privacy) | Yes, for the web client | The bearer in `localStorage` is strictly necessary, so it needs no consent. **Any analytics SDK added later needs consent or a documented exemption** |
| **EU–US transfer rules** (GDPR Ch. V) | Yes | OpenRouter, Inc. (US) and Google LLC. The DPF survived *Latombe* (General Court, T-553/23, Sept 2025), but an appeal to the Court of Justice is possible. Build on SCCs + a TIA, not on the DPF alone |
| **Consumer law** (Codice del Consumo; Dir. 2019/770 on digital content) | Yes, for consumers | Italian-language information duties; the broad liability exclusion in ToS §8 is at risk of being an unfair term (Codice del Consumo artt. 33–36) |
| **App-store rules** (Google Play Data safety + account-deletion policy; Apple 5.1.1(v), 5.1.2) | Yes, at store submission | Play requires a **web** deletion path as well as the in-app one. Apple now requires explicit permission before personal data goes to a third-party AI |
| **UK GDPR / Swiss nFADP** | Only if UK or Swiss users are targeted | Would need an Art. 27 UK representative and Swiss disclosures |
| **US state privacy laws, FTC Act §5, GLBA / FTC Safeguards Rule** | Only if US users are targeted | State-law thresholds are unlikely to be met. Get counsel on the GLBA question before any US launch |
| **PSD2 / PSD3 / FiDA** | **No**, while there is no bank connection (ADR-0026) | Adding aggregation later is a licensing event (AISP), not a feature |
| **NIS2, DORA** | No | Micro-enterprise outside the NIS2 sectors; not a financial entity |

---

## 3. Risk register

Likelihood (L) and impact (I) are rated 1–5. Score = L × I.

| # | Risk | L | I | Score | Evidence in repo |
|---|---|---|---|---|---|
| R1 | **Wrong controller/processor model for consumers.** The privacy policy says "we are a processor and you are the controller". A private individual managing their own money falls under the household exemption (Art. 2(2)(c), Recital 18) and **cannot be a controller**. Progress Lab is the controller and carries every controller duty: legal basis, Art. 13 notice, Art. 30 records, Art. 33–34 breach duties, a DPIA and transfer responsibility. The processor framing is correct only for business customers (the DPA template) | 5 | 4 | **20** | `compliance/privacy-policy.md` §Who we are, `compliance/subprocessors.md` intro |
| R2 | **Art. 13 notice incomplete.** It gives no legal basis per purpose (Art. 6(1)(b) for the service, 6(1)(f) for logs, security and the 30-day payload debugging). It gives no transfer safeguard or a way to obtain it. It has no automated-decision statement and no legal-entity identifiers (P.IVA, registered address). It is English-only for an Italian consumer audience | 5 | 3 | **15** | `privacy-policy.md` |
| R3 | **Special-category data, including inferred data, sent to the model.** `snapshot.py` ships item names, tags and event notes to the US. Following CJEU **C-184/20**, data that *indirectly* reveals health, religion or union membership is Art. 9 data. No Art. 9(2) condition exists today. The DPA annex acknowledges the issue for businesses; the consumer path does not | 4 | 4 | **16** | `agent/snapshot.py:_item`, `_events`; `dpa-template.md` Annex 1 |
| R4 | **Third-country transfer rests on an unverifiable claim.** Zero retention is contractual only (SPEC9 item 9 says so honestly). OpenRouter can route to **Google AI Studio** as well as Vertex, and the two have different data-use and residency terms. "Google's regions" names no location. There is no TIA, and it is unconfirmed whether OpenRouter is DPF-certified or signs SCCs | 4 | 4 | **16** | `subprocessors.md`; `config.py` `llm_model`; SPEC9 item 9 |
| R5 | **No DPIA.** WP248 criteria met: highly personal (financial) data, innovative technology (an LLM), and possibly vulnerable users (people in financial difficulty are the core audience of a runway tool). The Garante's Art. 35(4) list (Provv. 467/2018) names innovative technologies. With two or more criteria, a DPIA is expected | 4 | 3 | **12** | No DPIA anywhere in the repo |
| R6 | **Account takeover exposes a full financial profile.** On web the bearer lives in `localStorage`, and the site sends **no Content-Security-Policy** header, so any XSS steals the session (D-MLP-44 records the trade-off). `POST /auth/link` has **no per-address or per-IP throttle**, which enables mailbox bombing and an open cost channel once a paid mailer exists. Whoever controls the mailbox controls the account | 3 | 4 | **12** | `apps/client/src/api/tokenStore.ts`; `ops/Caddyfile` header block; `routers/auth.py:request_link` |
| R7 | **No breach-response capability.** As controller: 72 hours to notify the Garante (Art. 33), notify users when the risk is high (Art. 34), and keep a breach register (Art. 33(5)). No runbook, register or notification template exists. The only "breach" alarm is the backup-window one | 3 | 4 | **12** | `ops/DEPLOY.md` (monitoring only) |
| R8 | **No Record of Processing Activities (Art. 30).** The <250-employee exemption does not apply, because the processing is not occasional and includes Art. 9-capable data. It is the first document an inspector asks for | 5 | 2 | **10** | none |
| R9 | **AI Act Art. 50(1) disclosure absent.** Neither the auth nor the onboarding screen tells the user they are talking to an AI system or that the assistant sends data to the US. The assistant cannot be switched off for consumers, although the DPA template assumes it can be disabled | 4 | 2 | **8** | `AuthScreen.tsx`, `OnboardingScreen.tsx`, `SettingsScreen.tsx` |
| R10 | **No age floor.** The ToS states no minimum age. Italy sets the digital age at 14 (art. 2-quinquies Codice Privacy), and L. 132/2025 adds parental consent for AI use under 14. The Garante's actions against Replika and OpenAI both featured age-verification findings | 3 | 3 | **9** | `terms-of-service.md` |
| R11 | **Storage limitation.** Turns, the book and the account are kept indefinitely for inactive accounts. There is no dormancy rule | 3 | 2 | **6** | `retention.py`, `privacy-policy.md` table |
| R12 | **Email provider (open blocker) could leak tokens.** Click tracking rewrites magic-link URLs through the vendor, which then sees live sign-in tokens, and link-scanner prefetch can burn single-use links. The vendor must be EU-hosted, have a DPA, and have tracking **off** | 3 | 3 | **9** | SPEC9 item 13; `mail.py` |
| R13 | **Operator access is unlogged.** A single operator has shell access to plaintext books and 30 days of raw payloads, and nothing records operator reads. That matters for Art. 32 and for any B2B customer audit (DPA §3(h)) | 2 | 3 | **6** | `ops/DEPLOY.md` |
| R14 | **Business-pilot data (payroll, counterparties) through the consumer path.** Employee salary data needs a signed DPA and a customer-side legal basis. The ERP pilot would bring it in | 2 | 4 | **8** | `ERP-pilot-guide.md` §2, `dpa-template.md` |
| R15 | **Enforcement climate.** The Garante is the EU's most active authority on generative AI: OpenAI was fined €15M (Dec 2024), DeepSeek was blocked (Jan 2025), and Luka/Replika was fined (2025). A small Italian AI finance app is well inside its attention | — | — | amplifier | — |

**Low or accepted risks, recorded so they are not re-litigated:**

- Cookies and trackers: none found in the client.
- Dictation: on-device only.
- Logs and metrics: carry no identifier, enforced by a closed label vocabulary.
- Deletion and backups: hard delete, backup windows proved by the bucket's own timestamps.
- Prompt injection through imports: low. Imports cannot write without a confirmed proposal (ADR-0029/0031).

---

## 4. Top five actions before public launch

Ordered by risk removed per unit of effort. Each lists the risks it closes.

### 1. Re-found the consumer legal model: controller notice, legal bases, ROPA — closes R1, R2, R8, R10

- Rewrite `privacy-policy.md` with **Progress Lab as controller** for consumers. Keep the processor framing only in the B2B DPA.
- Add a purpose → legal basis → retention table. Name the transfer safeguard (SCCs, plus DPF where it applies) and say how to obtain it. Add a no-automated-decisions statement, legal-entity details (P.IVA, registered office) and an **Italian version** (EN + IT).
- Set an **18+ age floor** in the ToS, with a sign-up self-declaration.
- Write the **Art. 30 record** (one page, derived from §1 above) and keep it in `compliance/`.
- Extend `test_compliance.py` so the ROPA's retention and recipient columns are checked against `Settings` and the subprocessor list, as the policy already is.
- Have the lawyer hour review this, the ToS §8 liability clause, and the DPA together.

### 2. Make the assistant a separate, opt-in feature, and shrink or relocate what it sends — closes R3, R4, R9

- **Relocate:** call **Vertex AI directly in an EU region** under Google Cloud's DPA and SCCs, with abuse-monitoring logging exempted and zero retention configured. This removes OpenRouter as a subprocessor, removes the AI Studio path, and makes "zero retention" a contract term rather than a request flag.
  - Fallback if routing must stay: a signed OpenRouter DPA + SCCs, a written zero-retention confirmation, `provider.only` pinned to Vertex, and a documented TIA.
- **Opt-in:** the book, import and forecast work without the assistant, so show a first-use screen before the first turn. It should say three things:
  - you are talking to an AI system (AI Act Art. 50(1));
  - what goes where (US or EU, and which vendor);
  - free-text entries can reveal sensitive information.
  
  Capture **explicit consent** for that processing (the Art. 9(2)(a) route, and Apple 5.1.2 when iOS ships), and add a Settings switch that turns the assistant off.
- **Minimise:** drop event notes from the snapshot unless a turn needs them, and consider pseudonymising item names (for example `item_7`) and mapping them back on the server. Measure the effect with the existing trial suite (ADR-0028) before adopting it.

### 3. Run and file a DPIA (with the TIA inside it) — closes R5, supports R3, R4, R14

- Use the Garante's DPIA software or the CNIL PIA tool; either is fine.
- Cover these processing operations:
  - the book;
  - the assistant transfer;
  - the 30-day raw-payload retention (and whether 30 days is necessary, or whether 7 would do);
  - inferred special categories;
  - the B2B/ERP path with employee data.
- The DPIA is where the choice made in action 2 gets its written justification. It is also the document a Garante inspection or an app-store review asks for first.
- Record the outcome as an ADR.

### 4. Harden the account boundary and ship the mailer correctly — closes R6, R12, R13

- **Web session:** move the bearer to an `HttpOnly; Secure; SameSite=Strict` cookie (the D-MLP-44 upgrade) **and** add a strict `Content-Security-Policy` in the Caddyfile. Either one is a real improvement; both are the target.
- **Throttle** `POST /auth/link` per address and per IP, with the same 202 response so enumeration stays closed.
- **Mailer:** choose an EU-hosted provider with a DPA, turn **click and open tracking off**, add it to `subprocessors.md`, and let the existing hostname test enforce the listing.
- **Operator access:** SSH key plus 2FA on the Hetzner console, and an access log for operator reads of books and payloads.
- Run one **external penetration test** focused on auth, IDOR across books, and the import parser.

### 5. Build the breach-and-rights runbook, add dormancy retention, and complete the store disclosures — closes R7, R11, and the store gate

- **`ops/INCIDENT.md`:**
  - who decides, and the 72-hour clock from awareness;
  - a Garante notification draft (the online form) and a user-notice template;
  - an **Art. 33(5) breach register**, logged even when nothing is notified;
  - which alarms from `ops/observability/alerts.yml` start the clock.
- **Rights requests:** a small request log for anything outside the in-app buttons (rectification, objection, restriction), with the 30-day clock.
- **Dormancy rule:** for example, warn at 12 months of inactivity and delete at 13. Implement it in `retention.py` with the same both-directions tests, and state it in the policy (the compliance test then enforces it).
- **Store disclosures:** complete the Google Play **Data safety** form so it matches the subprocessor list, and publish a **web** account-deletion URL, which Play requires in addition to the in-app button.

---

## 5. Decisions for Luca / the lawyer hour

1. Direct Vertex EU or OpenRouter + SCCs? This analysis recommends direct Vertex EU (action 2).
2. Should assistant consent be explicit Art. 9 consent, or a notice plus an instruction not to enter sensitive data? This analysis recommends explicit consent, because the assistant is optional and consent is freely given.
3. Is the launch geography EU/EEA only? Recommended. It keeps UK, Swiss and US regimes out of scope until it is decided otherwise.
4. Can the raw-payload retention drop from 30 days to 7?
5. What ToS §8 liability wording is enforceable against Italian consumers?
