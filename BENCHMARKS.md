# BENCHMARKS — measured numbers per phase gate

Hardware: MacBook Pro, Apple M3 Pro, 18 GB RAM, macOS (Darwin 24.2.0).
Toolchain: CPython 3.13.5, pydantic 2.13.4, hypothesis 6.163.0, PyYAML 6.0.3.

## Phase 1 — Models and canonical serialization

Phase 1 has no performance gate (PRD §5.2 budgets apply to the engine,
Phases 2–3). Recorded for the baseline only:

| Measure | Value |
|---|---|
| Full test suite (62 tests, incl. 850 property examples) | ~8 s |
| Gate stress run: 1000 generated Books + 500 Scenarios, byte round-trip | ~50 s, zero failures |

Nothing in Phase 1 is on the run-time hot path; serialization happens at
commit/load boundaries only (PRD budget: commit < 3 s, untested until the
config store exists in S3+).
