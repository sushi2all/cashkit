# PRD / PROMPT review — findings and resolutions

**Date** 2026-07-19. Pre-implementation review of `PRD-cashkit.md` and `PROMPT-fable5-implementation.md`. Every finding below was either fixed in the documents in this commit or explicitly deferred. ADR references point at `km/adr/`.

## Contradictions

| # | Finding | Resolution |
|---|---|---|
| C1 | Phases 2–3 gates need `prev()`/`agg()`/formulas; language was Phase 4 | Formula front-end moved into Phase 2; Phase 4 hardens (ADR-0001). PROMPT Phases 2 & 4 rewritten |
| C2 | Reference engine placed in both `cashkit/reference/` and `tests/reference/` | `cashkit/reference/`, exercised from `tests/property/`. PROMPT anti-pattern bullet fixed |
| C3 | D5 "Item is atom" vs rule 1 "item-level LWW" vs rule 3 "field-sparse merge" | Field-sparse resolution, segments atomic; D5 reworded (ADR-0009). §2 D5 + §4.6 rules rewritten |
| C4 | `why_zero`: PRD listed 4 causes, PROMPT gate demanded 5 | Five causes enumerated in §6.5 (segment, probability, upstream zero, cutover, settlement) |
| C5 | `import_events` "all-or-nothing" vs "skipped/conflicted counts" | Identical→skip, different payload→conflict aborts batch (ADR-0008). §6.2 docstring rewritten |
| C6 | §7.1 "FX as param time series" vs scalar `params` | §7.1 corrected to scalar params; series deferred (ADR-0010) |
| C7 | Phase 9 exact-reproduction gate vs engine_version in run identity | Snapshots record engine_version; exactness guaranteed at matching version (ADR-0006). §3.3, §10, PROMPT Phase 9 updated |
| C8 | §10 round-trip criterion written backwards (`serialize(parse(x))` over books) | Both directions stated correctly in §10 |
| C9 | "Base is not special" vs privileged top-level storage, undefined `base.yaml` | Special in storage only; semantics uniform (ADR-0007). §3.3 paragraph added |
| C10 | Watermark update timing undefined; imports would dirty tracked config | Stamped at `commit()`; live runs use full ledger (ADR-0006). §3.3 rewritten |

## Gaps

| # | Finding | Resolution |
|---|---|---|
| G1 | ~12 referenced models never defined (Money, Grain, CalendarSpec, PeriodRange/Ref, Watermark, Amount, Escalation, Duration, Diagnostic…) | New §4.0 Primitives defines them |
| G2 | `prev()` at `t < n` undefined | Yields `init` (default 0, literal or param ref) — §5.4 |
| G3 | Division under `where` (both branches always run) → div-by-zero unavoidable; `/` rounding unspecified | Masked-safe division: 0 elementwise, diagnostic only when selected; rounds at declared policy — §5.4 |
| G4 | VAT-inclusive vs exclusive never stated | All authored amounts net; engine grosses up cash leg (ADR-0005). §4.5 |
| G5 | Withholding had one leg; remittance/credit vanished | Counter-leg manual + `CK-W004` warning (ADR-0005). §4.4, §7.2 |
| G6 | Mixed currencies would sum silently | `CK-E020` error (ADR-0010). §5.3 |
| G7 | Cutover suppression rule per-item, questionable, boundary inclusivity undefined | Blanket pre-cutover suppression; boundary = first forecast period; post-cutover actuals warn (ADR-0004). New §3.2 paragraph; PROMPT Phase 5 aligned |
| G8 | TaxRegime↔engine integration unspecified (credit carry needs feedback participation) | Synthetic derived items injected pre-condensation (ADR-0005). §4.5 |
| G9 | Selector grammar (agg/retag/accumulates/where) undefined | ANDed `key:value` / `flag:name` terms; no OR/negation v1 — §5.4 |
| G10 | Rounding order across escalation×probability×split×withholding×VAT unspecified — byte-equality gate underdetermined | Canonical order fixed; last share term absorbs residual (ADR-0003). §5.3 |
| G11 | Generative `kind="stock"` semantics undefined | Stock valid on derived items only in v1; generative stock rejected — §4.2 |
| G12 | `direction` display-only + signed storage = silent sign-flip footgun for agents | `add_item()` rejects sign/direction contradiction (`CK-E011`) — §4.2 |
| G13 | `why_zero` referenced "upstream null" but no null exists in the int64 core | Cause list rewritten without null (see C4) |
| G14 | Forecast-event lifecycle (cancel/amend) missing; deletion would break watermark | `void_event` tombstone, refuses actuals (ADR-0008). §6.2 |
| G15 | Param keys dotted (`vat.standard`) but formula access `p.vat_standard` — mapping/collisions undefined | Keys restricted to `[a-z][a-z0-9_]*`; dotted keys rejected. §4.1, §4.5 default updated |
| G16 | Recurrence `day=31` in short months; legal `unit` values | Clamp to month end; documented — §4.2 |
| G17 | Concurrent-writer "fails loudly" had no mechanism | `.cashkit/lock` lockfile (ADR-0010). §6.6 |
| G18 | `Amount` "expression" variant: language and DAG participation undefined | Dropped in v1 — constant \| schedule only; expressions belong to derived items. §4.0, §4.2 |

## Correctness risks

| # | Finding | Resolution |
|---|---|---|
| R1 | float64 `(1+r)^n` vs Decimal reference diverges on half-up ties → gate likely unsatisfiable | Decimal factor table; float removed from money path (ADR-0002). §5.3, PROMPT constraint 1 |
| R2 | int64 intermediate overflow in scale→multiply→divide near stated max | Arbitrary-precision/checked intermediates; overflow raises — §5.3 |
| R3 | `holidays` package version drift silently changes historical business-day rolls | Holiday dates resolved at book creation and committed (ADR-0010). §4.0 CalendarSpec |
| R4 | Phase 11 gate says agent "installs CashKit" — not on PyPI | Gate now says local wheel provided by harness. PROMPT Phase 11 |
| R5 | Settlement clamp ambiguity (fixed > accrual); negative accruals (credit notes) unspecified | Fixed legs pay in full; remainder clamps with warning; negative accruals sign-symmetric for shares, warn+remainder for fixed — §4.4 |
| R6 | Working dir was not a git repo despite per-gate commit requirement | `git init` done, v1 tagged (this repo) |

## Improvements applied

- Wall-clock lint extended to `now()`/`utcnow()`/`datetime.today()`/`time.time()` — PROMPT constraint 3 (ADR-0010).
- Initial diagnostic catalogue added as §10.1 (~22 codes) so phases 4–9 emit consistent codes instead of retrofitting.
- `opening_balance` is a reserved param key, so scenarios can sweep it (capital injection cases) — §4.1.
- CLI gains `status` / `commit` / `history` for the human-in-shell user — §8.4, PROMPT Phase 10.
- Phase 3 dual-engine corpus extended with `probability < 1`, withholding, mixed-sign amounts — where rounding-order divergence actually bites.
- `serve --quack` marked feature-flagged in the Phase 10 CLI list, consistent with §3.4.

## Not changed (noted, deliberate)

- `Settlement` on derived items: left unspecified; will surface in Phase 2 and get a DECISIONS.md entry.
- `Scenario.removed` + descendant re-`added` resolution: one-liner ambiguity, low risk, defer to Phase 7.
- `Event.status="committed"` has no distinct engine semantics beyond filtering; acceptable for v1.
