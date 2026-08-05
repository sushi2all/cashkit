# ADR-0016 — The engine and SDK never call a model

**Date** 2026-08-05 · **Status** accepted · **Type** architecture / constraint · **Source** design session 2026-08-05

## Context

CashKit is designed to be driven by an LLM agent (PRD §9), and the local-first adoption strategy (ADR-0017) introduces several model runtimes. Without an explicit boundary, an LLM dependency drifts into the package: a "smart" formula parser, a natural-language item importer, an LLM-assisted diagnostic explainer.

## Decision

**`cashkit/` has no LLM dependency, ever.** No package under `cashkit/` imports a model client, calls an inference endpoint, or embeds a prompt. This is a non-negotiable of the same class as "no float in money paths" and belongs in `CLAUDE.md`.

The model lives strictly outside the package boundary, in the skill, the CLI host, or the UI process. It reaches the engine only through the public SDK.

Consequences that make this worth stating:

- The engine's determinism guarantee (byte-identical dual-engine equality, no wall clock) stays total. A model call inside the engine would void it silently.
- Model choice becomes a host concern, so swapping frontier for local is a deployment decision with zero engine impact.
- `pip install cashkit` never pulls an inference stack.

## Alternatives considered

- **Optional LLM extra (`cashkit[llm]`)**: rejected. An optional dependency is still a supported code path, and the first one written will be an NL-to-formula helper, which is precisely the silent-error surface the design forbids.

## Risks and implications

- Needs a lint or import guard in CI to be real, in the same place the wall-clock ban is enforced (ADR-0010). A written rule without a check decays.
- Anything genuinely needing a model (natural-language import, narrative summaries) becomes a separate package with CashKit as a dependency, never the reverse.

Related: [[0010-determinism-guards]], [[0017-local-first-adoption-requirement]]
