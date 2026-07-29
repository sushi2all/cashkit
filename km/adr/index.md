# ADR Index — CashKit

Numbered, append-only. Status: accepted unless noted.

| # | Title | Date |
|---|---|---|
| [0001](0001-formula-frontend-in-phase-2.md) | Formula front-end built with the reference engine; Phase 4 hardens it | 2026-07-19 |
| [0002](0002-decimal-escalation-factor-table.md) | Escalation via Decimal factor table — no float in the money path | 2026-07-19 |
| [0003](0003-canonical-rounding-order.md) | Canonical rounding order and residual absorption | 2026-07-19 |
| [0004](0004-cutover-blanket-suppression.md) | Cutover: blanket pre-cutover suppression, ledger authoritative | 2026-07-19 |
| [0005](0005-vat-net-amounts-synthetic-items.md) | VAT: net amounts, regimes as synthetic graph items, withholding counter-leg manual | 2026-07-19 |
| [0006](0006-watermark-at-commit.md) | Ledger watermark stamped at commit(); live runs use the full ledger | 2026-07-19 |
| [0007](0007-base-scenario-storage.md) | Base scenario: uniform semantics, privileged storage location | 2026-07-19 |
| [0008](0008-import-conflicts-abort-batch.md) | Import conflicts abort the batch; identical rows skip; void_event tombstones — *superseded in part by 0012* | 2026-07-19 |
| [0009](0009-field-sparse-overrides.md) | Overrides resolve field-sparse along the chain; segments atomic | 2026-07-19 |
| [0010](0010-determinism-guards.md) | Determinism guards: holiday snapshot, wall-clock lint, writer lock, currency error | 2026-07-19 |
| [0011](0011-session-based-orchestration.md) | Phase execution via sequenced fresh-context subagent sessions | 2026-07-19 |
| [0012](0012-actual-corrections-append-only.md) | Actuals are correctable in the ledger, immutably; corrections are append-only | 2026-07-29 |
