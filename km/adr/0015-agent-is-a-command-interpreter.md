# ADR-0015 — The agent is a command interpreter over the SDK, not a financial advisor

**Date** 2026-08-05 · **Status** accepted · **Type** strategy / product · **Source** design session 2026-08-05

## Context

PRD §9 defines the LLM agent as the primary interface and loads it with advisory duties: §9.5 requires it to run a non-native tax checklist, ask the user about IRES/IRAP, INPS, TFR and acconto IVA when building a book for a real entity, and never present a forecast without an explicit coverage statement. §9.6 step 4 makes that checklist mandatory immediately after `init`. PRD §11 acceptance criteria bind "done" to an agent producing that statement unaided.

That scope assumes a model capable of sustained domain judgement. It also conflicts with the local-first adoption goal (ADR-0017), where the target models are small.

## Decision

**The agent's job is: interpret the user's command, call the SDK, present the result.** Nothing more.

In scope:

- Mapping a natural-language instruction to an SDK mutation (`set_item`, `set_param`, `fork`, `run`, `commit`).
- Reporting on data already in the book: "which is the highest spending category", "will I have cash in two months if I spend 2k today".
- Surfacing diagnostics and `ChangeReport` contents verbatim.

Out of scope:

- Asking the user about anything not already represented in the book.
- Advising on tax treatment, financing, or business decisions.
- Judging whether a forecast is complete.

The safety property §9.5 was protecting is real and is **not** discarded. It moves out of agent behaviour and into the engine as a deterministic diagnostic (ADR-0020).

## Alternatives considered

- **Keep the advisory scope**: rejected. It makes forecast correctness a function of model quality, which is the failure mode the rest of the design exists to prevent. It also puts a floor under model size that the adoption strategy cannot afford.
- **Two agent tiers (basic interpreter / advisory add-on)**: rejected for v1. One surface, one contract; revisit if a paid advisory tier is ever a product.

## Risks and implications

- **Contradicts the PRD as written.** §9.3, §9.5 "Required agent behaviour", §9.6 step 4, and the §11 acceptance criterion all need revision. Held as pending spec updates, not applied.
- A user can now build an incomplete book and never be told conversationally. ADR-0020 is the mitigation and is load-bearing; do not ship the narrowed scope without it.
- Narrows the SKILL.md surface considerably, which is the point: fewer behavioural rules, all of them mechanical.

Related: [[0017-local-first-adoption-requirement]], [[0019-intent-grammar-agent-surface]], [[0020-tax-coverage-as-diagnostic]]
