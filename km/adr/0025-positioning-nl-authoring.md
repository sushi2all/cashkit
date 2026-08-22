# ADR-0025 — Positioning: natural-language budget authoring on deterministic math

**Date** 2026-08-21 · **Status** accepted · **Type** strategy · **Source** launch sessions 2026-08-19/21 (.specstory 22-13-43Z, 07-33-18Z) + km/notes/cashkit-launch-brief.md

## Context

The first launch message ("A forecast you can audit") tested as uninteresting for consumers, and external research showed no existing app uses natural language to *create* a budget — that is the open gap. Separately, every "AI + money" pitch dies on the objection that language models get numbers wrong. And the reason CashKit exists, in the user's words, is the spreadsheet workflow: the pain is manual roll-forward, not broken formulas.

## Decision

Three durable positioning rules:

1. **The hero is authoring, against the spreadsheet.** "You budget by talking. The math is never AI." / "You can finally ditch your Excel budget." The named pain is manual rework; precision is stated as parity with the spreadsheet, not as the differentiator.
2. **Auditability is proof, not promise.** The deterministic engine and tap-any-number receipts move to the proof section. Exception: the Hacker News cut leads with the engine — two cuts of one message, benefit-first for the page, proof-first for HN.
3. **Every number visible in any marketing asset must add up.** All assets derive from the design.pen mockups with real euro amounts; one screenshot with a wrong sum is fatal, and someone will check.

## Alternatives considered

- Audit-first messaging for the consumer page: rejected; retained only for the HN channel.
- "None of the breakage" as the pain: rejected — the actual pain is roll-forward.
- Dropping "same precision": rejected — precision stays stated as parity.

## Risks and implications

The "explain in plain words" promise depends on the app-layer agent, not the engine; the copy is true only once that layer works end to end (the 2026-08-22 proto trials are the first evidence it does). Site copy and design.pen must stay in sync. Rule 3 adds a verification pass to every asset export.

Related: [[0015-agent-is-a-command-interpreter]], [[0022-mobile-mlp-five-features]]
