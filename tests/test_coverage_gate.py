"""Session S5.6 — the coverage gate is configuration, so it is checked as such.

S5.5 measured ``engine/`` + ``model/`` at 97% with an ephemeral
``uv run --with pytest-cov`` and left the standing gate open. It is now
declared in ``pyproject.toml``:

* ``[tool.coverage.run] source`` — exactly ``cashkit/engine`` and
  ``cashkit/model``. Coverage of the SDK and the stores is a different question
  with a different answer; the 90% number in the PROMPT is about the two
  packages where a silent numerical error can hide.
* ``[tool.coverage.report] fail_under`` — what makes the run exit non-zero.
  The threshold lives in the file so no invocation can quietly lower it.
* ``addopts`` **without** ``--cov`` — the default ``uv run pytest`` stays
  uninstrumented, because coverage.py's trace hook is worth roughly 2.5x on the
  delta-recompute path and the §5.2 budgets would fail measuring it.

Which makes the gate two commands, and this file the thing that stops either
half from rotting:

    uv run pytest                            # everything, uninstrumented
    uv run pytest -m "not benchmark" --cov   # the gate, fails below 90%

Nothing here measures coverage. Running the suite inside the suite to assert a
percentage would double the wall clock to re-derive a number the gate command
already prints; what can silently break is the configuration, and that is what
is asserted.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path

REPO = Path(__file__).parent.parent
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))

#: The PROMPT's standing requirement for S5.6.
COVERED = ("cashkit/engine", "cashkit/model")
THRESHOLD = 90


def test_pytest_cov_is_a_declared_dev_dependency() -> None:
    """The number has to come from the dependency set, not from a remembered
    ``--with`` flag on somebody's shell history."""
    dev = PYPROJECT["dependency-groups"]["dev"]
    assert any(spec.startswith("pytest-cov") for spec in dev), dev


def test_the_gate_covers_the_engine_and_the_model_and_says_so() -> None:
    source = PYPROJECT["tool"]["coverage"]["run"]["source"]
    assert tuple(source) == COVERED
    for path in source:
        assert (REPO / path).is_dir(), f"{path} is not a package in this repo"


def test_the_threshold_is_declared_and_at_least_ninety() -> None:
    report = PYPROJECT["tool"]["coverage"]["report"]
    assert report["fail_under"] >= THRESHOLD
    assert report["show_missing"] is True, (
        "a gate that fails without naming the uncovered lines is a gate nobody "
        "can act on"
    )


def test_the_default_run_is_not_instrumented() -> None:
    """The §5.2 benchmarks measure the engine. Under coverage they measure
    coverage — verified: delta recompute goes from ~5 ms to ~12 ms — so a
    default run carrying ``--cov`` would turn every budget into a lie."""
    addopts = PYPROJECT["tool"]["pytest"]["ini_options"]["addopts"]
    assert "--cov" not in addopts, addopts


def test_the_benchmark_marker_the_gate_deselects_is_declared() -> None:
    markers = PYPROJECT["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("benchmark:") for marker in markers), markers


def test_every_timing_test_carries_the_marker_that_excludes_it() -> None:
    """``-m 'not benchmark'`` only protects what is marked.

    A timing test added without the marker would run under instrumentation and
    fail for a reason that has nothing to do with the engine — the kind of
    failure that gets a budget loosened rather than a cause found. Structural,
    so it holds for tests nobody has written yet: a function that reads
    ``perf_counter`` is a timing test.
    """
    unmarked: list[str] = []
    for path in sorted((REPO / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                # A nested helper is timed by the test that calls it; the marker
                # belongs on the test, which is what pytest deselects.
                continue
            names = {
                inner.attr
                for inner in ast.walk(node)
                if isinstance(inner, ast.Attribute)
            } | {
                inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)
            }
            if "perf_counter" not in names:
                continue
            marked = any(
                ast.unparse(decorator).endswith("benchmark")
                for decorator in node.decorator_list
            )
            if not marked:
                unmarked.append(f"{path.name}::{node.name}")
    assert not unmarked, (
        "timing tests missing @pytest.mark.benchmark, so the coverage run would "
        "instrument them:\n" + "\n".join(unmarked)
    )
