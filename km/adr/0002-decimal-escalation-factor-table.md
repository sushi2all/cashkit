# ADR-0002 — Escalation via Decimal factor table — no float in the money path

**Date** 2026-07-19 · **Status** accepted

## Context

The spec permitted a float64 intermediate for `(1+r)^n`, called it "exact at these magnitudes", and required a property test against a Decimal reference. It is not exact — it is within ~1 ulp, and a Hypothesis test *will* find half-up tie cases where the float lands at 0.499999…, the Decimal at exactly 0.5, and the two round differently. As stated, the gate was likely unsatisfiable.

## Decision

Escalation factors are computed in `Decimal`, once per distinct `(rate, n)` pair, converted to scaled int64 multipliers, and applied as a vectorized integer multiply. The distinct-factor count is rates × years — tens of values — so precomputation costs nothing. Float is now absent from the money path entirely. A float64 fast path may be added later only behind a property test proving byte-identity with the factor table, ties included.

## Consequences

- The "no float for money" constraint loses its single exception; the lint/audit story is simpler.
- The dual-engine byte-equality gate becomes achievable: both engines consume the same factor table semantics.
- If profiling ever shows factor precomputation mattering (it won't at this scale), the fast path has a defined admission test.
