# SDK-call eval — first results (2026-11-05)

First runs of `evals/sdkeval` ([ADR-0037](../adr/0037-sdk-call-accuracy-eval.md)). Models were driven
through the `claude` CLI with every tool disabled (`evals/scripts/claude_cli.py`), so they answer
from the system prompt and the task alone, as over a plain API. All runs use the `dev` split and
temperature 0, with at most two turns: the second turn returns tracebacks and error diagnostics,
never expected values.

## Headline

| Run | Suite | Context | Pass (final) | First turn | Tokens in / out |
|---|---|---|---|---|---|
| Haiku | 43 families × 1 | none | **14.0%** [6.6–27.3] (6/43) | 14.0% | 188k / 119k |
| Haiku | 43 families × 1 | SDK reference | **100%** [91.8–100] (43/43) | 100% | — |
| Haiku | 51 families × 2 | SDK reference | **100%** [96.4–100] (102/102) | 98.0% | 958k / 117k |
| Sonnet | 51 families × 2 | SDK reference | **100%** [96.4–100] (102/102) | 97.1% | 965k / 44k |

The SDK reference costs about 9k input tokens per call.

## What it says

1. **The documentation does almost all the work.** Without `sdk_reference.md`, Haiku invents
   the API (`add_line`, `FixedAmountTerm`, `Scenario.fork`, a `param` collection) and fails
   every authoring category. Only read-only questions survive, because `kit.run().summary()` is
   guessable. With the reference it solves everything. For the earlier fine-tuning or
   weight-editing question, the evidence says good context comes first and dominates.
2. **With the reference, current Claude models are at the ceiling of this suite**, including the
   difficulty-4 families (`startup_brief`, `iva_per_cassa`). On the current suite the remaining
   value is in:
   - small or local models (the ADR-0017/0019 on-device case), via `openai-compat:`;
   - consistency (`--samples k` at temperature > 0, read pass^k);
   - catching doc and SDK regressions (`--context` ablations, `regrade`).
   Separating frontier models from each other would need harder families.
3. **First-turn misses are mundane, and the feedback turn fixes all of them:**
   - Sonnet, 3 misses: used `Event`, `Decimal` or `Settlement` without importing them.
   - Haiku: passed `memo=` to `Event`. The field is `note`, and the reference showed an `Event`
     example but no field list. **Fixed:** the reference now lists every `Event` field.
   - Haiku: asserted that base Acme's settlement `== Settlement.immediate()`. Base holds
     `settlement=None`, which *means* immediate but does not compare equal. **SDK wrinkle, not
     fixed here:** two representations of one meaning. The reference now warns about it; whether
     the SDK should normalise `None` to `immediate()` (or the reverse) is an engine decision.

## Caveats

- One or two variants per family. The intervals are wide, and 100% means "no failure observed
  in ~100 samples", not "never fails".
- Haiku through the CLI does not report the reference run's tokens: that run predates the
  token envelope.
- `evals/runs/` is git-ignored. These numbers are reproducible with the commands in
  `evals/README.md`; the runs themselves are not committed.
