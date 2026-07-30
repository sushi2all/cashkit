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

__all__ = ["Engine", "RoundingPolicy", "RunResult", "run"]
