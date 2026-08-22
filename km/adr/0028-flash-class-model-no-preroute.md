# ADR-0028 — The agent surface runs on a flash-class model; no pre-interpretation routing

**Date** 2026-08-22 · **Status** accepted · **Type** strategy / app-architecture · **Source** proto session 2026-08-22 (.specstory 10-41-07Z; evidence in proto/TESTLOG.md)

## Context

The proto webapp (NL → ops → SDK) was trialed on gemini-2.5-flash-lite and gemini-3.7-flash against the budget scenarios. Measured: lite handles flat lines, windows, schedules, params, forks, settlement and non-monthly recurrence, but fails two classes with **plausible numbers and no diagnostic** — recursive conditional formulas (T03) and numeric Q&A (T11, where it also wrote ops on a question). flash passes everything. Measured single-call medians: flash 3.0 s (edit), 3.1 s (Q&A), 8.7 s (formula); lite 0.8–1.6 s. Cost at 2 sessions/week: all-flash ≈ $0.80/week. A keyword router on the raw instruction was considered and rejected on principle: the requirement is clear only after interpretation, especially for spoken input.

## Decision

1. **Every agent turn runs on a flash-class model.** No router at current volume.
2. **Pre-interpretation routing is banned as a pattern.** If a cheap lane is ever justified by volume, the route decision reads the *artifact*, not the instruction: draft on the small model, escalate when its ops contain `where`/`prev`/`cum`, when a correction round fired, or when the turn resolved to a question.
3. **Any model swap is gated on the trial suite** (`proto/trials/`), which encodes the failure classes.

## Alternatives considered

- Keyword pre-route on conditional words: rejected — interpretation done early and badly.
- Draft-and-escalate now: rejected — the latency premium it was buying does not exist (3 s vs 0.8 s), and it adds a lane whose failure mode is silently wrong money.
- All-lite: rejected — unsafe classes above.

## Risks and implications

- Caveat for ADR-0019, not a contradiction: lite failed numeric Q&A while doing *arithmetic over a results snapshot*; under rule 1 of ADR-0019 (one intent per reportable question) the engine computes and the model never subtracts — small-model slot filling remains plausible, small-model formula *construction* does not. The ADR-0019 scoring exercise must include a formula-construction class.
- Model-version dependence is explicit: the trial suite is the gate, cost re-opens the decision at ~100× volume.

Related: [[0015-agent-is-a-command-interpreter]], [[0019-intent-grammar-agent-surface]], [[0030-staged-agent-harness]]
