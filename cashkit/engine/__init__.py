"""Evaluation engine: graph, condensation, columns, fold, formula AST.

``cashkit.engine.run(book)`` is the vectorized evaluator: it builds the
dependency graph including ``prev()`` edges, condenses it into strongly connected
components, evaluates trivial components as whole-horizon int64 column
expressions and folds the genuine feedback sets sequentially (PRD §5.1).

Its output is byte-identical to ``cashkit.reference.run`` — exact integer
equality on every cell — which is what the dual-engine gate proves on every test
run. Nothing here reads the wall clock or uses ``float``; both are enforced by
lint tests.
"""

from .numeric import RoundingPolicy
from .result import RunResult
from .run import Engine, run

#: Evaluation-semantics generation. A run is identified by
#: ``(revision_sha, scenario_id, engine_version, ledger_watermark)`` (PRD §6.6),
#: so this string must change whenever a change to this package could move a
#: number — and must *not* change for a refactor that cannot. It is recorded in
#: every committed snapshot; ``at(ref)`` guarantees exact reproduction only at a
#: matching value and reports the delta otherwise (ADR-0006), never silently.
ENGINE_VERSION = "1"

__all__ = ["ENGINE_VERSION", "Engine", "RoundingPolicy", "RunResult", "run"]
