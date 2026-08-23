"""The spreadsheet import pipeline (SPEC §7, ADR-0030 stage 4).

Import is the one free-running agentic loop in this product. Everything else is
bounded and single-call by design; this is the deliberate exception, because a
spreadsheet import is rare, latency-tolerant and the highest-stakes thing a new
user does. The loop is still bounded — twenty model calls, then the partial
result is presented honestly rather than finished by guessing.

Four rules hold the whole module together, and each has a test:

* **Nothing lands without an applied proposal.** The loop produces one
  proposal (origin ``import``) and a reconciliation report. `POST /import`
  applies nothing (ADR-0029, SPEC §7.4).
* **The target is host-decided, never model-decided** (SPEC §7.3). On an empty
  book import authors into base; on a non-empty book it authors into a fresh
  fork named from the filename, never into base. Every authored operation is
  stamped with the target host-side, after the guard, so a model that names a
  scenario cannot move the change somewhere else.
* **The model never supplies a check figure.** It may say *which* cell of the
  sheet is a total and *what that total means*; the value is read out of the
  workbook by the host, and the engine's side of the comparison is computed by
  the engine. A reconciliation the model could satisfy by asserting is not a
  reconciliation.
* **A 1-cent divergence is labelled, never absorbed** (SPEC §7.5). The engine
  works in int64 at 4dp with banker's rounding and Excel uses float ``ROUND``;
  they disagree on exact ties. The report says so where it happens.
"""
