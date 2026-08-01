"""The staged fold compiler agrees with the generic evaluator, node for node.

``cashkit.engine.fold`` exists only for speed: it resolves everything that does
not vary with the period during one walk of the AST, so the sequential fold pays
for arithmetic rather than dispatch. That optimization is only legitimate if the
staged closure computes exactly what
:class:`~cashkit.engine.columns.ColumnEvaluator` computes under its scalar
kernel — same value, same ``zero_div`` flag, same rounding boundaries, in every
period.

This test asserts that directly, over every node type and over randomized
columns, so a drift shows up here with a formula and a period rather than three
layers away as a wrong number in a book.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pytest

from cashkit.engine.calendars import BusinessCalendar, PeriodIndex
from cashkit.engine.columns import ColumnEvaluator, EvalWindow, TimeColumns
from cashkit.engine.fold import compile_cell
from cashkit.engine.graph import compile_book
from cashkit.engine.numeric import RoundingPolicy
from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
)

#: Every node type the language has, and the promotion corners between them.
FORMULAS = [
    "0",
    "-3",
    "p.rate",
    "-p.rate",
    "p.rate * p.other",
    "p.rate / p.other",
    "p.rate + p.other",
    "1 / 0",
    "1 / 0 + p.rate",
    "t.index",
    "t.month",
    "t.is_quarter_end",
    "t.is_business_day",
    "not t.is_business_day",
    "t.is_business_day and t.is_quarter_end",
    "t.is_business_day or t.is_quarter_end",
    'it("alpha")',
    'it("alpha", measure="accrual")',
    'it("gamma")',
    'it("gamma", measure="accrual")',
    'prev("alpha")',
    'prev("alpha", n=3, init=-250)',
    'prev("alpha", n=7, init=p.rate)',
    'prev("gamma", n=2, init=0)',
    'cum("alpha", measure="accrual")',
    'cum("beta")',
    'agg(tag="cat:flow", measure="accrual")',
    'agg(tag="flag:cashflow")',
    'it("alpha", measure="accrual") + it("beta", measure="accrual")',
    'it("alpha", measure="accrual") - it("beta", measure="accrual")',
    'it("alpha", measure="accrual") * it("beta", measure="accrual")',
    'it("alpha", measure="accrual") / it("beta", measure="accrual")',
    'it("alpha", measure="accrual") * p.rate',
    'p.rate * it("alpha", measure="accrual")',
    'it("alpha", measure="accrual") / p.rate',
    'p.rate / it("alpha", measure="accrual")',
    'it("alpha", measure="accrual") / 0',
    '-it("alpha", measure="accrual")',
    'abs_(it("beta", measure="accrual"))',
    'round_(it("alpha", measure="accrual"))',
    'round_(it("alpha", measure="accrual") * p.rate, ndigits=2)',
    'round_(it("alpha", measure="accrual") * p.rate, ndigits=4)',
    'clip(it("alpha", measure="accrual"), -1000, 1000)',
    'min(it("alpha", measure="accrual"), it("beta", measure="accrual"), 0)',
    'max(it("alpha", measure="accrual"), p.rate, -5)',
    'where(t.is_business_day, it("alpha", measure="accrual"), it("beta", measure="accrual"))',
    'where(it("beta", measure="accrual") == 0, 0, '
    'it("alpha", measure="accrual") / it("beta", measure="accrual"))',
    'where(it("beta", measure="accrual") != 0, '
    'it("alpha", measure="accrual") / it("beta", measure="accrual"), -1)',
    'where(it("alpha", measure="accrual") > it("beta", measure="accrual"), 1, 2)',
    'where(it("alpha", measure="accrual") >= 0 and t.is_quarter_end, '
    'abs_(it("beta")) * p.rate, min(it("alpha"), 0))',
    'where(not (t.index < 5), cum("alpha", measure="accrual") / 3, prev("alpha", n=2, init=7))',
    'where(t.is_business_day, 1 / 0, it("alpha", measure="accrual"))',
    'where(t.is_business_day, it("alpha", measure="accrual"), 1 / 0)',
    '(it("alpha", measure="accrual") + 1) * (p.rate - 2) / 3',
    'agg(tag="cat:flow", measure="accrual") * p.rate '
    '- agg(tag="flag:cashflow") / p.other',
]

PARAMS = {"rate": Decimal("0.0725"), "other": Decimal("3.5"), "opening_balance": Decimal("1000")}


def _probe_book() -> Book:
    """Three generative items plus one derived item per formula under test."""
    segment = Segment(
        start=date(2026, 1, 1),
        recurrence=Recurrence(every=1, unit=Grain.MONTH),
        amount=Amount(constant=Decimal("1000")),
    )
    items: dict[str, Item] = {
        name: Item(
            id=name,
            name=name,
            kind="flow",
            tags={"cat": "flow"},
            flags={"cashflow"},
            segments=[segment],
        )
        for name in ("alpha", "beta")
    }
    items["gamma"] = Item(
        id="gamma",
        name="gamma",
        kind="stock",
        tags={"cat": "balance"},
        formula='prev("gamma", init=0) + it("alpha", measure="accrual")',
        agg_rule="last",
    )
    for index, formula in enumerate(FORMULAS):
        items[f"probe_{index:03d}"] = Item(
            id=f"probe_{index:03d}",
            name=f"probe {index}",
            kind="derived",
            tags={"cat": "probe"},
            formula=formula,
        )
    return Book(
        id="fold-probe",
        base_grain=Grain.DAY,
        calendar=CalendarSpec(
            fiscal_year_start_month=7,
            holidays=[date(2026, 1, 1), date(2026, 1, 6)],
            weekend={5, 6},
        ),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 2, 21)),
        opening_balance=Decimal("1000"),
        cutover=date(2026, 1, 1),
        params=dict(PARAMS),
        items=items,
    )


BOOK = _probe_book()
COMPILED = compile_book(BOOK)
PERIODS = PeriodIndex.build(BOOK.horizon, BOOK.base_grain, 7)
CALENDAR = BusinessCalendar.from_spec(BOOK.calendar)
TIME = TimeColumns.build(PERIODS, CALENDAR)


def _window(seed: int) -> EvalWindow:
    """A window over deliberately awkward columns: zeros, ties, negatives."""
    rng = np.random.default_rng(seed)
    length = len(PERIODS)
    accrual: dict[str, np.ndarray] = {}
    cash: dict[str, np.ndarray] = {}
    for name in BOOK.items:
        accrual[name] = rng.integers(-50_000, 50_000, length, dtype=np.int64)
        cash[name] = rng.integers(-50_000, 50_000, length, dtype=np.int64)
    # Zeros are the interesting case for masked-safe division; halves are the
    # interesting case for rounding.
    accrual["beta"][::4] = 0
    cash["beta"][::5] = 0
    accrual["alpha"][::3] = 5_000
    accrual["alpha"][1::7] = -5_000
    return EvalWindow(
        accrual=accrual,
        cash=cash,
        kinds={name: item.kind for name, item in BOOK.items.items()},
        params=dict(BOOK.params),
        opening_balance=BOOK.opening_balance,
        time=TIME,
        start=0,
        stop=length,
    )


def test_every_formula_in_the_probe_book_compiles() -> None:
    """A rejected probe would silently drop a node type from the comparison."""
    broken = [
        item_id
        for item_id, entry in COMPILED.items.items()
        if item_id.startswith("probe_") and entry.expr is None
    ]
    assert not broken, f"probe formulas rejected: {broken}"


@pytest.mark.parametrize("policy", list(RoundingPolicy))
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_staged_cells_match_the_generic_scalar_evaluator(
    policy: RoundingPolicy, seed: int
) -> None:
    window = _window(seed)
    length = len(PERIODS)
    mismatches: list[str] = []
    for index, formula in enumerate(FORMULAS):
        expr = COMPILED.items[f"probe_{index:03d}"].expr
        assert expr is not None
        cell = compile_cell(expr, window, policy)
        for period in range(length):
            window.start = period
            window.stop = period + 1
            evaluator = ColumnEvaluator(window, policy, scalar=True)
            expected = evaluator.to_money(evaluator.eval(expr))
            value, zero_div = cell(period)
            if (value, zero_div) != (int(expected.value), bool(expected.zero_div)):
                mismatches.append(
                    f"{formula!r} at period {period}: staged "
                    f"({value}, {zero_div}) vs generic "
                    f"({int(expected.value)}, {bool(expected.zero_div)})"
                )
    window.start, window.stop = 0, length
    assert not mismatches, "staged fold diverged:\n" + "\n".join(mismatches[:20])


@pytest.mark.parametrize("policy", list(RoundingPolicy))
@pytest.mark.parametrize("seed", [1, 2])
def test_staged_cells_match_the_whole_column_evaluator(
    policy: RoundingPolicy, seed: int
) -> None:
    """The two tiers must produce the same numbers, or a feedback item and a
    plain derived item computing the same expression would disagree."""
    window = _window(seed)
    length = len(PERIODS)
    mismatches: list[str] = []
    for index, formula in enumerate(FORMULAS):
        expr = COMPILED.items[f"probe_{index:03d}"].expr
        assert expr is not None
        window.start, window.stop = 0, length
        evaluator = ColumnEvaluator(window, policy)
        column = evaluator.to_money(evaluator.eval(expr))
        cell = compile_cell(expr, window, policy)
        staged = np.fromiter(
            (cell(period)[0] for period in range(length)), dtype=np.int64, count=length
        )
        differing = np.flatnonzero(np.asarray(column.value) != staged)
        if differing.size:
            first = int(differing[0])
            mismatches.append(
                f"{formula!r} at period {first}: column "
                f"{int(np.asarray(column.value)[first])} vs staged {int(staged[first])}"
            )
        flags = np.fromiter(
            (cell(period)[1] for period in range(length)), dtype=bool, count=length
        )
        if not np.array_equal(np.asarray(column.zero_div), flags):
            mismatches.append(f"{formula!r}: zero_div flags differ")
    assert not mismatches, "tier disagreement:\n" + "\n".join(mismatches[:20])


def test_dispatch_table_covers_every_node_type() -> None:
    """The staged compiler dispatches on exact type; a new node type must not
    fall through to a KeyError at run time."""
    from cashkit.engine import columns, fold, formula

    node_types = {
        formula.Literal, formula.Param, formula.TimeField, formula.ItemRef,
        formula.Prev, formula.Agg, formula.Cum, formula.Unary, formula.Binary,
        formula.Compare, formula.Logical, formula.Where, formula.Builtin,
    }
    assert set(fold._DISPATCH) == node_types
    assert set(columns._DISPATCH) == node_types
