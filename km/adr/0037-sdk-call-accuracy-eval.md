# ADR-0037 — SDK-call accuracy is measured by an outcome-graded eval suite under `evals/`

**Date** 2026-11-05 · **Status** accepted · **Type** quality / tooling · **Source** discussion 2026-11-05 on fine-tuning a model for the SDK

## Context

Whether to fine-tune (or weight-edit) a model for CashKit, which model to run the agent on, and whether the SDK documentation helps are all empirical questions. None can be answered without a measurement of how often a model turns a request into the *right* SDK calls. The service trials (`apps/service/trials/`) measure the consumer intent grammar on one pinned model (ADR-0028), once per task, as pass/fail gates. They do not exercise the free-form SDK surface (PRD §9, §10 "an agent given only SKILL.md builds a 20-item book"), compare models, or measure consistency.

## Decision

1. **An eval suite lives in `evals/sdkeval/`, outside `cashkit/`.** It contains model adapters and prompts, so under ADR-0016 it can never sit in the engine package. `tests/test_no_llm_dependency.py` scans `cashkit/` only and stays green.
2. **Grade outcomes, not code text.** A candidate script runs in a child process. The book is then re-opened **from disk** and graded on figures (per-period cash and accrual, balances, closing), structure (items, params, scenarios, regimes, events) and answers. Many call sequences are correct; only the resulting book matters. Call logs and static analysis feed separate *hygiene* checks (floats, store access, git, wall clock, unrequested commits, writes during a question). These are reported apart from correctness.
3. **Expected values are independent of the engine.** Each task family re-derives its figures in `Decimal` from the numbers it put in the prompt (`sdkeval/oracle.py`). Comparison is exact, with no tolerance: a forecast off by a cent is wrong.
4. **The eval gates itself.** Every family's reference solution must pass (`evals/tests/test_reference_solutions.py`). This is where oracle and engine meet; a disagreement fails here first. Every family also has at least one realistic mutation that must fail a critical check while still *running* (`test_negative_controls.py`), and an empty script must fail everywhere. This mirrors the dual-engine gate one layer up: a grader that cannot fail measures nothing.
5. **Seeded variants with a dev/heldout split.** Instances are drawn from string seeds `"<family>:<variant>"`. Variants ≥ 1000 are `heldout`. Iterate on prompts, docs or fine-tuning data against `dev` only, and report `heldout`.
6. **The SDK reference given to models is versioned and tested.** `sdkeval/context/sdk_reference.md` is the default context. Every code block in it is executed against the real SDK, and every catalogue code must appear in it. `--context none|<file>` turns the suite into a documentation ablation.
7. **Report rates with 95% Wilson intervals, plus pass^k when sampling.** For an unattended agent writing financial models, "right every time" (pass^k) is the operative number, not pass@k.

## Alternatives considered

- **Match generated code against a canonical call sequence**: rejected. It punishes equivalent solutions (a schedule versus two segments, `Settlement.net(60)` versus `DueTerm(offset="60d")`) and rewards look-alike wrong ones.
- **Use the engine's output on the reference solution as the expected value**: rejected as the primary oracle, because it would make the eval agree with the engine by construction. It survives only as the self-test that cross-checks the oracle.
- **Extend the service trials**: rejected. They test the intent grammar through the service on a pinned model, a different surface and question. Their numeric discipline (independent `Decimal` expectations) is kept.

## Risks and implications

- Families encode a reading of the PRD (for example, calendar-semantic durations bucketed into months). If the engine's semantics change deliberately, the oracle must change with it, and the reference self-test is what says so.
- The sandbox is a child process with a scrubbed environment, rlimits and a timeout. That isolates mistakes, not adversaries; untrusted models should run in a container.
- Running a model costs tokens; the default suite (51 families × 3 variants) is sized for a few hundred calls per model.

Related: [[0016-engine-and-sdk-are-model-free]], [[0019-intent-grammar-agent-surface]], [[0021-engine-is-content-free]], [[0028-flash-class-model-no-preroute]]
