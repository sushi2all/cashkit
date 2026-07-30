"""Two-tier evaluation: condensation, column expressions, sequential fold.

```
graph -> SCC condensation -> per component:
    trivial      -> one int64 column expression over the whole horizon
    non-trivial  -> sequential fold, one period at a time
```

Trivial components are the overwhelming majority and cost one pass of array
operations each. Non-trivial components are the genuine feedback sets — cash
balance, overdraft interest, VAT credit carry — and only they pay for a Python
loop. Everything they depend on is already a finished column, so the loop reads
one number per period however many items sit outside the cycle (PRD §5.1).

:class:`Engine` also owns the delta path: it keeps the compiled graph and the
computed columns, so changing one item recomputes only that item's dependency
cone and reuses the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from cashkit.model import Book, Diagnostic, Item, ItemId

from .calendars import BusinessCalendar, PeriodIndex
from .columns import ColumnEvaluator, EvalWindow, TimeColumns
from .expand import (
    DateOps,
    IMMEDIATE,
    NEVER,
    INVALID,
    classify_settlement,
    derived_accrual_ordinals,
    expand_item,
    settle_occurrences,
)
from .formula import Agg, iter_refs
from .graph import CompiledBook, Component, compile_book
from .numeric import RoundingPolicy, check_column
from .result import RunResult

__all__ = ["Engine", "run"]


@dataclass
class Engine:
    """A book compiled once and evaluable many times.

    Construct it to compile; call :meth:`run` for a full evaluation and
    :meth:`delta` to replace items and recompute only what depends on them.
    """

    book: Book
    policy: RoundingPolicy = RoundingPolicy.HALF_UP
    compiled: CompiledBook = field(init=False)
    periods: PeriodIndex = field(init=False)
    calendar: BusinessCalendar = field(init=False)
    dates: DateOps = field(init=False)
    time: TimeColumns = field(init=False)
    accrual: dict[ItemId, np.ndarray] = field(init=False, default_factory=dict)
    cash: dict[ItemId, np.ndarray] = field(init=False, default_factory=dict)

    def __post_init__(self) -> None:
        self.periods = PeriodIndex.build(
            self.book.horizon,
            self.book.base_grain,
            self.book.calendar.fiscal_year_start_month,
        )
        self.calendar = BusinessCalendar.from_spec(self.book.calendar)
        self.dates = DateOps(periods=self.periods, calendar=self.calendar)
        self.time = TimeColumns.build(self.periods, self.calendar)
        self.compiled = compile_book(self.book)

    # -- evaluation ------------------------------------------------------- #

    def run(self) -> RunResult:
        """Evaluate the whole book.

        Returns a :class:`~cashkit.engine.result.RunResult` of int64 minor-unit
        columns. Diagnostics carry every problem found at compile time
        (``CK-E001``/``E002``/``E003``/``E008``/``E020``) and at expansion time
        (``CK-E004``/``E005``/``W001``/``W002``/``W005``). Never raises on book
        content; a numeric overflow raises ``MoneyOverflowError`` rather than
        wrapping.
        """
        return self._evaluate(set(self.book.items))

    def delta(self, changed: dict[ItemId, Item]) -> RunResult:
        """Recompute after replacing ``changed`` items, reusing cached columns.

        Recompiles the graph — a changed formula or a changed tag can move
        dependency edges, and `agg()` selectors resolve against tags — then
        recomputes only the dependency cone of the items whose definition or
        resolved dependencies moved. Returns a :class:`RunResult`; same
        diagnostics as :meth:`run`.
        """
        previous = self.compiled
        items = dict(self.book.items)
        items.update(changed)
        self.book = self.book.model_copy(update={"items": items})
        self.compiled = compile_book(self.book)

        moved: set[ItemId] = set(changed)
        for item_id, compiled_item in self.compiled.items.items():
            before = previous.items.get(item_id)
            if (
                before is None
                or before.item != compiled_item.item
                or before.expr != compiled_item.expr
                or before.same_period_deps != compiled_item.same_period_deps
                or before.lagged_deps != compiled_item.lagged_deps
            ):
                moved.add(item_id)
        return self._evaluate(self.compiled.downstream(moved))

    # -- internals -------------------------------------------------------- #

    def _evaluate(self, stale: set[ItemId]) -> RunResult:
        length = len(self.periods)
        diagnostics: list[Diagnostic] = list(self.compiled.diagnostics)
        emitted: set[tuple[str, ItemId | None]] = set()

        def emit(diagnostic: Diagnostic) -> None:
            # One diagnostic per (code, item): a monthly clamp over five years is
            # one modelling fact, not sixty (DECISIONS D-P2-11).
            key = (diagnostic.code, diagnostic.item_id)
            if key not in emitted:
                emitted.add(key)
                diagnostics.append(diagnostic)

        for item_id in self.book.items:
            if item_id in stale or item_id not in self.accrual:
                self.accrual[item_id] = np.zeros(length, dtype=np.int64)
                self.cash[item_id] = np.zeros(length, dtype=np.int64)
        for item_id in list(self.accrual):
            if item_id not in self.book.items:
                del self.accrual[item_id]
                del self.cash[item_id]

        kinds = {item_id: item.kind for item_id, item in self.book.items.items()}
        settlement_kind: dict[ItemId, str] = {}
        for item_id, compiled_item in sorted(self.compiled.items.items()):
            if compiled_item.broken:
                settlement_kind[item_id] = INVALID
                continue
            kind, structural = classify_settlement(compiled_item.item)
            settlement_kind[item_id] = kind
            for diagnostic in structural:
                emit(diagnostic)

        # 1. Generative expansion, before any derived evaluation: if agg() cannot
        #    see the generated facts, every derived item is wrong.
        for item_id, compiled_item in sorted(self.compiled.items.items()):
            if compiled_item.broken or compiled_item.is_derived or item_id not in stale:
                continue
            expansion = expand_item(
                compiled_item.item,
                settlement_kind[item_id],
                self.periods,
                self.dates,
                self.book.cutover,
                self.book.params,
                self.policy,
            )
            self.accrual[item_id] = check_column(
                expansion.accrual, f"item {item_id!r} accrual"
            )
            self.cash[item_id] = check_column(expansion.cash, f"item {item_id!r} cash")
            for diagnostic in expansion.diagnostics:
                emit(diagnostic)

        # 2. Derived evaluation, component by component in dependency order.
        derived_ords = derived_accrual_ordinals(self.periods)
        all_indices = np.arange(length, dtype=np.int64)
        for component in self.compiled.components:
            if not any(member in stale for member in component.members):
                continue
            if component.trivial:
                self._evaluate_trivial(
                    component.members[0],
                    kinds,
                    settlement_kind,
                    derived_ords,
                    all_indices,
                    emit,
                )
            else:
                self._evaluate_fold(
                    component, kinds, settlement_kind, derived_ords, emit
                )

        return RunResult(
            book_id=self.book.id,
            periods=self.periods,
            accrual=dict(self.accrual),
            cash=dict(self.cash),
            diagnostics=tuple(diagnostics),
            currencies={
                item_id: item.currency for item_id, item in self.book.items.items()
            },
        )

    def _column_of(
        self, item_id: ItemId, measure: str, kinds: dict[ItemId, str]
    ) -> np.ndarray:
        if kinds[item_id] == "stock":
            return self.accrual[item_id]
        return self.accrual[item_id] if measure == "accrual" else self.cash[item_id]

    def _window(self, kinds: dict[ItemId, str], start: int, stop: int) -> EvalWindow:
        return EvalWindow(
            accrual=self.accrual,
            cash=self.cash,
            kinds=kinds,
            params=self.book.params,
            opening_balance=self.book.opening_balance,
            time=self.time,
            start=start,
            stop=stop,
        )

    def _evaluate_trivial(
        self,
        item_id: ItemId,
        kinds: dict[ItemId, str],
        settlement_kind: dict[ItemId, str],
        derived_ords: np.ndarray,
        all_indices: np.ndarray,
        emit,
    ) -> None:
        compiled_item = self.compiled.items[item_id]
        if compiled_item.broken or not compiled_item.is_derived or compiled_item.expr is None:
            return
        length = len(self.periods)
        evaluator = ColumnEvaluator(self._window(kinds, 0, length), self.policy)
        result = evaluator.to_money(evaluator.eval(compiled_item.expr))
        self.accrual[item_id] = check_column(result.value, f"item {item_id!r} accrual")
        if result.zero_div.any():
            emit(
                _zero_division(
                    item_id, self.periods.starts[int(np.argmax(result.zero_div))]
                )
            )
        if compiled_item.item.kind == "stock":
            return
        for diagnostic in settle_occurrences(
            compiled_item.item,
            settlement_kind[item_id],
            derived_ords,
            result.value,
            all_indices,
            self.cash[item_id],
            self.dates,
            self.policy,
        ):
            emit(diagnostic)

    def _evaluate_fold(
        self,
        component: Component,
        kinds: dict[ItemId, str],
        settlement_kind: dict[ItemId, str],
        derived_ords: np.ndarray,
        emit,
    ) -> None:
        """The sequential tier: one period at a time, members in same-period order.

        The window is a single period, so the operator semantics are literally the
        same code the whole-horizon path uses — the fold cannot drift from the
        vectorized tier because it *is* the vectorized tier, narrowed.
        """
        members = [
            self.compiled.items[member]
            for member in component.members
            if not self.compiled.items[member].broken
            and self.compiled.items[member].is_derived
            and self.compiled.items[member].expr is not None
        ]
        inside = set(component.members)
        presum: dict[Agg, tuple[np.ndarray, tuple[ItemId, ...]]] = {}
        for compiled_item in members:
            assert compiled_item.expr is not None
            for ref in iter_refs(compiled_item.expr):
                if not isinstance(ref, Agg) or ref in presum:
                    continue
                outside = [m for m in (ref.items or ()) if m not in inside]
                still_inside = tuple(m for m in (ref.items or ()) if m in inside)
                total = np.zeros(len(self.periods), dtype=np.int64)
                for member in outside:
                    total = total + self._column_of(member, ref.measure, kinds)
                presum[ref] = (check_column(total, f"agg({ref.selector.source!r})"), still_inside)

        window = self._window(kinds, 0, 1)
        window.presum = presum
        evaluator = ColumnEvaluator(window, self.policy, scalar=True)
        for period in range(len(self.periods)):
            window.start = period
            window.stop = period + 1
            for compiled_item in members:
                assert compiled_item.expr is not None
                result = evaluator.to_money(evaluator.eval(compiled_item.expr))
                self.accrual[compiled_item.id][period] = result.value
                if result.zero_div:
                    emit(_zero_division(compiled_item.id, self.periods.starts[period]))
                if compiled_item.item.kind == "stock":
                    continue
                for diagnostic in settle_occurrences(
                    compiled_item.item,
                    settlement_kind[compiled_item.id],
                    derived_ords[period : period + 1],
                    np.array([result.value], dtype=np.int64),
                    np.array([period], dtype=np.int64),
                    self.cash[compiled_item.id],
                    self.dates,
                    self.policy,
                ):
                    emit(diagnostic)


def _zero_division(item_id: ItemId, period_start) -> Diagnostic:
    from cashkit.model.diagnostics import make_diagnostic

    return make_diagnostic(
        "CK-W005", item_id=item_id, field="formula", period=period_start.isoformat()
    )


def run(book: Book, *, policy: RoundingPolicy = RoundingPolicy.HALF_UP) -> RunResult:
    """Evaluate ``book`` with the vectorized engine.

    Returns a :class:`~cashkit.engine.result.RunResult` whose columns are int64
    minor units at 4 dp, byte-identical to ``cashkit.reference.run`` on the same
    book. See :meth:`Engine.run` for the diagnostics produced.
    """
    return Engine(book, policy).run()
