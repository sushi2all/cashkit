# ADR-0021 — The engine is content-free: domain knowledge and the agent are app-layer

**Date** 2026-08-05 · **Status** accepted · **Type** strategy / scope · **Source** user directive, orchestration session 2026-08-05 · **Supersedes** [ADR-0020](0020-tax-coverage-as-diagnostic.md); **narrows** [ADR-0015](0015-agent-is-a-command-interpreter.md) / [ADR-0019](0019-intent-grammar-agent-surface.md) to app-layer artifacts; **cancels** Phase 11 as a core deliverable

## Context

The design sessions of 2026-08-05 progressively moved judgement out of the agent (ADR-0015) and into the engine as deterministic diagnostics (ADR-0020). That was the right direction with the wrong destination: an enumerated list of Italian tax mechanics (IRES/IRAP, INPS/INAIL, TFR, acconto IVA) inside `validate()` is domain **content**, and content ages, localizes, and multiplies — the engine would grow a regulatory encyclopedia one info-code at a time.

## Decision

**CashKit core is a pure logic/calculation engine behind an SDK. All discretionary content is the domain of applications built on top of the SDK — as is the agent implementation.**

What this rules in and out:

1. **Stays (logic):** everything parameterized and deterministic — models, both engines, formula language, ledger, scenarios, frames, version control, introspection, CLI. Including the Phase 6 VAT/tax-regime machinery: rates, return periods, netting, credit carry-forward, withholding are arithmetic over authored parameters, with no jurisdiction baked in.
2. **Removed (content):** ADR-0020's coverage diagnostics (CK-I010…CK-I015) — never implemented; S5 instructed to skip them. `validate()` checks model consistency (PRD §10.1), not domain completeness. The mechanism apps should use instead already exists: tags, flags, and `describe_book()`.
3. **Removed from core (agent):** Phase 11 (the packaged agent skill) is no longer a core deliverable. v1 completes at Phase 10 / Session S5. ADR-0015 (command-interpreter scope) and ADR-0019 (intent grammar, drafted in `km/notes/intent-schema-draft.md`) remain valid *as app-layer design records* — they describe the first app, not the SDK. The two SDK gaps ADR-0019 surfaced (single-call reporting verbs) remain legitimate SDK feature requests, because a verb like `top_categories` is logic, not content.
4. PRD §7.2 ("tax mechanics deliberately not native") is thereby strengthened from a scoping table into the architecture: the escape hatch (manual items/events, tags) is the *only* tax-content mechanism the core will ever have.
5. The §9.5 safety property (a forecast silently omitting known mechanics) is now an **app-layer requirement**: any app or agent presenting forecasts for real entities owes its users a coverage check. The engine's contribution is unchanged: honest diagnostics, `describe_book()`, and tag-based introspection that make such a check a query, not a judgement.

## Consequences

- The session plan ends at S5; the Phase 11 gate (fresh-agent end-to-end transcript) moves to the app-layer backlog with the UI (ADR-0013/0014) and the agent surface (ADR-0015/0019).
- `km/adr/pending-spec-updates.md` (PRD §9 edits) is superseded: rather than rewriting agent behaviour, PRD §9 and the agent-related §11 acceptance criteria are reclassified as app-layer material. The engine's definition of done is PRD §10 acceptance criteria minus agent-skill items, plus the per-phase gates through Phase 10.
- No implemented, gate-verified code is rolled back by this ADR: coverage diagnostics were caught before implementation; Phase 6 stays.
- Risk accepted knowingly: nothing in the core now nags an incomplete book. That duty is delegated to the layer that talks to users — which is where a message in the user's language, jurisdiction, and context belonged anyway.
