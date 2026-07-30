"""Formula evaluation as masked column operations (PRD §5.1, §5.4).

Every node of the restricted AST is a masked column operation. That single
abstraction serves both tiers of evaluation:

* a **trivial** component evaluates once over the whole horizon — one column
  expression per item, which is the point of the whole design;
* a **non-trivial** component (a genuine ``prev()`` feedback set) evaluates one
  period at a time in the sequential fold.

The two tiers share one :class:`ColumnEvaluator`. Node dispatch, the rate/money
promotion rules and division-flag propagation — the places where a second
implementation would quietly drift — exist exactly once. Only the arithmetic
*kernel* differs: :class:`ArrayKernel` operates on int64 numpy columns,
:class:`ScalarKernel` on Python ints. The fold uses the scalar kernel because
numpy on one-element arrays costs more in call overhead than the arithmetic
saves, and the fold is the only sequential thing left in the engine.

Values are one of two kinds, mirroring the reference engine exactly
(DECISIONS D-P2-07): a **rate**, a dimensionless scalar held as an exact
``Fraction``, or **money** at 4 dp in int64 minor units. Rates stay exact until
they meet money. Masks come from comparisons and logical operators.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

import numpy as np

from cashkit.model import ItemId

from .calendars import BusinessCalendar, PeriodIndex
from .formula import (
    Agg,
    Binary,
    Builtin,
    Compare,
    Cum,
    Expr,
    ItemRef,
    Literal,
    Logical,
    Param,
    Prev,
    TimeField,
    Unary,
    Where,
)
from .numeric import (
    ADDITION_HEADROOM,
    MINOR_SCALE,
    RoundingPolicy,
    guard_total,
    mul_elementwise,
    mul_ratio,
    mul_ratio_array,
    ratio_of,
    round_div,
    round_div_array,
    round_div_elementwise,
    scale_numerator,
)

__all__ = [
    "ArrayKernel",
    "Column",
    "ColumnEvaluator",
    "EvalWindow",
    "Mask",
    "Rate",
    "ScalarKernel",
    "TimeColumns",
    "Value",
]


# --------------------------------------------------------------------------- #
# Value kinds
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Rate:
    """A dimensionless exact scalar — a literal or a param."""

    value: Fraction
    zero_div: bool = False


@dataclass(frozen=True)
class Column:
    """Money at 4 dp: an int64 column, or one int under the scalar kernel."""

    value: Any
    zero_div: Any


@dataclass(frozen=True)
class Mask:
    """A boolean from a comparison or logical operator."""

    value: Any
    zero_div: Any


Value = Rate | Column | Mask


# --------------------------------------------------------------------------- #
# Period metadata, computed once per run
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TimeColumns:
    """``t.<field>`` over the whole horizon.

    ``index`` and ``month`` are integer-valued *money* columns so every numeric
    column shares one representation; the predicates are masks.
    ``is_business_day`` is evaluated on the period's start date and
    ``is_quarter_end`` on its inclusive end date (DECISIONS D-P2-07).
    """

    index: np.ndarray
    month: np.ndarray
    is_quarter_end: np.ndarray
    is_business_day: np.ndarray

    #: Fields that are numeric columns rather than masks.
    NUMERIC = ("index", "month")

    @classmethod
    def build(cls, periods: PeriodIndex, calendar: BusinessCalendar) -> "TimeColumns":
        """Precompute every period-metadata column. No diagnostics."""
        length = len(periods)
        return cls(
            index=np.arange(length, dtype=np.int64) * MINOR_SCALE,
            month=np.fromiter(
                (day.month for day in periods.starts), dtype=np.int64, count=length
            )
            * MINOR_SCALE,
            is_quarter_end=np.fromiter(
                (periods.is_quarter_end(end - timedelta(days=1)) for end in periods.ends),
                dtype=bool,
                count=length,
            ),
            is_business_day=np.fromiter(
                (calendar.is_business_day(day) for day in periods.starts),
                dtype=bool,
                count=length,
            ),
        )


# --------------------------------------------------------------------------- #
# Evaluation window
# --------------------------------------------------------------------------- #


@dataclass
class EvalWindow:
    """The slice of the horizon a formula is being evaluated over.

    ``start``/``stop`` are period indices. Symbol lookups read the *full*
    columns and return the windowed part, which is what lets ``prev()`` reach
    back before the window and ``cum()`` accumulate from the horizon start.
    """

    accrual: dict[ItemId, np.ndarray]
    cash: dict[ItemId, np.ndarray]
    kinds: dict[ItemId, str]
    params: dict[str, Decimal]
    opening_balance: Decimal
    time: TimeColumns
    start: int
    stop: int
    #: Pre-summed ``agg()`` vectors for a feedback fold: node -> (sum of the
    #: members outside the component, members still inside it). None otherwise.
    presum: dict[Agg, tuple[np.ndarray, tuple[ItemId, ...]]] | None = None

    @property
    def size(self) -> int:
        """Number of periods in the window. No diagnostics."""
        return self.stop - self.start

    def full_column(self, item_id: ItemId, measure: str) -> np.ndarray:
        """The whole-horizon column for an item.

        A stock has no cash column: both measures read its level, or the
        canonical ``prev("cash")`` would read zero (DECISIONS D-P2-06).
        """
        if self.kinds[item_id] == "stock":
            return self.accrual[item_id]
        return self.accrual[item_id] if measure == "accrual" else self.cash[item_id]

    def param(self, key: str) -> Decimal:
        """Resolve ``p.<key>``; ``opening_balance`` falls back to the Book field.

        No diagnostics — an unknown param is caught at compile time (CK-E008),
        so reaching here with one is programmer error.
        """
        if key in self.params:
            return self.params[key]
        return self.opening_balance

    def agg_members(self, expr: Agg) -> tuple[np.ndarray | None, tuple[ItemId, ...]]:
        """Split an aggregate into its pre-summed part and its live members.

        Returns ``(presummed_or_None, members_to_add)``. No diagnostics.
        """
        if self.presum is not None:
            entry = self.presum.get(expr)
            if entry is not None:
                return entry[0], entry[1]
        return None, (expr.items or ())

    def guard_aggregate(self, expr: Agg, members: tuple[ItemId, ...]) -> None:
        """Check an unusually wide aggregate against int64 (see ADDITION_HEADROOM)."""
        if len(members) <= ADDITION_HEADROOM:
            return
        peak = max(
            (int(np.abs(self.full_column(m, expr.measure)).max()) for m in members),
            default=0,
        )
        guard_total(peak, len(members), f"agg({expr.selector.source!r})")


# --------------------------------------------------------------------------- #
# Arithmetic kernels
# --------------------------------------------------------------------------- #


class ArrayKernel:
    """Whole-column arithmetic on int64 numpy arrays."""

    def __init__(self, window: EvalWindow) -> None:
        self.window = window
        self._zeros = np.zeros(window.size, dtype=bool)
        self._zeros.flags.writeable = False

    # -- flags ------------------------------------------------------------ #

    def no_flag(self) -> Any:
        return self._zeros

    def flag(self, value: bool) -> Any:
        return np.full(self.window.size, value, dtype=bool)

    def flag_of(self, value: Value) -> Any:
        if isinstance(value, Rate):
            return self.flag(value.zero_div)
        return value.zero_div

    def combine(self, *flags: Any) -> Any:
        result = flags[0]
        for extra in flags[1:]:
            result = result | extra
        return result

    # -- construction ----------------------------------------------------- #

    def broadcast(self, minor: int) -> Any:
        return np.full(self.window.size, minor, dtype=np.int64)

    def from_mask(self, mask: Any) -> Any:
        return np.where(mask, MINOR_SCALE, 0).astype(np.int64)

    # -- leaves ----------------------------------------------------------- #

    def item(self, item_id: ItemId, measure: str) -> Any:
        window = self.window
        return window.full_column(item_id, measure)[window.start : window.stop].astype(
            np.int64
        )

    def lagged(self, item_id: ItemId, measure: str, lag: int, init: int) -> Any:
        window = self.window
        column = window.full_column(item_id, measure)
        indices = np.arange(window.start, window.stop, dtype=np.int64) - lag
        inside = indices >= 0
        gathered = column[np.where(inside, indices, 0)]
        return np.where(inside, gathered, init).astype(np.int64)

    def cumulative(self, item_id: ItemId, measure: str) -> Any:
        window = self.window
        column = window.full_column(item_id, measure)
        guard_total(
            int(np.abs(column).max()) if column.size else 0,
            column.size,
            f"cum({item_id!r})",
        )
        return np.cumsum(column)[window.start : window.stop].astype(np.int64)

    def aggregate(self, expr: Agg) -> Any:
        window = self.window
        presummed, members = window.agg_members(expr)
        window.guard_aggregate(expr, members)
        if presummed is None:
            total = np.zeros(window.size, dtype=np.int64)
        else:
            total = presummed[window.start : window.stop].astype(np.int64)
        for member in members:
            total = total + self.item(member, expr.measure)
        return total

    def time_numeric(self, name: str) -> Any:
        window = self.window
        return getattr(window.time, name)[window.start : window.stop].astype(np.int64)

    def time_mask(self, name: str) -> Any:
        window = self.window
        return getattr(window.time, name)[window.start : window.stop]

    # -- operations ------------------------------------------------------- #

    def truthy(self, value: Any) -> Any:
        return value != 0

    def mask_from_bool(self, value: bool) -> Any:
        return self.flag(value)

    def logical_not(self, mask: Any) -> Any:
        return ~mask

    def logical(self, left: Any, right: Any, conjunction: bool) -> Any:
        return (left & right) if conjunction else (left | right)

    def select(self, mask: Any, when_true: Any, when_false: Any) -> Any:
        return np.where(mask, when_true, when_false)

    def negate(self, value: Any) -> Any:
        return -value

    def absolute(self, value: Any) -> Any:
        return np.abs(value)

    def extreme(self, left: Any, right: Any, minimum: bool) -> Any:
        return np.minimum(left, right) if minimum else np.maximum(left, right)

    def mul_rate(self, value: Any, numerator: int, denominator: int, policy) -> Any:
        return mul_ratio_array(value, numerator, denominator, policy)

    def mul_money(self, left: Any, right: Any, policy) -> Any:
        return mul_elementwise(left, right, policy)

    def div_money(self, left: Any, right: Any, policy) -> tuple[Any, Any]:
        scaled = scale_numerator(left, MINOR_SCALE)
        return round_div_elementwise(scaled, right, policy), right == 0

    def round_to(self, value: Any, quantum: int, policy) -> Any:
        return (round_div_array(value, quantum, policy) * quantum).astype(np.int64)

    def digits_of(self, value: Any) -> int:
        return int(value[0]) // MINOR_SCALE


class ScalarKernel(ArrayKernel):
    """Single-period arithmetic on Python ints, for the sequential fold.

    Every method here is the one-value form of its :class:`ArrayKernel`
    counterpart. The arithmetic primitives it calls (``round_div``,
    ``mul_ratio``) are the scalar siblings of the array ones and are pinned
    against them by property tests in ``tests/test_numeric.py``, so the two
    kernels cannot disagree numerically.
    """

    def __init__(self, window: EvalWindow) -> None:
        self.window = window

    @property
    def period(self) -> int:
        return self.window.start

    # -- flags ------------------------------------------------------------ #

    def no_flag(self) -> Any:
        return False

    def flag(self, value: bool) -> Any:
        return value

    def combine(self, *flags: Any) -> Any:
        return any(flags)

    # -- construction ----------------------------------------------------- #

    def broadcast(self, minor: int) -> Any:
        return minor

    def from_mask(self, mask: Any) -> Any:
        return MINOR_SCALE if mask else 0

    # -- leaves ----------------------------------------------------------- #

    def item(self, item_id: ItemId, measure: str) -> Any:
        return int(self.window.full_column(item_id, measure)[self.period])

    def lagged(self, item_id: ItemId, measure: str, lag: int, init: int) -> Any:
        index = self.period - lag
        if index < 0:
            return init
        return int(self.window.full_column(item_id, measure)[index])

    def cumulative(self, item_id: ItemId, measure: str) -> Any:
        column = self.window.full_column(item_id, measure)
        guard_total(
            int(np.abs(column).max()) if column.size else 0,
            column.size,
            f"cum({item_id!r})",
        )
        return int(column[: self.period + 1].sum())

    def aggregate(self, expr: Agg) -> Any:
        window = self.window
        presummed, members = window.agg_members(expr)
        window.guard_aggregate(expr, members)
        total = 0 if presummed is None else int(presummed[self.period])
        for member in members:
            total += self.item(member, expr.measure)
        return total

    def time_numeric(self, name: str) -> Any:
        return int(getattr(self.window.time, name)[self.period])

    def time_mask(self, name: str) -> Any:
        return bool(getattr(self.window.time, name)[self.period])

    # -- operations ------------------------------------------------------- #

    def truthy(self, value: Any) -> Any:
        return value != 0

    def mask_from_bool(self, value: bool) -> Any:
        return value

    def logical_not(self, mask: Any) -> Any:
        return not mask

    def logical(self, left: Any, right: Any, conjunction: bool) -> Any:
        return (left and right) if conjunction else (left or right)

    def select(self, mask: Any, when_true: Any, when_false: Any) -> Any:
        return when_true if mask else when_false

    def absolute(self, value: Any) -> Any:
        return abs(value)

    def extreme(self, left: Any, right: Any, minimum: bool) -> Any:
        return min(left, right) if minimum else max(left, right)

    def mul_rate(self, value: Any, numerator: int, denominator: int, policy) -> Any:
        return mul_ratio(value, numerator, denominator, policy)

    def mul_money(self, left: Any, right: Any, policy) -> Any:
        return round_div(left * right, MINOR_SCALE, policy)

    def div_money(self, left: Any, right: Any, policy) -> tuple[Any, Any]:
        if right == 0:
            return 0, True
        return round_div(left * MINOR_SCALE, right, policy), False

    def round_to(self, value: Any, quantum: int, policy) -> Any:
        return round_div(value, quantum, policy) * quantum

    def digits_of(self, value: Any) -> int:
        return int(value) // MINOR_SCALE


# --------------------------------------------------------------------------- #
# The evaluator
# --------------------------------------------------------------------------- #


class ColumnEvaluator:
    """Evaluate a bound formula over an :class:`EvalWindow`.

    Operator semantics, identical to the reference engine's node for node:
    ``+``/``-``/comparisons/``min``/``max``/``clip`` promote a rate to money at
    4 dp; ``*`` and ``/`` keep a rate operand exact and round once; rate-only
    arithmetic stays exact; ``where`` always yields money.
    """

    def __init__(
        self, window: EvalWindow, policy: RoundingPolicy, *, scalar: bool = False
    ) -> None:
        self.window = window
        self.policy = policy
        self.kernel: ArrayKernel = (
            ScalarKernel(window) if scalar else ArrayKernel(window)
        )

    # -- promotion -------------------------------------------------------- #

    def to_money(self, value: Value) -> Column:
        """Coerce any value to money, rounding a rate to 4 dp at this boundary.

        Returns a :class:`Column`; produces no diagnostics.
        """
        kernel = self.kernel
        if isinstance(value, Column):
            return value
        if isinstance(value, Mask):
            return Column(kernel.from_mask(value.value), value.zero_div)
        minor = round_div(
            value.value.numerator * MINOR_SCALE, value.value.denominator, self.policy
        )
        return Column(kernel.broadcast(minor), kernel.flag(value.zero_div))

    def _truth(self, value: Value) -> Any:
        kernel = self.kernel
        if isinstance(value, Mask):
            return value.value
        if isinstance(value, Rate):
            return kernel.mask_from_bool(value.value != 0)
        return kernel.truthy(value.value)

    # -- dispatch --------------------------------------------------------- #

    def eval(self, expr: Expr) -> Value:
        """Evaluate ``expr`` over the window.

        Returns a :class:`Rate`, :class:`Column` or :class:`Mask`. Division by
        zero yields zero and raises the ``zero_div`` flag rather than an
        exception; the caller turns a surviving flag into ``CK-W005``.
        """
        kernel = self.kernel
        if isinstance(expr, Literal):
            return Rate(Fraction(expr.value))
        if isinstance(expr, Param):
            return Rate(Fraction(self.window.param(expr.key)))
        if isinstance(expr, TimeField):
            if expr.name in TimeColumns.NUMERIC:
                return Column(kernel.time_numeric(expr.name), kernel.no_flag())
            return Mask(kernel.time_mask(expr.name), kernel.no_flag())
        if isinstance(expr, ItemRef):
            return Column(kernel.item(expr.item_id, expr.measure), kernel.no_flag())
        if isinstance(expr, Prev):
            init = (
                Fraction(self.window.param(expr.init.key))
                if isinstance(expr.init, Param)
                else Fraction(expr.init.value)
            )
            minor = round_div(
                init.numerator * MINOR_SCALE, init.denominator, self.policy
            )
            return Column(
                kernel.lagged(expr.item_id, expr.measure, expr.lag, minor),
                kernel.no_flag(),
            )
        if isinstance(expr, Agg):
            return Column(kernel.aggregate(expr), kernel.no_flag())
        if isinstance(expr, Cum):
            return Column(
                kernel.cumulative(expr.item_id, expr.measure), kernel.no_flag()
            )
        if isinstance(expr, Unary):
            return self._unary(expr)
        if isinstance(expr, Binary):
            return self._binary(expr)
        if isinstance(expr, Compare):
            return self._compare(expr)
        if isinstance(expr, Logical):
            return self._logical(expr)
        if isinstance(expr, Where):
            return self._where(expr)
        assert isinstance(expr, Builtin)
        return self._builtin(expr)

    # -- operators -------------------------------------------------------- #

    def _unary(self, expr: Unary) -> Value:
        kernel = self.kernel
        operand = self.eval(expr.operand)
        if expr.op == "not":
            return Mask(
                kernel.logical_not(self._truth(operand)), kernel.flag_of(operand)
            )
        if expr.op == "+":
            return operand
        if isinstance(operand, Rate):
            return Rate(-operand.value, operand.zero_div)
        column = self.to_money(operand)
        return Column(kernel.negate(column.value), column.zero_div)

    def _binary(self, expr: Binary) -> Value:
        kernel = self.kernel
        left = self.eval(expr.left)
        right = self.eval(expr.right)
        flag = kernel.combine(kernel.flag_of(left), kernel.flag_of(right))
        both_rate = isinstance(left, Rate) and isinstance(right, Rate)

        if expr.op in ("+", "-"):
            if both_rate:
                total = (
                    left.value + right.value
                    if expr.op == "+"
                    else left.value - right.value
                )
                return Rate(total, left.zero_div or right.zero_div)
            lhs = self.to_money(left).value
            rhs = self.to_money(right).value
            return Column(lhs + rhs if expr.op == "+" else lhs - rhs, flag)

        if expr.op == "*":
            if both_rate:
                return Rate(left.value * right.value, left.zero_div or right.zero_div)
            if isinstance(left, Rate):
                money, rate = self.to_money(right), left
            elif isinstance(right, Rate):
                money, rate = self.to_money(left), right
            else:
                return Column(
                    kernel.mul_money(
                        self.to_money(left).value, self.to_money(right).value, self.policy
                    ),
                    flag,
                )
            return Column(
                kernel.mul_rate(
                    money.value,
                    rate.value.numerator,
                    rate.value.denominator,
                    self.policy,
                ),
                flag,
            )

        assert expr.op == "/"
        if both_rate:
            if right.value == 0:
                return Rate(Fraction(0), True)
            return Rate(left.value / right.value, left.zero_div or right.zero_div)
        if isinstance(right, Rate):
            if right.value == 0:
                # Masked-safe division: both `where` branches always evaluate, so
                # a/0 executes by design and yields 0 (PRD §5.4, CK-W005).
                return Column(kernel.broadcast(0), kernel.flag(True))
            return Column(
                kernel.mul_rate(
                    self.to_money(left).value,
                    right.value.denominator,
                    right.value.numerator,
                    self.policy,
                ),
                flag,
            )
        quotient, zero = kernel.div_money(
            self.to_money(left).value, self.to_money(right).value, self.policy
        )
        return Column(quotient, kernel.combine(flag, zero))

    def _compare(self, expr: Compare) -> Value:
        kernel = self.kernel
        left = self.eval(expr.left)
        right = self.eval(expr.right)
        flag = kernel.combine(kernel.flag_of(left), kernel.flag_of(right))
        if isinstance(left, Rate) and isinstance(right, Rate):
            outcome = _COMPARISONS[expr.op](left.value, right.value)
            return Mask(kernel.mask_from_bool(bool(outcome)), flag)
        lhs = self.to_money(left).value
        rhs = self.to_money(right).value
        return Mask(_COMPARISONS[expr.op](lhs, rhs), flag)

    def _logical(self, expr: Logical) -> Value:
        kernel = self.kernel
        # Never short-circuit: every operand is evaluated before combining (D8).
        values = [self.eval(operand) for operand in expr.operands]
        flag = kernel.combine(*[kernel.flag_of(value) for value in values])
        truths = [self._truth(value) for value in values]
        result = truths[0]
        for truth in truths[1:]:
            result = kernel.logical(result, truth, expr.op == "and")
        return Mask(result, flag)

    def _where(self, expr: Where) -> Value:
        kernel = self.kernel
        condition = self.eval(expr.cond)
        chosen = self.eval(expr.then)
        otherwise = self.eval(expr.otherwise)
        # Both branches always evaluate (D8); the result is always money, since a
        # per-period exact rate has no column representation (D-P2-20).
        mask = self._truth(condition)
        when_true = self.to_money(chosen)
        when_false = self.to_money(otherwise)
        return Column(
            kernel.select(mask, when_true.value, when_false.value),
            kernel.combine(
                kernel.flag_of(condition),
                kernel.select(mask, when_true.zero_div, when_false.zero_div),
            ),
        )

    def _builtin(self, expr: Builtin) -> Value:
        kernel = self.kernel
        args = [self.eval(arg) for arg in expr.args]
        flag = kernel.combine(*[kernel.flag_of(arg) for arg in args])
        if expr.name == "abs_":
            return Column(kernel.absolute(self.to_money(args[0]).value), flag)
        if expr.name == "round_":
            digits = kernel.digits_of(self.to_money(args[1]).value)
            quantum = 10 ** (4 - digits)
            return Column(
                kernel.round_to(self.to_money(args[0]).value, quantum, self.policy),
                flag,
            )
        if expr.name == "clip":
            value = self.to_money(args[0]).value
            low = self.to_money(args[1]).value
            high = self.to_money(args[2]).value
            return Column(
                kernel.extreme(kernel.extreme(value, low, False), high, True), flag
            )
        values = [self.to_money(arg).value for arg in args]
        minimum = expr.name == "min"
        result = values[0]
        for value in values[1:]:
            result = kernel.extreme(result, value, minimum)
        return Column(result, flag)


_COMPARISONS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}
