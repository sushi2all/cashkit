# ADR-0020 — Non-native tax coverage is a deterministic diagnostic, not agent behaviour

**Date** 2026-08-05 · **Status** accepted · **Type** feature / safety · **Source** design session 2026-08-05

## Context

PRD §9.5 identifies the system's most dangerous failure mode correctly: a cash forecast that silently omits IRES advances, INPS contributions or TFR is not slightly wrong, it is wrong by the amount that causes the crisis it was built to predict. Its mitigation is a behavioural instruction to the agent: run a checklist, produce a coverage statement, never present a forecast without one.

Behavioural instructions are the weakest available mitigation. They degrade with model size, and ADR-0015 removes the advisory scope they depend on.

The observation that resolves this: the checklist needs no judgement. "This book has a `TaxRegime` and no items tagged `cat:tax` outside VAT" is a mechanical property of the book. PRD §9.5 already half-admits it, noting `validate()` emits an info diagnostic for exactly that case.

## Decision

**Move the §9.5 coverage statement from SKILL.md into the engine, as diagnostics emitted by `validate()`.**

- One diagnostic per non-native mechanic the book does not cover: IRES/IRAP, INPS/INAIL, TFR, acconto IVA, tax credits, instalment plans. Detection is tag- and flag-based (`cat:tax`, `flags={"manual_tax"}`), which the model already carries.
- Severity `info`, not `warning`: absence is legitimate for a book that is not modelling a real legal entity, and a diagnostic that always fires gets ignored.
- The coverage statement becomes a rendering of those diagnostics, producible by `validate()` or the CLI with no model involved.
- The agent's only remaining duty is to surface diagnostics verbatim, which ADR-0015 keeps in scope.

## Alternatives considered

- **Keep it in SKILL.md and accept degradation on small models**: rejected. It concedes the worst failure mode to model quality.
- **Severity `warning` or blocking `commit`**: rejected. Not every book is a real entity, and a check that fires on legitimate states trains users to ignore it.

## Risks and implications

- Adds ~6 codes to the diagnostics catalogue (PRD §10.1) and to `tests/test_diagnostics_catalogue.py`. Small, mechanical, testable, which is the entire argument.
- Detection is heuristic on tags. A book that models IRES under a differently-tagged item gets a false positive. Acceptable: an info-severity false positive costs a sentence, a false negative costs the forecast.
- Makes ADR-0015 safe to ship. The two travel together; the narrowed agent scope should not land without this.

Related: [[0015-agent-is-a-command-interpreter]], [[0005-vat-net-amounts-synthetic-items]]
