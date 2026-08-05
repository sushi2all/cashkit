"""Phase 3 gate: the two engines agree byte-for-byte.

``cashkit.reference`` computes every cell as a scalar ``Decimal`` quantized at
each declared rounding boundary; ``cashkit.engine`` computes whole columns of
int64 minor units, folding only the genuine feedback sets. They share the model,
the formula front-end, the escalation factor table, the graph and the recurrence
generator — and duplicate every piece of arithmetic. Agreement between two
independent implementations of the canonical rounding order (ADR-0003) is the
only evidence that either is right.

Zero tolerance: exact integer equality on every cell of every column, plus
agreement on which diagnostics were raised.
"""

from __future__ import annotations

import numpy as np
import pytest
from corpus import LEDGER_COVERAGE, REQUIRED_COVERAGE, build_corpus, coverage_of

import cashkit.engine as engine
import cashkit.reference as reference
from cashkit.engine.numeric import RoundingPolicy

CORPUS = build_corpus()


def _compare(case, policy: RoundingPolicy) -> list[str]:
    expected = reference.run(case.book, policy=policy, events=case.events)
    actual = engine.run(case.book, policy=policy, events=case.events)
    problems: list[str] = []
    if expected.diagnostic_keys() != actual.diagnostic_keys():
        problems.append(
            "diagnostics differ:\n"
            f"  reference:  {expected.diagnostic_keys()}\n"
            f"  vectorized: {actual.diagnostic_keys()}"
        )
    if sorted(expected.accrual) != sorted(actual.accrual):
        problems.append("item sets differ")
        return problems
    for item_id in sorted(expected.accrual):
        for measure in ("accrual", "cash"):
            left = expected.column(item_id, measure)
            right = actual.column(item_id, measure)
            if left.shape != right.shape:
                problems.append(f"{item_id}.{measure}: shape {left.shape} vs {right.shape}")
                continue
            differing = np.flatnonzero(left != right)
            if differing.size:
                first = int(differing[0])
                problems.append(
                    f"{item_id}.{measure}: {differing.size} cells differ, first at "
                    f"period {first} ({expected.periods.starts[first]}): "
                    f"reference {int(left[first])} vs vectorized {int(right[first])}"
                )
    return problems


def test_corpus_is_at_least_fifty_books() -> None:
    assert len(CORPUS) >= 50, f"the gate requires >= 50 books, corpus has {len(CORPUS)}"


def test_corpus_ids_are_unique() -> None:
    ids = [case.book.id for case in CORPUS]
    assert len(ids) == len(set(ids))


def test_corpus_covers_every_feature_the_gate_names() -> None:
    """Coverage is re-derived from the books, so it cannot drift from them."""
    missing = (REQUIRED_COVERAGE | LEDGER_COVERAGE) - coverage_of(CORPUS)
    assert not missing, f"corpus does not exercise: {sorted(missing)}"


@pytest.mark.parametrize("case", CORPUS, ids=[case.book.id for case in CORPUS])
def test_engines_agree_byte_for_byte(case) -> None:
    """The gate itself."""
    problems = _compare(case, RoundingPolicy.HALF_UP)
    assert not problems, f"{case.description} ({case.book.id}):\n" + "\n".join(problems)


@pytest.mark.parametrize("case", CORPUS[::5], ids=[case.book.id for case in CORPUS[::5]])
def test_engines_agree_under_bankers_rounding(case) -> None:
    """The rounding policy is a run-level knob (D-P2-01); both engines must
    honour it identically, not only the default."""
    problems = _compare(case, RoundingPolicy.HALF_EVEN)
    assert not problems, (
        f"{case.description} ({case.book.id}) under HALF_EVEN:\n" + "\n".join(problems)
    )


@pytest.mark.parametrize("case", CORPUS[::7], ids=[case.book.id for case in CORPUS[::7]])
def test_delta_recompute_reproduces_a_full_run(case) -> None:
    """The delta path must be an optimization, never a different answer.

    Replacing an item with itself moves no numbers, but it walks the whole
    recompile-and-recompute path, so a stale cached column would show up here.
    """
    full = engine.run(case.book, events=case.events)
    incremental = engine.Engine(case.book, events=case.events)
    incremental.run()
    changed = dict(list(case.book.items.items())[:1])
    after = incremental.delta(changed)
    for item_id in sorted(full.accrual):
        for measure in ("accrual", "cash"):
            assert np.array_equal(
                full.column(item_id, measure), after.column(item_id, measure)
            ), f"{case.description}: delta diverged on {item_id}.{measure}"
    assert full.diagnostic_keys() == after.diagnostic_keys()


def test_run_is_reproducible_across_repeated_evaluation() -> None:
    """PRD §10: a run at a given input is byte-identical across processes. The
    in-process half of that is that nothing accumulates state between runs."""
    for case in CORPUS[::9]:
        first = engine.run(case.book, events=case.events)
        second = engine.run(case.book, events=case.events)
        for item_id in sorted(first.accrual):
            for measure in ("accrual", "cash"):
                assert np.array_equal(
                    first.column(item_id, measure), second.column(item_id, measure)
                )
