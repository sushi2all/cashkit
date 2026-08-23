"""The agent layer: the only part of the service that talks to a model.

Everything here obeys four rules that come from outside it:

* **The model never derives a number.** It emits intents and quotes engine
  output (ADR-0030 stage 3). The results block in :mod:`.snapshot` is what
  makes quoting possible; the read intents in
  :mod:`cashkit_service.intents.read` are what it quotes.
* **The model never writes.** Mutation intents are held as a proposal, always,
  whatever the turn looked like (ADR-0029). :mod:`.guard` is that rule as code.
* **The model never sees a host op or a raw SDK verb.** Its surface is the 21
  intents plus the one host read tool ``query_ledger`` (SPEC §2.3, ADR-0030).
* **A model call never holds the book lock** (SPEC §2.2).
"""
