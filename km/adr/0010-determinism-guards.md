# ADR-0010 — Determinism guards: holiday snapshot, wall-clock lint, writer lock, currency error

**Date** 2026-07-19 · **Status** accepted

## Context

Four smaller holes, each capable of quietly breaking the reproducibility or exactness guarantees:

1. Business-day adjustment consulted the `holidays` package at runtime — a version bump changes holiday sets, which changes historical runs. The reproducibility guarantee everything is built on dies to a dependency update.
2. The wall-clock lint banned only `date.today()`; `datetime.now()`, `utcnow()`, `datetime.today()`, `time.time()` were all still legal.
3. The Phase 9 gate said a second concurrent writer "fails loudly" with no mechanism.
4. `currency` exists on Item and Event, conversion is deferred — so nothing stopped `agg()` or the cash fold from silently summing EUR and USD.

## Decision

1. `CalendarSpec.holidays` is a **resolved list of dates** for the whole horizon, computed at book creation and committed. The `holidays` package is only a seed; runtime never consults it.
2. The lint bans the full set: `date.today`, `datetime.now`, `datetime.utcnow`, `datetime.today`, `time.time` in `engine/` and `model/`.
3. Write operations take an exclusive lockfile at `.cashkit/lock` (O_EXCL, pid + timestamp). Second writer gets `CK-E013` naming the holder; stale locks (dead pid) reclaim with `CK-W010`.
4. Cross-currency aggregation or fold is an error diagnostic (`CK-E020`), never a silent sum. Conversion arrives with multi-currency support (§7.3). Relatedly, the "FX as param time series" wording in §7.1 was corrected — params are scalars; per-period series are deferred.

## Consequences

- Reproducibility no longer depends on any dependency's data files.
- The single-writer story covers all three stores with one mechanism, matching the single-entity deployment model.
- Mixed-currency books fail fast at validation instead of producing plausible nonsense.
