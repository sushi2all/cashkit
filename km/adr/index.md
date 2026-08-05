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
| [0013](0013-ui-cell-semantics.md) | UI cell semantics: every cell edit resolves to a model mutation | 2026-08-04 |
| [0014](0014-ui-delivery-post-v1.md) | UI delivery: post-v1, notebook spike then local web app, single-user local-first | 2026-08-04 |
| [0015](0015-agent-is-a-command-interpreter.md) | The agent is a command interpreter over the SDK, not a financial advisor — *narrows PRD §9.3/§9.5/§9.6* | 2026-08-05 |
| [0016](0016-engine-and-sdk-are-model-free.md) | The engine and SDK never call a model | 2026-08-05 |
| [0017](0017-local-first-adoption-requirement.md) | Local-first is an adoption requirement: privacy-conscious host and offline mobile | 2026-08-05 |
| [0018](0018-revision-store-is-an-interface.md) | The revision store is an interface; git is one implementation | 2026-08-05 |
| [0019](0019-intent-grammar-agent-surface.md) | The agent surface is an enumerated intent grammar, not free-form SDK composition | 2026-08-05 |
| [0020](0020-tax-coverage-as-diagnostic.md) | Non-native tax coverage is a deterministic diagnostic, not agent behaviour — *superseded by 0021* | 2026-08-05 |
| [0021](0021-engine-is-content-free.md) | The engine is content-free: domain knowledge and the agent are app-layer | 2026-08-05 |
