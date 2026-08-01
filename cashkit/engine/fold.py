"""The sequential tier: a formula compiled once, evaluated once per period.

A non-trivial component — a genuine ``prev()`` feedback set, typically 2-8 items
(PRD §5.1) — cannot be evaluated as one column expression, because period ``t``
reads period ``t-1``. It is the only sequential thing left in the engine, and it
is therefore the only place where per-node interpreter overhead is multiplied by
the horizon length. Walking the AST once per period costs more in dispatch than
in arithmetic.

So the tree is **staged**: :func:`compile_cell` walks the AST once and returns a
closure ``fn(t) -> (minor_units, zero_div)``. Everything that does not depend on
``t`` — rate arithmetic, the rate-to-money conversion, ``prev()`` init values,
column and aggregate resolution, rounding ratios — is resolved during that walk,
and the fold pays only for the arithmetic that genuinely varies per period.

Staging is sound because the value *kind* of every node is statically
determined by the node type, never by a runtime value:

* ``Literal`` and ``Param`` are rates, and every operation that keeps both
  operands rates yields a rate — so **a rate is always a compile-time constant**;
* ``it``/``prev``/``agg``/``cum``, ``t.index``/``t.month``, ``where`` and every
  builtin yield money;
* comparisons, logical operators and the remaining ``t.<field>`` yield masks.

The semantics implemented here are the semantics of
:class:`~cashkit.engine.columns.ColumnEvaluator` under its scalar kernel, node
for node — the promotion rules, the rounding boundaries and the ``zero_div``
propagation of ``where`` included. ``tests/test_fold.py`` asserts that agreement
directly on generated formulas, and the dual-engine gate re-proves it against
the ``Decimal`` oracle on every run.
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable

import numpy as np

from .columns import EvalWindow, TimeColumns, exact_fraction
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
    mul_ratio,
    round_div,
)

__all__ = ["CellFn", "compile_cell"]

#: A compiled cell: period index in, ``(minor units, zero-division flag)`` out.
CellFn = Callable[[int], tuple[int, bool]]


@dataclass(frozen=True, slots=True)
class _Const:
    """A rate resolved at compile time — the only shape a rate can take."""

    value: Fraction
    flag: bool = False


@dataclass(frozen=True, slots=True)
class _Dyn:
    """A per-period closure and the kind of value it yields."""

    kind: str  # "money" | "mask"
    fn: Callable[[int], tuple[Any, bool]]


_Compiled = _Const | _Dyn

_COMPARISONS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
}


class _Compiler:
    """Walk an :class:`Expr` once, emitting closures over the period index."""

    def __init__(self, window: EvalWindow, policy: RoundingPolicy) -> None:
        self.window = window
        self.policy = policy

    # -- promotion, staged ------------------------------------------------- #

    def money(self, node: _Compiled) -> Callable[[int], tuple[int, bool]]:
        """Coerce a compiled node to money, rounding a rate once at compile time."""
        if isinstance(node, _Const):
            minor = round_div(
                node.value.numerator * MINOR_SCALE, node.value.denominator, self.policy
            )
            constant = (minor, node.flag)
            return lambda _t: constant
        if node.kind == "money":
            return node.fn
        inner = node.fn

        def as_money(t: int) -> tuple[int, bool]:
            value, flag = inner(t)
            return (MINOR_SCALE if value else 0, flag)

        return as_money

    def truth(self, node: _Compiled) -> Callable[[int], tuple[bool, bool]]:
        """Coerce a compiled node to a mask."""
        if isinstance(node, _Const):
            constant = (node.value != 0, node.flag)
            return lambda _t: constant
        if node.kind == "mask":
            return node.fn
        inner = node.fn

        def as_mask(t: int) -> tuple[bool, bool]:
            value, flag = inner(t)
            return (value != 0, flag)

        return as_mask

    # -- dispatch ---------------------------------------------------------- #

    def compile(self, expr: Expr) -> _Compiled:
        return _DISPATCH[expr.__class__](self, expr)

    # -- leaves ------------------------------------------------------------ #

    def _literal(self, expr: Literal) -> _Compiled:
        return _Const(exact_fraction(expr.value))

    def _param(self, expr: Param) -> _Compiled:
        return _Const(exact_fraction(self.window.param(expr.key)))

    def _time_field(self, expr: TimeField) -> _Compiled:
        column = getattr(self.window.time, expr.name)
        if expr.name in TimeColumns.NUMERIC:
            return _Dyn("money", lambda t: (int(column[t]), False))
        return _Dyn("mask", lambda t: (bool(column[t]), False))

    def _item_ref(self, expr: ItemRef) -> _Compiled:
        column = self.window.full_column(expr.item_id, expr.measure)
        return _Dyn("money", lambda t: (int(column[t]), False))

    def _prev(self, expr: Prev) -> _Compiled:
        column = self.window.full_column(expr.item_id, expr.measure)
        init = (
            exact_fraction(self.window.param(expr.init.key))
            if isinstance(expr.init, Param)
            else exact_fraction(expr.init.value)
        )
        minor = round_div(init.numerator * MINOR_SCALE, init.denominator, self.policy)
        lag = expr.lag

        def lagged(t: int) -> tuple[int, bool]:
            index = t - lag
            return ((int(column[index]) if index >= 0 else minor), False)

        return _Dyn("money", lagged)

    def _agg(self, expr: Agg) -> _Compiled:
        window = self.window
        presummed, members = window.agg_members(expr)
        columns = [window.full_column(member, expr.measure) for member in members]
        if len(members) > ADDITION_HEADROOM:
            # Only an aggregate wider than the addition headroom needs a running
            # overflow guard; below it the column ceiling already covers the sum.
            def wide(t: int) -> tuple[int, bool]:
                window.guard_aggregate(expr, members)
                total = 0 if presummed is None else int(presummed[t])
                for column in columns:
                    total += int(column[t])
                return (total, False)

            return _Dyn("money", wide)

        if presummed is None:

            def live_only(t: int) -> tuple[int, bool]:
                total = 0
                for column in columns:
                    total += int(column[t])
                return (total, False)

            return _Dyn("money", live_only)

        def with_presum(t: int) -> tuple[int, bool]:
            total = int(presummed[t])
            for column in columns:
                total += int(column[t])
            return (total, False)

        return _Dyn("money", with_presum)

    def _cum(self, expr: Cum) -> _Compiled:
        column = self.window.full_column(expr.item_id, expr.measure)
        label = f"cum({expr.item_id!r})"

        def cumulative(t: int) -> tuple[int, bool]:
            guard_total(
                int(np.abs(column).max()) if column.size else 0, column.size, label
            )
            return (int(column[: t + 1].sum()), False)

        return _Dyn("money", cumulative)

    # -- operators --------------------------------------------------------- #

    def _unary(self, expr: Unary) -> _Compiled:
        operand = self.compile(expr.operand)
        if expr.op == "not":
            inner = self.truth(operand)

            def negated(t: int) -> tuple[bool, bool]:
                value, flag = inner(t)
                return (not value, flag)

            return _Dyn("mask", negated)
        if expr.op == "+":
            return operand
        if isinstance(operand, _Const):
            return _Const(-operand.value, operand.flag)
        inner_money = self.money(operand)

        def minus(t: int) -> tuple[int, bool]:
            value, flag = inner_money(t)
            return (-value, flag)

        return _Dyn("money", minus)

    def _binary(self, expr: Binary) -> _Compiled:
        left = self.compile(expr.left)
        right = self.compile(expr.right)
        op = expr.op

        if isinstance(left, _Const) and isinstance(right, _Const):
            flag = left.flag or right.flag
            if op == "+":
                return _Const(left.value + right.value, flag)
            if op == "-":
                return _Const(left.value - right.value, flag)
            if op == "*":
                return _Const(left.value * right.value, flag)
            if right.value == 0:
                # Masked-safe division: both `where` branches always evaluate, so
                # a/0 executes by design and yields 0 (PRD §5.4, CK-W005).
                return _Const(Fraction(0), True)
            return _Const(left.value / right.value, flag)

        if op in ("+", "-"):
            lhs = self.money(left)
            rhs = self.money(right)
            if op == "+":

                def added(t: int) -> tuple[int, bool]:
                    a, af = lhs(t)
                    b, bf = rhs(t)
                    return (a + b, af or bf)

                return _Dyn("money", added)

            def subtracted(t: int) -> tuple[int, bool]:
                a, af = lhs(t)
                b, bf = rhs(t)
                return (a - b, af or bf)

            return _Dyn("money", subtracted)

        if op == "*":
            if isinstance(left, _Const) or isinstance(right, _Const):
                rate = left if isinstance(left, _Const) else right
                assert isinstance(rate, _Const)
                other = self.money(right if isinstance(left, _Const) else left)
                numerator = rate.value.numerator
                denominator = rate.value.denominator
                rate_flag = rate.flag
                policy = self.policy

                def scaled(t: int) -> tuple[int, bool]:
                    value, flag = other(t)
                    return (
                        mul_ratio(value, numerator, denominator, policy),
                        flag or rate_flag,
                    )

                return _Dyn("money", scaled)

            lhs = self.money(left)
            rhs = self.money(right)
            policy = self.policy

            def multiplied(t: int) -> tuple[int, bool]:
                a, af = lhs(t)
                b, bf = rhs(t)
                return (round_div(a * b, MINOR_SCALE, policy), af or bf)

            return _Dyn("money", multiplied)

        assert op == "/"
        if isinstance(right, _Const):
            if right.value == 0:
                return _Dyn("money", lambda _t: (0, True))
            numerator = right.value.denominator
            denominator = right.value.numerator
            rate_flag = right.flag
            lhs = self.money(left)
            policy = self.policy

            def by_rate(t: int) -> tuple[int, bool]:
                value, flag = lhs(t)
                return (
                    mul_ratio(value, numerator, denominator, policy),
                    flag or rate_flag,
                )

            return _Dyn("money", by_rate)

        lhs = self.money(left)
        rhs = self.money(right)
        policy = self.policy

        def divided(t: int) -> tuple[int, bool]:
            a, af = lhs(t)
            b, bf = rhs(t)
            if b == 0:
                return (0, True)
            return (round_div(a * MINOR_SCALE, b, policy), af or bf)

        return _Dyn("money", divided)

    def _compare(self, expr: Compare) -> _Compiled:
        left = self.compile(expr.left)
        right = self.compile(expr.right)
        operation = _COMPARISONS[expr.op]
        if isinstance(left, _Const) and isinstance(right, _Const):
            constant = (
                bool(operation(left.value, right.value)),
                left.flag or right.flag,
            )
            return _Dyn("mask", lambda _t: constant)
        lhs = self.money(left)
        rhs = self.money(right)

        def compared(t: int) -> tuple[bool, bool]:
            a, af = lhs(t)
            b, bf = rhs(t)
            return (bool(operation(a, b)), af or bf)

        return _Dyn("mask", compared)

    def _logical(self, expr: Logical) -> _Compiled:
        # Never short-circuit: every operand is evaluated before combining (D8).
        operands = [self.truth(self.compile(operand)) for operand in expr.operands]
        conjunction = expr.op == "and"

        def combined(t: int) -> tuple[bool, bool]:
            flag = False
            result: bool | None = None
            for operand in operands:
                value, operand_flag = operand(t)
                flag = flag or operand_flag
                result = value if result is None else (
                    (result and value) if conjunction else (result or value)
                )
            return (bool(result), flag)

        return _Dyn("mask", combined)

    def _where(self, expr: Where) -> _Compiled:
        condition = self.truth(self.compile(expr.cond))
        when_true = self.money(self.compile(expr.then))
        when_false = self.money(self.compile(expr.otherwise))

        def selected(t: int) -> tuple[int, bool]:
            # Both branches always evaluate (D8); only the selected branch's
            # zero-division flag survives, alongside the condition's.
            mask, condition_flag = condition(t)
            a, af = when_true(t)
            b, bf = when_false(t)
            return ((a, condition_flag or af) if mask else (b, condition_flag or bf))

        return _Dyn("money", selected)

    def _builtin(self, expr: Builtin) -> _Compiled:
        args = [self.money(self.compile(arg)) for arg in expr.args]
        name = expr.name

        if name == "abs_":
            inner = args[0]

            def absolute(t: int) -> tuple[int, bool]:
                value, flag = inner(t)
                return (abs(value), flag)

            return _Dyn("money", absolute)

        if name == "round_":
            inner = args[0]
            digits = args[1](0)[0] // MINOR_SCALE
            quantum = 10 ** (4 - digits)
            policy = self.policy

            def rounded(t: int) -> tuple[int, bool]:
                value, flag = inner(t)
                return (round_div(value, quantum, policy) * quantum, flag)

            return _Dyn("money", rounded)

        if name == "clip":
            inner, low_fn, high_fn = args

            def clipped(t: int) -> tuple[int, bool]:
                value, flag = inner(t)
                low, low_flag = low_fn(t)
                high, high_flag = high_fn(t)
                return (min(max(value, low), high), flag or low_flag or high_flag)

            return _Dyn("money", clipped)

        minimum = name == "min"

        def extreme(t: int) -> tuple[int, bool]:
            result: int | None = None
            flag = False
            for arg in args:
                value, arg_flag = arg(t)
                flag = flag or arg_flag
                if result is None:
                    result = value
                else:
                    result = min(result, value) if minimum else max(result, value)
            assert result is not None
            return (result, flag)

        return _Dyn("money", extreme)


_DISPATCH = {
    Literal: _Compiler._literal,
    Param: _Compiler._param,
    TimeField: _Compiler._time_field,
    ItemRef: _Compiler._item_ref,
    Prev: _Compiler._prev,
    Agg: _Compiler._agg,
    Cum: _Compiler._cum,
    Unary: _Compiler._unary,
    Binary: _Compiler._binary,
    Compare: _Compiler._compare,
    Logical: _Compiler._logical,
    Where: _Compiler._where,
    Builtin: _Compiler._builtin,
}


def compile_cell(expr: Expr, window: EvalWindow, policy: RoundingPolicy) -> CellFn:
    """Compile ``expr`` into a per-period closure over ``window``.

    The window's columns are resolved to array objects during compilation, which
    is why the fold must mutate its columns in place rather than rebind them.
    Returns ``fn(period) -> (minor units, zero_div)``; produces no diagnostics —
    a surviving ``zero_div`` is the caller's ``CK-W005``.
    """
    compiler = _Compiler(window, policy)
    return compiler.money(compiler.compile(expr))
