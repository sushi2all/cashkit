"""Phase 3 performance gate (PRD §5.2 budgets).

Two numbers are gated: a cold full run of 50 items x 1826 periods under 50 ms,
and a delta recompute after one item changes under 5 ms. Both are measured as
the best of several repetitions — the budget is a statement about the work the
design does, and a scheduler hiccup on a shared machine is not evidence about
that. The measured medians are recorded in ``BENCHMARKS.md`` with the hardware.

The shape is the PRD's own benchmark: 40 generative flows with escalation and
settlement lags, 8 derived, 2 in a feedback loop, at day grain over five years.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest
from gate_book import build_benchmark_book

from cashkit.engine.run import Engine

#: PRD §5.2.
COLD_BUDGET_MS = 50.0
DELTA_BUDGET_MS = 5.0

BOOK = build_benchmark_book(items=50, years=5)


def _best(measure, repeats: int) -> float:
    return min(measure() for _ in range(repeats))


def test_benchmark_book_matches_the_prd_shape() -> None:
    assert len(BOOK.items) == 50
    periods = (BOOK.horizon.end - BOOK.horizon.start).days
    assert periods == 1826, periods
    engine = Engine(BOOK)
    assert any(not component.trivial for component in engine.compiled.components)
    assert not engine.compiled.has_errors


@pytest.mark.benchmark
def test_cold_run_is_within_budget() -> None:
    def once() -> float:
        started = time.perf_counter()
        Engine(BOOK).run()
        return (time.perf_counter() - started) * 1000

    once()  # warm the import-time caches, not the engine: each run compiles afresh
    best = _best(once, 7)
    assert best < COLD_BUDGET_MS, f"cold run {best:.1f} ms exceeds {COLD_BUDGET_MS} ms"


@pytest.mark.benchmark
def test_delta_recompute_is_within_budget() -> None:
    engine = Engine(BOOK)
    engine.run()
    item = BOOK.items["gen_000"]
    first, second = item.segments
    counter = iter(range(1, 10_000))

    def once() -> float:
        amount = first.amount.model_copy(update={"constant": Decimal(next(counter))})
        changed = item.model_copy(
            update={"segments": [first.model_copy(update={"amount": amount}), second]}
        )
        started = time.perf_counter()
        engine.delta({"gen_000": changed})
        return (time.perf_counter() - started) * 1000

    once()
    best = _best(once, 15)
    assert best < DELTA_BUDGET_MS, (
        f"delta recompute {best:.2f} ms exceeds {DELTA_BUDGET_MS} ms"
    )


@pytest.mark.benchmark
def test_delta_touches_only_the_dependency_cone() -> None:
    """The delta budget rests on recomputing a cone, not the book. If the cone
    ever silently became 'everything', the timing would still pass on a small
    book — so the cone itself is asserted."""
    engine = Engine(BOOK)
    engine.run()
    cone = engine.compiled.downstream({"gen_000"})
    assert "gen_000" in cone and "cash" in cone
    assert len(cone) < len(BOOK.items) // 2, sorted(cone)
