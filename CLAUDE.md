# CashKit

Deterministic cash-flow modelling engine, SDK-only surface, built for LLM agents. Python ≥ 3.11, Pydantic v2, numpy core, YAML+git / SQLite / DuckDB storage split.

## Documents

- `PRD-cashkit.md` — the spec. §2 core decisions are settled; deviations invalidate downstream work.
- `PROMPT-fable5-implementation.md` — phase plan with gates, grouped into six orchestrated sessions (one fresh Fable subagent each, strict sequence; see its §Execution model). Do not pass a gate without its test evidence.
- `ERP-pilot-guide.md` — pilot context, not a spec.
- `km/adr/` — architecture decision records; `km/adr/index.md` is the index. Every judgement call made under ambiguity gets an ADR (or a `DECISIONS.md` entry once implementation starts).
- `km/notes/` — working notes, reviews, meeting output.

## Non-negotiables (full list in PROMPT §Non-negotiable constraints)

- No float in money paths. int64 minor units @ 4dp in core, `Decimal` at boundaries. Escalation factors computed in Decimal (ADR-0002).
- `where`, not `if` — both branches always evaluate, elementwise selection.
- No wall clock during evaluation (`today()`/`now()`/`time.time()` lint-banned in `engine/`, `model/`).
- Errors are `Diagnostic` objects (catalogue in PRD §10.1); exceptions only for programmer error.
- Actuals immutable. `segments` atomic in overlays. Git never exposed in the SDK.
- Silent numerical error is the worst failure mode — prefer a diagnostic over a guess, always.

## Working conventions

- Rounding order is canonical (PRD §5.3): escalation → probability → settlement split → withholding → VAT. The reference engine and vectorized engine must share it byte-for-byte.
- All authored amounts are VAT-exclusive (net). No VAT-inclusive mode exists.
- Formula front-end is built in Phase 2 with the reference engine; Phase 4 hardens it (ADR-0001).
- When the PRD is ambiguous: pick determinism + exactness, emit a diagnostic over guessing, keep storage swappable, record the choice in an ADR / `DECISIONS.md`, continue.
