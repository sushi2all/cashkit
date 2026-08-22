# ADR-0026 — Pilot ingestion avoids aggregators; CashKit keeps a pure-vendor GDPR posture

**Date** 2026-08-21 · **Status** accepted · **Type** security / strategy · **Source** km/notes/2026-08-21-privacy-compliance.md + .specstory 2026-08-19_22-21-13Z

## Context

Bank connections were cut at planning time over compliance. The question returned: does an aggregator (Plaid, Fabrick) that only reads transactions remove the burden? Analysis: the agent model removes the AISP licence but not agent registration with the national authority, GDPR duties, fintech-priced contracts, 90–180-day consent expiry, or the PSD3/PSR moving target (application 2027–2028).

## Decision

1. **For the ERP pilot: CSV/CAMT.053 file import and/or the user's own Qonto API key.** Both routes have zero PSD2 surface — local software using the user's own credentials provides no payment service to a third party. No aggregator contract.
2. **The aggregator door reopens only through a new ADR**, and the implementation must take the agent-of-the-aggregator path, never an own AISP licence.
3. **The pilot agreement states the controller/processor split**: the customer is the data controller; CashKit processes nothing server-side (true under local-first, ADR-0017).
4. **No hosted or LLM-touching feature ships before a DPA and a subprocessor list exist**, and an Italian fintech lawyer gets one hour before any production aggregator launch.

## Alternatives considered

- Plaid/Fabrick via the agent model now: viable but rejected — real regulatory relationship with audit and reporting duties, priced for funded fintechs, rules in flight.
- Own AISP licence: rejected as far too heavy.

## Risks and implications

Multi-bank coverage where a bank lacks a user-facing API stays blocked until an ADR reopens it. Statement 3 becomes false the moment a hosted sync or server feature ships — statement 4 is the gate that protects it. The consumer web-app track (ADR-0027) and the agent layer both hit gate 4. Not legal advice.

Related: [[0017-local-first-adoption-requirement]], [[0027-consumer-first-validation]]
