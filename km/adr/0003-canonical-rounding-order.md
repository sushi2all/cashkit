# ADR-0003 — Canonical rounding order and residual absorption

**Date** 2026-07-19 · **Status** accepted

## Context

A generated flow passes through up to five multiplicative steps — escalation, probability weighting, settlement share split, withholding, VAT — and each is a rounding boundary. The order changes cents. The PRD demanded dual-engine byte-equality without pinning the order, which made the Phase 3 gate underdetermined: two correct engines could disagree legally.

## Decision

The order is canonical and fixed: **base amount → escalation → probability → settlement share split → withholding → VAT per line**, each step rounding to 4 dp under the book's declared policy before the next step. In a `share` split, the last term absorbs the rounding residual so legs sum exactly to the accrued amount. The reference engine implements the identical order.

Also specified while here: formula division rounds at the same boundary policy, and integer intermediates for scale→multiply→divide go through arbitrary-precision ints (or checked int128) — silent int64 wraparound is forbidden.

## Consequences

- Byte-equality between engines is now a well-posed requirement.
- Any future step (e.g. FX conversion) must declare its position in the chain before implementation.
- Overflow near the 9×10¹⁴-unit ceiling raises instead of wrapping.
