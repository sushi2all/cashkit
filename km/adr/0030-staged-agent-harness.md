# ADR-0030 — The agent harness is staged over the intent grammar, not a free tool loop

**Date** 2026-08-22 · **Status** accepted · **Type** app-architecture · **Source** proto session 2026-08-22 (.specstory 10-41-07Z; evidence in proto/TESTLOG.md)

## Context

The proto encoded all interpretation in one prompt and one call. The question: is a full agent harness — tool calling, verification built in — worth it? Trial evidence: batch emission went 10/10 on flash at ~3 s; the diagnostics-feedback round self-corrects structural errors; the one uncaught class is a valid formula with wrong semantics (T03: plausible numbers, silence); a state snapshot cannot serve open-ended Q&A at scale (T11 required hand-feeding computed results); upload verification lived outside the loop (T06/T07).

## Decision

A staged harness, with the ADR-0019 enumerated intents as the only tool surface:

1. **Authoring turns keep the single-call spine.** One call emits the whole op batch; the diagnostics round remains the repair loop. No per-op agent iteration.
2. **Formula-bearing turns get one bounded verification call**: instruction + applied ops + `trace()` receipts in, confirmation or corrective ops out. This closes the silent-wrong-formula hole — exactly the failure ADR-0019 predicted for composed analysis.
3. **Question turns get a read-only tool loop** (run, trace, why_zero, query_events, plus the single-call reporting intents of ADR-0019 rule 1), 2–4 calls, bounded. The model quotes engine numbers; it never derives them.
4. **Only spreadsheet import runs a full agentic loop**: author a section, run, reconcile engine totals against the sheet's own subtotal rows, investigate mismatches with trace, repeat. Rare, latency-tolerant, highest-stakes.
5. Host fills `as_of`; read and write toolsets are separated (ADR-0029).

## Alternatives considered

- Free-running agent over the ~40 SDK verbs: rejected — ADR-0019 rejected the surface, and it destroys the two properties the proto proved valuable: bounded latency and a trial suite that asserts final state.
- Flat single call everywhere: rejected — silent-wrong formulas and the Q&A scaling wall.

## Risks and implications

Roughly 2× call cost (immaterial at current volume, ADR-0028). The verification prompt is new design work. Stage 3 obsoletes the proto's hand-built results snapshot. The intent schema becomes a versioned contract — already an ADR-0019 risk; the prerequisite scoring exercise now owes a formula-construction class and a reconciliation-loop class.

Related: [[0015-agent-is-a-command-interpreter]], [[0019-intent-grammar-agent-surface]], [[0028-flash-class-model-no-preroute]], [[0029-question-turns-never-write]]
