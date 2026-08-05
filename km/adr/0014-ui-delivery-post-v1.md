# ADR-0014 — UI delivery: post-v1 deliverable, notebook spike then local web app, single-user local-first

**Date** 2026-08-04 · **Status** accepted · **Type** strategy / architecture · **Source** orchestration session 2026-07-29

## Context

A direct-manipulation UI (ADR-0013) needs a delivery vehicle and a decision on whether it changes v1 scope. PRD §5.2 already budgets delta recompute < 5 ms for "UI interaction"; PRD §7.3 defers a hosted UI and ties the Postgres question to concurrent human editing.

## Decision

1. **The UI is the second major deliverable, after the 11-phase v1.** ("Phase 2" of the product, distinct from PRD Phase 2.) v1 needs no new engine affordances for it: `trace()`, `describe_book()`, field-sparse overlays, `ChangeReport`, and the 5 ms delta budget were designed for exactly this consumer.
2. **Spike first as a notebook widget** (`anywidget` grid) to validate the trace-popover-as-edit-menu interaction at near-zero cost before committing to a front end.
3. **Product form: local web app** — FastAPI + browser, wrapping the SDK in-process; engine, git, SQLite, DuckDB all on local disk.
4. **Single-user, local-first.** No auth, no tenancy, single writer. Postgres stays deferred per PRD §7.3; DuckDB-Wasm over Quack remains a read-only path (and not load-bearing until DuckDB v2.0).

## Alternatives considered

- **Build the UI into v1**: rejected — collides with the config-store decision (D9) only if multi-user is assumed, and delays the engine for a consumer that needs nothing from it yet.
- **Hosted/multi-user app**: rejected for now — concurrent human editing is the documented trigger for revisiting storage (PRD §7.3); take that decision when the need is real.
- **Notebook-only forever**: rejected — the primary persona (founder/CFO) does not live in Jupyter.

## Risks and implications

- If the spike shows `trace()`-driven editing doesn't feel right, the taxonomy in ADR-0013 gets revised before web-app investment — that is the spike's purpose.
- Single-writer assumption must hold in the web app (one browser, one engine process); violating it re-opens the Postgres question implicitly.
- Creates future work: UI phase plan with its own gates, after v1's S6.
