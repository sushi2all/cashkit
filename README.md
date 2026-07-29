# CashKit

Deterministic cash-flow modelling engine with an SDK-only surface, built so
LLM agents can build, mutate, and interrogate financial forecasts without
touching the underlying data structures.

Three properties spreadsheets do not have:

1. **Time as a first-class axis** — values computed period by period over an
   explicit horizon, with `prev()` as the only sequential dependency.
2. **Reproducibility by construction** — a run is identified by
   `(config revision, scenario, engine version)`; nothing reads the wall clock
   during evaluation.
3. **Introspectability** — every computed number traces to its formula,
   bindings and arithmetic; every configured value traces to the scenario
   level and commit that set it.

## Status

Under construction, phase by phase. See `PROMPT-fable5-implementation.md` for
the phase plan and `PRD-cashkit.md` for the full specification.

| Layer | Status |
|---|---|
| `cashkit/model` — Pydantic models, canonical YAML serialization | Phase 1, done |
| `cashkit/reference` — naive Decimal oracle | Phase 2 |
| `cashkit/engine` — vectorized int64 engine | Phases 3–4 |
| `cashkit/stores` — YAML+git config, SQLite ledger, DuckDB frames | Phases 5–9 |
| `cashkit/sdk` — public API | Phases 5–10 |
| `cashkit/cli` | Phase 10 |

## Development

```bash
uv sync --group dev
uv run pytest
```

Key invariants (full list in `PROMPT-fable5-implementation.md`):

- No `float` in money paths — int64 minor units at 4 dp in the core,
  `Decimal` at the boundaries (enforced by a type-audit test).
- Nothing reads the wall clock in `engine/` or `model/` (enforced by a lint
  test).
- Errors are `Diagnostic` objects; exceptions are for programmer error only.
- Canonical serialization is byte-stable: phantom diffs are a build failure.

Design decisions made during implementation live in `DECISIONS.md`; measured
numbers per phase gate in `BENCHMARKS.md`; architecture decision records in
`km/adr/`.
