"""Phase 4 gate: every symbol in PRD §5.4 is a column operation.

"Column operation" means two things, and both are asserted here for every
symbol in the table:

* **the whole-horizon result is the elementwise result** — evaluating the symbol
  once over the full horizon equals evaluating it period by period. If that ever
  stopped holding, the same formula would mean different things in a trivial
  component and inside a feedback fold;
* **the values are what a hand-written `Decimal` reading of §5.4 says they are**
  — checked against a small oracle written here, independent of both engines, so
  a shared misreading of the spec cannot pass by agreeing with itself.

`test_every_documented_symbol_is_covered` derives the required symbol set from
the parser's own tables, so adding a builtin without a vectorization test fails
the gate rather than slipping through.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable

import numpy as np
import pytest

from cashkit.engine.calendars import BusinessCalendar, PeriodIndex
from cashkit.engine.columns import ColumnEvaluator, EvalWindow, TimeColumns
from cashkit.engine.fold import compile_cell
from cashkit.engine.formula import NUMERIC_BUILTINS, TIME_FIELDS
from cashkit.engine.graph import compile_book
from cashkit.engine.numeric import MINOR_SCALE, RoundingPolicy
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

POLICY = RoundingPolicy.HALF_UP
RATE = Decimal("0.0725")
OTHER = Decimal("2.5")
ONE_DAY = timedelta(days=1)

HORIZON = PeriodRange(start=date(2026, 1, 1), end=date(2026, 3, 2))
CALENDAR = CalendarSpec(
    fiscal_year_start_month=7, holidays=[date(2026, 1, 1), date(2026, 1, 6)], weekend={5, 6}
)


def q(value: Decimal, places: str = "0.0001") -> Decimal:
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def minor(value: Decimal) -> int:
    return int(q(value).scaleb(4))


# --------------------------------------------------------------------------- #
# The probe book and its columns
# --------------------------------------------------------------------------- #

#: Deliberately awkward: zeros for masked division, exact halves for rounding
#: ties, negatives for sign symmetry.
A_VALUES = [
    12_345_678, -12_345_678, 0, 5_000, -5_000, 1, -1, 99_999_999, 2_500,
    -2_500, 7_777, 0, 15_000, -15_000, 33_333, 100_000, -100_000, 4_999,
    -4_999, 65_432, 0, 8_888, -8_888, 25_000, -25_000, 1_234_567, 0,
    -1_234_567, 500_000, -500_000, 314_159, 271_828, 0, 161_803, -161_803,
    141_421, 173_205, 223_606, 0, 264_575, -264_575, 300_000, 1_000_000,
    -1_000_000, 45_000, -45_000, 6_666, 0, 9_999, -9_999, 707_106, 866_025,
    -866_025, 123_456, 0, 654_321, 111_111, -111_111, 222_222, 333_333,
]
B_VALUES = [
    3_000, 0, 7_500, -7_500, 0, 20_000, -20_000, 1, 0, -1, 40_000, 0,
    -40_000, 3_333, 0, 66_666, -66_666, 0, 12_500, -12_500, 0, 90_000,
    -90_000, 0, 55_555, 0, 77_777, -77_777, 0, 10_000, -10_000, 0, 5_555,
    0, 8_000, -8_000, 0, 60_000, -60_000, 0, 2_222, 0, 44_444, -44_444, 0,
    99_000, -99_000, 0, 31_415, 0, -31_415, 27_182, 0, 16_180, -16_180, 0,
    14_142, 17_320, 0, 22_360,
]

FORMULAS: dict[str, str] = {
    "it": 'it("a", measure="accrual")',
    "it_cash": 'it("a")',
    "prev": 'prev("a", n=2, init=5, measure="accrual")',
    "prev_param_init": 'prev("a", n=3, init=p.rate, measure="accrual")',
    "param": "p.rate",
    "agg": 'agg(tag="cat:probe", measure="accrual")',
    "agg_flag": 'agg(tag="flag:cashflow", measure="accrual")',
    "cum": 'cum("a", measure="accrual")',
    "t.index": "t.index",
    "t.month": "t.month",
    "t.is_quarter_end": "where(t.is_quarter_end, 1, 0)",
    "t.is_business_day": "where(t.is_business_day, 1, 0)",
    "where": 'where(it("b", measure="accrual") > 0, '
    'it("a", measure="accrual"), -it("a", measure="accrual"))',
    "min": 'min(it("a", measure="accrual"), it("b", measure="accrual"), 0)',
    "max": 'max(it("a", measure="accrual"), p.rate)',
    "clip": 'clip(it("a", measure="accrual"), -1000, 1000)',
    "round_": 'round_(it("a", measure="accrual"))',
    "round_ndigits": 'round_(it("a", measure="accrual"), ndigits=2)',
    "abs_": 'abs_(it("a", measure="accrual"))',
    "add": 'it("a", measure="accrual") + it("b", measure="accrual")',
    "sub": 'it("a", measure="accrual") - it("b", measure="accrual")',
    "mul_money": 'it("a", measure="accrual") * it("b", measure="accrual")',
    "mul_rate": 'it("a", measure="accrual") * p.rate',
    "div_money": 'it("a", measure="accrual") / it("b", measure="accrual")',
    "div_rate": 'it("a", measure="accrual") / p.other',
    "neg": '-it("a", measure="accrual")',
    "compare": 'where(it("a", measure="accrual") >= it("b", measure="accrual"), 1, 0)',
    "logical": "where(t.is_business_day and not t.is_quarter_end, 1, 0)",
}


def _probe_book() -> Book:
    segment = Segment(
        start=date(2026, 1, 1),
        recurrence=Recurrence(every=1, unit=Grain.MONTH),
        amount=Amount(constant=Decimal("1")),
    )
    items: dict[str, Item] = {
        name: Item(
            id=name,
            name=name,
            kind="flow",
            tags={"cat": "probe"},
            flags={"cashflow"},
            segments=[segment],
        )
        for name in ("a", "b")
    }
    for key, formula in FORMULAS.items():
        items[_probe_id(key)] = Item(
            id=_probe_id(key),
            name=key,
            kind="derived",
            tags={"cat": "derived"},
            formula=formula,
        )
    return Book(
        id="builtin-probe",
        base_grain=Grain.DAY,
        calendar=CALENDAR,
        horizon=HORIZON,
        opening_balance=Decimal("1000"),
        cutover=date(2026, 1, 1),
        params={"rate": RATE, "other": OTHER},
        items=items,
    )


def _probe_id(key: str) -> str:
    return "probe_" + key.replace(".", "_")


BOOK = _probe_book()
COMPILED = compile_book(BOOK)
PERIODS = PeriodIndex.build(HORIZON, Grain.DAY, 7)
BUSINESS = BusinessCalendar.from_spec(CALENDAR)
TIME = TimeColumns.build(PERIODS, BUSINESS)
LENGTH = len(PERIODS)

assert LENGTH == len(A_VALUES) == len(B_VALUES), (LENGTH, len(A_VALUES))


def _window() -> EvalWindow:
    accrual = {
        "a": np.array(A_VALUES, dtype=np.int64),
        "b": np.array(B_VALUES, dtype=np.int64),
    }
    cash = {
        "a": np.array(A_VALUES, dtype=np.int64) // 2,
        "b": np.array(B_VALUES, dtype=np.int64) // 2,
    }
    for name in BOOK.items:
        accrual.setdefault(name, np.zeros(LENGTH, dtype=np.int64))
        cash.setdefault(name, np.zeros(LENGTH, dtype=np.int64))
    return EvalWindow(
        accrual=accrual,
        cash=cash,
        kinds={name: item.kind for name, item in BOOK.items.items()},
        params=dict(BOOK.params),
        opening_balance=BOOK.opening_balance,
        time=TIME,
        start=0,
        stop=LENGTH,
    )


# --------------------------------------------------------------------------- #
# The independent oracle: PRD §5.4 read by hand, in Decimal
# --------------------------------------------------------------------------- #


def _money(values: list[int]) -> list[Decimal]:
    return [Decimal(value).scaleb(-4) for value in values]


A = _money(A_VALUES)
B = _money(B_VALUES)
A_CASH = _money([value // 2 for value in A_VALUES])


def _select(mask: list[bool], when_true: list[Decimal], when_false: list[Decimal]):
    return [t if m else f for m, t, f in zip(mask, when_true, when_false)]


def _quarter_ends() -> list[bool]:
    """`t.is_quarter_end` reads the period's inclusive end date (D-P2-07)."""
    return [PERIODS.is_quarter_end(end - ONE_DAY) for end in PERIODS.ends]


def _business_days() -> list[bool]:
    return [BUSINESS.is_business_day(day) for day in PERIODS.starts]


def _cumulative(values: list[Decimal]) -> list[Decimal]:
    running = Decimal(0)
    out: list[Decimal] = []
    for value in values:
        running += value
        out.append(running)
    return out


def _lagged(values: list[Decimal], lag: int, init: Decimal) -> list[Decimal]:
    return [values[index - lag] if index >= lag else init for index in range(LENGTH)]


def _divide(top: Decimal, bottom: Decimal) -> Decimal:
    if bottom == 0:
        return Decimal(0)
    return q(top / bottom)


ORACLES: dict[str, Callable[[], list[Decimal]]] = {
    "it": lambda: list(A),
    "it_cash": lambda: list(A_CASH),
    "prev": lambda: _lagged(A, 2, q(Decimal(5))),
    "prev_param_init": lambda: _lagged(A, 3, q(RATE)),
    "param": lambda: [q(RATE)] * LENGTH,
    "agg": lambda: [a + b for a, b in zip(A, B)],
    "agg_flag": lambda: [a + b for a, b in zip(A, B)],
    "cum": lambda: _cumulative(A),
    "t.index": lambda: [Decimal(index) for index in range(LENGTH)],
    "t.month": lambda: [Decimal(day.month) for day in PERIODS.starts],
    "t.is_quarter_end": lambda: [
        Decimal(1) if flag else Decimal(0) for flag in _quarter_ends()
    ],
    "t.is_business_day": lambda: [
        Decimal(1) if flag else Decimal(0) for flag in _business_days()
    ],
    "where": lambda: _select([b > 0 for b in B], list(A), [-a for a in A]),
    "min": lambda: [min(a, b, Decimal(0)) for a, b in zip(A, B)],
    "max": lambda: [max(a, q(RATE)) for a in A],
    "clip": lambda: [min(max(a, Decimal(-1000)), Decimal(1000)) for a in A],
    "round_": lambda: [q(q(a, "1")) for a in A],
    "round_ndigits": lambda: [q(q(a, "0.01")) for a in A],
    "abs_": lambda: [abs(a) for a in A],
    "add": lambda: [a + b for a, b in zip(A, B)],
    "sub": lambda: [a - b for a, b in zip(A, B)],
    "mul_money": lambda: [q(a * b) for a, b in zip(A, B)],
    "mul_rate": lambda: [q(a * RATE) for a in A],
    "div_money": lambda: [_divide(a, b) for a, b in zip(A, B)],
    "div_rate": lambda: [q(a / OTHER) for a in A],
    "neg": lambda: [-a for a in A],
    "compare": lambda: [
        Decimal(1) if a >= b else Decimal(0) for a, b in zip(A, B)
    ],
    "logical": lambda: [
        Decimal(1) if (business and not quarter) else Decimal(0)
        for business, quarter in zip(_business_days(), _quarter_ends())
    ],
}


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("key", sorted(FORMULAS))
def test_symbol_is_a_column_operation(key: str) -> None:
    """One whole-horizon pass equals the elementwise result, and both equal the
    hand-written Decimal reading of §5.4."""
    window = _window()
    expr = COMPILED.items[_probe_id(key)].expr
    assert expr is not None, f"{key} failed to compile"

    evaluator = ColumnEvaluator(window, POLICY)
    column = np.asarray(evaluator.to_money(evaluator.eval(expr)).value)
    assert column.shape == (LENGTH,), f"{key} did not produce a full column"

    expected = [minor(value) for value in ORACLES[key]()]
    assert column.tolist() == expected, f"{key} column differs from the oracle"

    cell = compile_cell(expr, window, POLICY)
    elementwise = [cell(period)[0] for period in range(LENGTH)]
    assert elementwise == expected, f"{key} elementwise differs from the oracle"


def test_every_documented_symbol_is_covered() -> None:
    """Derived from the parser's own tables, so a new builtin without a
    vectorization test fails here."""
    from cashkit.engine import formula as formula_module

    call_handlers = {
        attribute.removeprefix("_call_")
        for attribute in dir(formula_module._Translator)
        if attribute.startswith("_call_") and attribute != "_call_numeric"
    }
    required = set(call_handlers) | set(NUMERIC_BUILTINS)
    required |= {f"t.{field}" for field in TIME_FIELDS}
    required |= {"param"}
    covered = set(FORMULAS)
    missing = {name for name in required if name not in covered}
    assert not missing, f"no vectorization test for: {sorted(missing)}"


def test_where_evaluates_both_branches() -> None:
    """D8: `where` is not a conditional. Both branches are computed for every
    period; only the selected branch's zero-division is reported (§5.4)."""
    window = _window()
    unselected = compile_book(
        _book_with('where(it("b", measure="accrual") == 0, 0, '
                   'it("a", measure="accrual") / it("b", measure="accrual"))')
    ).items["probe"].expr
    assert unselected is not None
    evaluator = ColumnEvaluator(window, POLICY)
    result = evaluator.to_money(evaluator.eval(unselected))
    # b is zero in many periods; those cells select the guarded branch, so no
    # zero-division is reported even though the division was computed.
    assert not np.asarray(result.zero_div).any()
    expected = [
        0 if b == 0 else minor(_divide(a, b)) for a, b in zip(A, B)
    ]
    assert np.asarray(result.value).tolist() == expected

    selected = compile_book(
        _book_with('where(it("b", measure="accrual") == 0, '
                   'it("a", measure="accrual") / it("b", measure="accrual"), 0)')
    ).items["probe"].expr
    assert selected is not None
    evaluator = ColumnEvaluator(window, POLICY)
    flagged = evaluator.to_money(evaluator.eval(selected))
    zeros = np.array([b == 0 for b in B])
    assert np.array_equal(np.asarray(flagged.zero_div), zeros)


def _book_with(formula: str) -> Book:
    items = dict(BOOK.items)
    items["probe"] = Item(
        id="probe", name="probe", kind="derived", tags={"cat": "derived"},
        formula=formula,
    )
    return BOOK.model_copy(update={"items": items})


def test_masks_promote_to_one_and_zero_minor_units() -> None:
    """A mask meeting money becomes 1.0000 or 0.0000, not True/False."""
    window = _window()
    expr = compile_book(_book_with("t.is_business_day * 1")).items["probe"].expr
    assert expr is not None
    evaluator = ColumnEvaluator(window, POLICY)
    column = np.asarray(evaluator.to_money(evaluator.eval(expr)).value)
    assert set(column.tolist()) <= {0, MINOR_SCALE}
    assert column.tolist() == [
        MINOR_SCALE if flag else 0 for flag in _business_days()
    ]


def test_rate_arithmetic_stays_exact_until_it_meets_money() -> None:
    """D-P2-07: a rate is an exact rational; only the boundary rounds."""
    window = _window()
    # 1/3 as a rate, multiplied into money: rounding happens once, at the end.
    expr = compile_book(
        _book_with('it("a", measure="accrual") * (1 / 3)')
    ).items["probe"].expr
    assert expr is not None
    evaluator = ColumnEvaluator(window, POLICY)
    column = np.asarray(evaluator.to_money(evaluator.eval(expr)).value)
    expected = [int(q(a / Decimal(3)).scaleb(4)) for a in A]
    assert column.tolist() == expected
