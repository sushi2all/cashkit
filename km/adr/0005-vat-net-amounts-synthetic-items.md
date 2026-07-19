# ADR-0005 — VAT: net amounts, regimes as synthetic graph items, withholding counter-leg manual

**Date** 2026-07-19 · **Status** accepted

## Context

Three unstated conventions, each capable of producing materially wrong cash:

1. Whether authored amounts include VAT was never specified — the single most consequential convention in the tax phase.
2. How `TaxRegime` output enters evaluation was unspecified. VAT credit carry-forward is a stock in a feedback loop (§5.1 lists it as a non-trivial SCC); a post-pass bolted on after evaluation could never participate in `prev()` feedback, so overdraft interest would not see VAT payments.
3. `DueTerm.withholding` "reduces cash received" — one leg only. The counter-leg (F24 remittance when paying, tax credit when withheld upon) simply vanished, which is the exact silent-understatement failure §9.5 warns against.

## Decision

1. **All authored amounts are VAT-exclusive (net).** The engine computes VAT per line, grosses up the settlement cash leg (1,000 @ 22% collects 1,220), and routes the VAT component through the regime schedule. No VAT-inclusive mode.
2. Each regime materializes as **synthetic derived items** (`_tax:<id>:liability` flow, `_tax:<id>:credit` stock) injected into the dependency graph before condensation — credit carry participates in feedback and the cash fold sees tax payments like any other flow. A VAT regime's base defaults to every item carrying a `VatSpec`; other regimes use an explicit tag selector.
3. The withholding counter-leg is **not generated** — consistent with §7.2's escape-hatch philosophy — but `validate()` warns (`CK-W004`) when withholding is in use and no `cat:tax` item covers the remittance. Loud absence, never silent.

## Consequences

- Agents and humans share one authoring convention; skill docs state it once.
- Tax flows are traceable with the same `trace()` machinery as everything else — no special-cased tax pass to explain.
- The withholding warning pushes the modelling burden to the user visibly, matching how IRES/INPS/TFR are already handled.
