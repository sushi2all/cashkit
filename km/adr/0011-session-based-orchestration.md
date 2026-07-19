# ADR-0011 — Phase execution via sequenced fresh-context subagent sessions

**Date** 2026-07-19 · **Status** accepted

## Context

The implementation prompt assumed one continuous autonomous run through all 11 phases. A single run would cross context compaction several times, and the things that die in summarization are exactly the load-bearing details of this project: canonical rounding order, serialization field order, `where`-not-`if`. Gate discipline also weakens with in-context momentum. The opposite extreme — one session per phase — pays a re-learning cost at seams that aren't real: Phases 2–4 became one semantic unit when ADR-0001 moved the formula front-end into Phase 2.

## Decision

Execution is restructured as an **orchestrator** (remote Claude Code session) spawning one **Fable subagent per session**, in strict sequence, each in a fresh context:

- S1: Phase 1 · S2: Phases 2–4 · S3: Phases 5–6 · S4: Phases 7–8 · S5: Phases 9–10 · S6: Phase 11 (mandatorily fresh — its gate specifies a fresh agent session).
- The orchestrator never implements; it verifies each session independently (re-runs the suite, checks per-gate commits, `DECISIONS.md`/`BENCHMARKS.md`, handoff note) and respawns a failed session fresh with the failure evidence.
- The repository is the only channel between sessions: each session starts by re-running the existing suite and ends with a `km/notes/handoff-s<N>.md`.

## Consequences

- Constraints are re-read verbatim at every seam instead of surviving as summaries; each gate is verified from committed evidence by a context that didn't produce it.
- The repo's self-sufficiency (the bet behind `DECISIONS.md`/ADRs/tests, and the explicit Phase 11 requirement) is tested at every boundary, not once at the end.
- Known trade-off: sessions S3–S5 modify engine-adjacent code without S2's tacit context. The reference engine and property tests are the guardrail — a broken invariant fails the dual-engine test loudly, which is the system working as designed.
