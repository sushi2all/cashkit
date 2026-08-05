"""Storage backends: config (YAML+git), ledger (SQLite), frames (DuckDB).

Three stores, one rule each about what may leak out of them:

* :mod:`cashkit.stores.ledger` is the only module that imports the SQLite
  driver (spelled out there, not here — the structural test that enforces this
  greps the package, and prose naming the module would look like a violation);
* :mod:`cashkit.stores.frames` is the only module that imports ``duckdb``;
* :mod:`cashkit.stores.git_store` is the only module that imports ``pygit2``.

Each is reached through a protocol — :class:`~cashkit.stores.ledger.LedgerStore`,
:class:`~cashkit.stores.frames.FrameStore`,
:class:`~cashkit.stores.revisions.RevisionStore` — so a second implementation of
any of them changes one file (ADR-0018). :mod:`cashkit.stores.config` owns the
PRD §3.3 on-disk layout and its forward-only migrations;
:mod:`cashkit.stores.lock` is the single-writer lock covering all three stores;
:mod:`cashkit.stores.clock` is the package's one wall-clock read, exempted from
the determinism lint for commit and lock timestamps and reachable from neither
the engine nor the model.
"""
