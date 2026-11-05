# `evals/` — how accurately does an LLM drive the CashKit SDK?

`sdkeval` measures whether a model, given a plain-language request, writes
the SDK calls that leave the book in the **right state**: right items, right
signs, right terms, right scenarios, and right numbers to the cent. It is the
measurement behind every "should we fine-tune / switch model / rewrite the
docs?" decision ([ADR-0037](../km/adr/0037-sdk-call-accuracy-eval.md)).

It lives outside `cashkit/` on purpose: the engine and SDK never depend on a
model ([ADR-0016](../km/adr/0016-engine-and-sdk-are-model-free.md)).

## Quick start

```bash
cd evals
uv run python -m sdkeval list                       # 51 task families, 9 categories, difficulty 1–4
uv run python -m sdkeval show vat_quarterly -v 0    # one task: prompt, setup, reference, checks
uv run python -m sdkeval selftest                   # reference = 100%, empty reply = 0%

# Run a model (see "Models" below)
export ANTHROPIC_API_KEY=...
uv run python -m sdkeval run --model anthropic:claude-sonnet-5-5 --variants 3 --jobs 8
# → runs/<timestamp>_<model>/report.md, summary.json, results.jsonl

uv run python -m sdkeval compare runs/A runs/B      # side by side, per category and family
uv run python -m sdkeval regrade runs/A             # re-run A's scripts against today's SDK
```

The eval's own tests (they run the real SDK, about 2 minutes):

```bash
uv run pytest evals/tests        # from the repo root
```

## What a task is

A **family** is a parametrised generator, for example `net_terms`: "we invoice
Gamma €X a month from <month>; they pay N days after invoice". Each
**variant** is a seeded draw with different amounts, dates, terms and wording.
It carries:

- **prompt**: the request, describing the starting book in prose (and, where
  useful, the code that built it);
- **setup**: hidden code that builds the starting book at `ROOT`;
- **reference**: a correct solution, used only for self-tests;
- **checks**: what the final book must satisfy. Expected figures are
  recomputed in `Decimal` from the prompt's numbers (`sdkeval/oracle.py`),
  never by running the engine.

The model receives a fixed execution contract (`sdkeval/prompt.py`: `ROOT`,
`kit` and `answer` are pre-bound; reply with one ```python block) plus a
**context**, by default the SDK reference in
`sdkeval/context/sdk_reference.md`.

### Coverage (51 families)

| Category | Families | What goes wrong in practice |
|---|---|---|
| construction | `create_book`, `monthly_expense`, `fixed_term_income`, `quarterly_cost`, `escalating_rent`, `escalation_calendar_year`, `phased_contract`, `seasonal_schedule`, `payday_business_day`, `pipeline_probability`, `retag_by_selector` | exclusive `end`, sign vs `direction`, recurrence units, escalation as a literal instead of a param |
| settlement | `net_terms`, `split_payment`, `deposit_remainder`, `withholding` | `"60d"` vs `"2m"`, shares not summing to 1, the missing F24 remittance leg |
| tax | `vat_quarterly`, `vat_inclusive_price`, `iva_per_cassa`, `manual_tax_item` | authoring gross amounts, accrual vs cash tax point, periodicity, non-native taxes left out |
| formula | `derived_commission`, `derived_agg`, `threshold_bonus`, `lagged_royalty`, `cumulative_tracker` | bare names, `if` instead of `where`, `prev()` lags, stock vs flow |
| ledger | `forecast_events`, `event_inherits_item`, `import_actuals`, `void_forecast`, `correct_actual` | one-offs as segments, events not attached to their item, non-idempotent imports, voiding actuals |
| scenario | `downside_param`, `scale_customer`, `delay_project`, `hire_vs_contractor`, `capital_injection`, `nested_scenarios`, `scenario_terms_change` | writing the what-if into base, wrong parent, ids instead of selectors, re-authoring an inherited item |
| query | `runway_month`, `min_cash`, `intra_month_trough`, `breakeven_month`, `scenario_gap`, `cash_vs_accrual`, `total_outflow` | float answers, accrual read as cash, a month-end balance for an intra-month low, writing during a question |
| repair | `fix_unknown_reference`, `fix_missing_param`, `fix_inverted_sign`, `fix_cycle` | deleting the item that complains instead of fixing the cause |
| workflow | `commit_after_build`, `rerunnable_build`, `full_build`, `startup_brief` | committing without validating, scripts that fail on re-run, composing every feature from one brief |

`run_twice` tasks (`import_actuals`, `rerunnable_build`) execute the script
twice against the same book. A script that is not idempotent fails, as it
would when an agent retries after an interruption.

## How a sample is graded

1. The model's reply is reduced to its last ```python block.
2. Setup and script run in a **child process**: fresh temp directory, scrubbed
   environment (no API keys), `python -I`, rlimits and a timeout. The runtime
   records every SDK call the script makes, with the scenario it addressed and
   the diagnostics it got back.
3. The book is **re-opened from disk** and observed: every scenario's items,
   params, events, regimes, per-period cash and accrual, balance, summary and
   diagnostics, plus history and `answer`.
4. **Critical checks** decide pass/fail; **hygiene checks** are reported
   separately: floats in money code, direct store access, git, `today()`,
   committing when not asked, writing during a question.
5. A failed sample gets one **failure class**: `no_code`, `syntax_error`,
   `timeout`, `exception`, `no_book`, `error_diagnostics`, `wrong_answer`,
   `wrong_structure` or `wrong_numbers`.

With `--max-turns N` a sample becomes a short agent loop. After each turn the
model sees what an agent would see: the traceback, or the error diagnostics
returned by its writes and the run, never the expected values. It may then
send a corrected script. The report gives first-turn and final-turn accuracy;
the gap shows how well the model uses the SDK's diagnostics.

## Reading a report

`report.md` opens with the pass rate and its 95% Wilson interval. With ~50
tasks a 10-point difference can be noise, and the interval shows whether it
is. Below that come per-category, per-difficulty and per-family tables, the
most-failed checks with the observed and expected value, hygiene violations,
and one failure example per family. With `--samples k > 1` it also reports
pass@k and **pass^k** (all k samples right). pass^k is the number to watch
for an agent that writes forecasts unattended.

## Models

| Spec | Notes |
|---|---|
| `anthropic:<model>` | `ANTHROPIC_API_KEY` |
| `openai:<model>` | `OPENAI_API_KEY` |
| `openrouter:<model>` | `OPENROUTER_API_KEY` or `CASHKIT_LLM_API_KEY` (the trials' key) |
| `openai-compat:<model>@<base url>` | any OpenAI-compatible server: vLLM, llama.cpp, Ollama, LM Studio. Use this for a local or fine-tuned model |
| `cmd:<command>` | pipes `{"system", "messages"}` JSON to a command, reads the reply (or a `{"reply", "input_tokens", "output_tokens"}` envelope). `scripts/claude_cli.py` drives the `claude` CLI with all tools disabled. `--temperature` is not passed through: the command decides |
| `replay:<run dir>` | re-grade recorded replies without calling a model |
| `reference`, `noop` | self-tests |

## Using it for the decisions it exists for

- **Docs ablation:** run the same model with `--context reference` and
  `--context none`, or with your draft at `--context path/to/draft.md`, then
  `compare` the runs. This tells you whether a doc change helps before you ship it.
- **Model choice:** run each candidate on `--split heldout --variants 3 --samples 3`
  and compare pass^3 and per-category rates against cost
  (`summary.json` → `tokens`).
- **Fine-tuning:** generate training material from `dev` variants only
  (`show`, or `tasks.jsonl` in a run directory); report on `heldout`. Serve the
  tuned model behind an OpenAI-compatible endpoint and run it with
  `openai-compat:`.
- **SDK regressions:** `regrade` an old run after an SDK change. Answers that
  flip from pass to fail show where the change breaks scripts that used to work.
- **CI gate:** `run ... --min-pass 0.9` exits non-zero below the threshold.

## Adding a family

Add a function in `sdkeval/tasks/<category>.py`, decorated with
`@family(name, category, difficulty, description)`, that takes
`(rng, variant)` and returns `task(...)`. Draw every number from `rng`;
compute expectations with `oracle.py`; write a reference solution that uses
`Decimal` literals only. Then:

1. `uv run python -m sdkeval show <name>` and read the prompt as a model would.
2. Add a mutation to `tests/test_negative_controls.py`: a realistic mistake
   that must still *run* and fail a critical check. The suite refuses a
   family without one.
3. `uv run pytest evals/tests -k <name>`.
