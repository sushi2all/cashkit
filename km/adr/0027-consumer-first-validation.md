# ADR-0027 — Validate with a consumer web app first; B2B/enterprise tiers later; single user per book

**Date** 2026-08-22 · **Status** accepted · **Type** strategy · **Source** .specstory 2026-08-22_11-09-47Z + /process-discussions session 2026-08-22

## Context

The strategy ADRs (0014, 0017) target a privacy-conscious Italian SME persona, local-first. The user set a different sequencing: both personas are targets, but the concept gets validated on the consumer side first — the user already sells other services B2B and will not ship to that segment before the concept is proven.

## Decision

1. **Launch a consumer-level hosted web app first for product/market validation; add business/enterprise tiers after.**
2. **Single user per book.** The MLP assumes one writer per book; multi-writer concurrency (shared household budgets) is out of scope. This keeps the engine's single-writer assumption (ADR-0014) intact on the hosted track — the hosted deltas reduce to a Postgres-backed revision store behind the ADR-0018 interface plus net-new auth and tenancy.

## Alternatives considered

- B2B/SME-first per ADR-0014/0017: deferred, not rejected — the local-first configurations remain the SME track.
- A full YNAB-style clone with bank sync as the validation vehicle: pushed back — bank aggregation is table stakes there (and gated by ADR-0026); the cheaper test of the actual differentiator is natural-language authoring with manual entry.

## Risks and implications

- Consumer PMF may not validate the deterministic-engine differentiator; the open question (recorded, unanswered in the source) is whether the test targets the budgeting category or the NL-authoring angle specifically.
- Hosting makes CashKit a processor: ADR-0026 gate 4 (DPA + subprocessor list) applies before launch.
- Partially revises the framing of ADR-0014/0017: local-first stays an adoption requirement for the SME track; the consumer track is hosted by design.

Related: [[0014-ui-delivery-post-v1]], [[0017-local-first-adoption-requirement]], [[0018-revision-store-is-an-interface]], [[0026-pilot-ingestion-no-aggregator]]
