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

from cashkit.model import Book, Diagnostic, Event, Item, ItemId

from .calendars import BusinessCalendar, PeriodIndex
from .columns import ColumnEvaluator, EvalWindow, TimeColumns
from .expand import (
    DateOps,
    IMMEDIATE,
    NEVER,
    INVALID,
    FoldSettlement,
    add_minor,
    classify_settlement,
    derived_accrual_ordinals,
    expand_item,
    scatter_add,
    settle_occurrences,
)
from .facts import EventFact, FactSet, augmented_items, resolve_facts
from .fold import compile_cell
from .formula import Agg, iter_refs
from .graph import CompiledBook, Component, compile_book
from .numeric import RoundingPolicy, check_column, to_minor
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
    #: The event side of the fact union (PRD §3.2). Supplied by the ledger
    #: store; the engine never opens one itself, so storage stays swappable.
    events: tuple[Event, ...] = ()
    factset: FactSet = field(init=False)
    compiled: CompiledBook = field(init=False)
    periods: PeriodIndex = field(init=False)
    calendar: BusinessCalendar = field(init=False)
    dates: DateOps = field(init=False)
    time: TimeColumns = field(init=False)
    accrual: dict[ItemId, np.ndarray] = field(init=False, default_factory=dict)
    cash: dict[ItemId, np.ndarray] = field(init=False, default_factory=dict)
    #: Runtime diagnostics per item, kept across evaluations. A delta run
    #: recomputes only the stale cone, but it must still *report* everything a
    #: full run would: a warning that stops appearing because the item that
    #: raised it was not recomputed is exactly the silent-degradation failure
    #: this engine exists to prevent.
    item_diagnostics: dict[ItemId, list[Diagnostic]] = field(
        init=False, default_factory=dict
    )

    def __post_init__(self) -> None:
        self.periods = PeriodIndex.build(
            self.book.horizon,
            self.book.base_grain,
            self.book.calendar.fiscal_year_start_month,
        )
        self.calendar = BusinessCalendar.from_spec(self.book.calendar)
        self.dates = DateOps(periods=self.periods, calendar=self.calendar)
        self.time = TimeColumns.build(self.periods, self.calendar)
        # The union happens before the graph is built, not after it: synthetic
        # carriers for unattached events must be visible to `agg()` selectors,
        # which resolve to concrete ids at graph-build time (PRD §5.4).
        self.factset = resolve_facts(self.book, self.events)
        if self.factset.synthetic_items:
            self.book = self.book.model_copy(
                update={"items": augmented_items(self.book, self.factset)}
            )
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
        buckets = self.item_diagnostics

        def emit(diagnostic: Diagnostic) -> None:
            # One diagnostic per (code, item): a monthly clamp over five years is
            # one modelling fact, not sixty (DECISIONS D-P2-11). Bucketing by
            # item makes the dedup survive a partial recompute unchanged.
            bucket = buckets.setdefault(diagnostic.item_id, [])
            if all(existing.code != diagnostic.code for existing in bucket):
                bucket.append(diagnostic)

        for item_id in self.book.items:
            if item_id in stale or item_id not in self.accrual:
                self.accrual[item_id] = np.zeros(length, dtype=np.int64)
                self.cash[item_id] = np.zeros(length, dtype=np.int64)
                buckets.pop(item_id, None)
        for item_id in list(self.accrual):
            if item_id not in self.book.items:
                del self.accrual[item_id]
                del self.cash[item_id]
        for item_id in list(buckets):
            if item_id is not None and item_id not in self.book.items:
                del buckets[item_id]

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

        # 1b. Ledger facts, unioned into the same columns before any derived
        #     item is evaluated. Events are never suppressed by cutover: before
        #     it the ledger is the complete record (ADR-0004).
        self._apply_event_facts(stale, emit)

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
                self._evaluate_fold(component, kinds, settlement_kind, emit)

        diagnostics: list[Diagnostic] = list(self.compiled.diagnostics)
        diagnostics.extend(self.factset.diagnostics)
        for item_id in sorted(buckets, key=lambda key: (key is not None, key)):
            diagnostics.extend(buckets[item_id])

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

    def _apply_event_facts(self, stale: set[ItemId], emit) -> None:
        """Scatter ledger facts into the columns of the items they land in.

        Events sharing a target and a settlement are batched into one array
        operation, so a five-thousand-row import costs a handful of vector ops
        rather than five thousand scalar ones. Emits the settlement structure
        diagnostics of any settlement an event overrides
        (``CK-E004``/``CK-E005``) and the split's warnings
        (``CK-W001``/``CK-W002``).
        """
        for target, facts in self.factset.by_target().items():
            if target not in stale or target not in self.accrual:
                continue
            for carrier, group in _batch_by_settlement(facts):
                kind, structural = classify_settlement(carrier)
                for diagnostic in structural:
                    emit(diagnostic)
                ordinals = np.fromiter(
                    (fact.event.date.toordinal() for fact in group),
                    dtype=np.int64,
                    count=len(group),
                )
                amounts = np.fromiter(
                    (to_minor(fact.event.amount) for fact in group),
                    dtype=np.int64,
                    count=len(group),
                )
                indices = self.periods.index_of_ordinals(ordinals)
                # An event dated outside the horizon is outside the model, cash
                # legs included — the same rule generative occurrences follow
                # (DECISIONS D-P2-03).
                keep = indices >= 0
                if not keep.any():
                    continue
                ordinals, amounts, indices = ordinals[keep], amounts[keep], indices[keep]
                scatter_add(self.accrual[target], indices, amounts)
                for diagnostic in settle_occurrences(
                    carrier,
                    kind,
                    ordinals,
                    amounts,
                    indices,
                    self.cash[target],
                    self.dates,
                    self.policy,
                ):
                    emit(diagnostic)
            check_column(self.accrual[target], f"item {target!r} accrual")
            check_column(self.cash[target], f"item {target!r} cash")

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
        emit,
    ) -> None:
        """The sequential tier: one period at a time, members in same-period order.

        Each member's formula is staged into a closure by
        :func:`~cashkit.engine.fold.compile_cell` before the loop starts, so the
        per-period cost is arithmetic rather than AST dispatch. The staged
        semantics are pinned against the generic evaluator in
        ``tests/test_fold.py`` and against the ``Decimal`` oracle by the
        dual-engine gate.
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

        # The fold is the one sequential loop in the engine, so everything that
        # does not vary with the period is resolved before entering it: the cell
        # expressions are staged into closures, and each cash leg's landing
        # period is fixed calendar arithmetic over the period index.
        window = self._window(kinds, 0, 1)
        window.presum = presum
        plans: list[tuple] = []
        for compiled_item in members:
            assert compiled_item.expr is not None
            # A stock is a level, not a movement: it produces no cash leg
            # (DECISIONS D-P2-05).
            kind = (
                NEVER
                if compiled_item.item.kind == "stock"
                else settlement_kind[compiled_item.id]
            )
            plan = FoldSettlement.build(
                compiled_item.item, kind, self.periods, self.dates
            )
            plans.append(
                (
                    compile_cell(compiled_item.expr, window, self.policy),
                    self.accrual[compiled_item.id],
                    None if kind in (NEVER, INVALID) else self.cash[compiled_item.id],
                    compiled_item.id,
                    plan if plan.splits else None,
                )
            )

        starts = self.periods.starts
        policy = self.policy
        for period in range(len(self.periods)):
            for cell, accrual, cash, item_id, plan in plans:
                value, zero_div = cell(period)
                accrual[period] = value
                if zero_div:
                    emit(_zero_division(item_id, starts[period]))
                if cash is None:
                    continue
                if plan is None:
                    add_minor(cash, period, value)
                else:
                    for diagnostic in plan.apply(period, value, cash, policy):
                        emit(diagnostic)


def _batch_by_settlement(
    facts: list[EventFact],
) -> list[tuple[Item, list[EventFact]]]:
    """Group facts sharing a target by the settlement that governs them.

    Order-preserving, so the batching is deterministic. ``Settlement`` holds a
    list and is therefore unhashable, so the grouping compares by equality — the
    distinct count per item is one in the common case and tiny otherwise.
    """
    batches: list[tuple[Item, list[EventFact]]] = []
    for fact in facts:
        for carrier, group in batches:
            if carrier.settlement == fact.settlement_item.settlement:
                group.append(fact)
                break
        else:
            batches.append((fact.settlement_item, [fact]))
    return batches


def _zero_division(item_id: ItemId, period_start) -> Diagnostic:
    from cashkit.model.diagnostics import make_diagnostic

    return make_diagnostic(
        "CK-W005", item_id=item_id, field="formula", period=period_start.isoformat()
    )


def run(
    book: Book,
    *,
    policy: RoundingPolicy = RoundingPolicy.HALF_UP,
    events: tuple[Event, ...] | list[Event] = (),
) -> RunResult:
    """Evaluate ``book`` with the vectorized engine.

    ``events`` is the live ledger — tombstones already excluded, corrections
    included — unioned with generative expansion before derived evaluation.
    Returns a :class:`~cashkit.engine.result.RunResult` whose columns are int64
    minor units at 4 dp, byte-identical to ``cashkit.reference.run`` on the same
    book and events. See :meth:`Engine.run` for the diagnostics produced.
    """
    return Engine(book, policy, tuple(events)).run()
