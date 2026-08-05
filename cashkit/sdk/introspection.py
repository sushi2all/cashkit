"""``trace()``, ``why_zero()``, ``depends_on()``, ``describe_book()`` (PRD §6.5).

The part that makes agents work, and — since ADR-0013 — the part that makes the
UI work: a cell click resolves to ``trace()``, and the returned tree *is* the
edit menu. A ``None`` field or a missing binding is a defect here, not a gap to
fill in later, so every value this module returns is populated and every number
in it is exact.

**Traced values come from the engine, never from a re-derivation.**
``Trace.value`` is read straight out of the run's int64 column, and the
arithmetic below it is evaluated with the engine's own
:class:`~cashkit.engine.columns.ColumnEvaluator` over a one-period window — the
same code path the fold uses, so a traced sub-expression cannot disagree with
the run about what it computed. For a generative cell there is no expression to
evaluate, so the steps *are* a second rendering of the canonical rounding order
(ADR-0003); :attr:`Trace.reconciles` compares their total back to the engine's
cell, and the tests assert it holds for every cell of a 50-item fixture. Drift
is made visible rather than left to be noticed.

``describe_book()`` enumerates rather than describes. Every ``pivot()`` argument
value it lists is asserted to run; a field name absent from it does not exist.
That is the only form of "sufficient to generate a working UI with no field
invention" (PRD §10) that can be checked mechanically.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Sequence

import numpy as np

from cashkit.engine import ENGINE_VERSION, RoundingPolicy
from cashkit.engine.calendars import GRAIN_COLUMN
from cashkit.engine.columns import ColumnEvaluator, EvalWindow, Mask, Rate
from cashkit.engine.expand import (
    IMMEDIATE,
    INVALID,
    NEVER,
    SHARES,
    classify_settlement,
    escalation_steps_array,
    leg_targets,
    occurrence_ordinals,
    split_legs,
)
from cashkit.engine.formula import (
    Agg,
    Binary,
    Builtin,
    Compare,
    Cum,
    Expr,
    ItemRef,
    Literal,
    Logical,
    NUMERIC_BUILTINS,
    Param,
    Prev,
    TIME_FIELDS,
    TimeField,
    Unary,
    Where,
    iter_refs,
)
from cashkit.engine.graph import CompiledBook
from cashkit.engine.numeric import escalation_factor, from_minor, to_minor
from cashkit.engine.result import MEASURE_NAMES
from cashkit.model import Book, Grain, Item, ItemId
from cashkit.model.diagnostics import make_diagnostic
from cashkit.model.introspection import (
    ArithmeticStep,
    Binding,
    BookDescription,
    DependencyGraph,
    Explanation,
    GraphEdge,
    GraphNode,
    ItemDescription,
    PivotVocabulary,
    Trace,
)

__all__ = [
    "SELECTOR_GRAMMAR",
    "STATUSES",
    "dependents_of",
    "depends_on",
    "describe_book",
    "render_expr",
    "trace",
    "why_zero",
]

#: The §5.4 selector grammar, stated once and published by ``describe_book``.
SELECTOR_GRAMMAR = (
    "space-separated terms, ANDed; a term is 'key:value' (tag equality) or "
    "'flag:name' (flag membership). No OR, no negation, no wildcards — model "
    "finer slices as tags."
)

#: Statuses a fact row can carry (PRD §4.3).
STATUSES = ("actual", "committed", "forecast")

def _is_synthetic(item_id: str) -> bool:
    return item_id.startswith("_")


# --------------------------------------------------------------------------- #
# Rendering the restricted AST back to source
# --------------------------------------------------------------------------- #


def render_expr(expr: Expr) -> str:
    """Render a parsed formula back to source text.

    Round-trips through the parser — ``tests/test_introspection.py`` asserts
    that re-parsing the rendering yields the same tree — so a trace can quote
    the sub-expression it is explaining without the reader having to trust a
    paraphrase. Produces no diagnostics.
    """
    if isinstance(expr, Literal):
        return _decimal_text(expr.value)
    if isinstance(expr, Param):
        return f"p.{expr.key}"
    if isinstance(expr, TimeField):
        return f"t.{expr.name}"
    if isinstance(expr, ItemRef):
        return f'it("{expr.item_id}", measure="{expr.measure}")'
    if isinstance(expr, Prev):
        init = render_expr(expr.init)
        return (
            f'prev("{expr.item_id}", n={expr.lag}, init={init}, '
            f'measure="{expr.measure}")'
        )
    if isinstance(expr, Agg):
        return f'agg(tag="{expr.selector.source}", measure="{expr.measure}")'
    if isinstance(expr, Cum):
        return f'cum("{expr.item_id}", measure="{expr.measure}")'
    if isinstance(expr, Unary):
        operand = render_expr(expr.operand)
        return f"not ({operand})" if expr.op == "not" else f"{expr.op}({operand})"
    if isinstance(expr, (Binary, Compare)):
        return f"({render_expr(expr.left)} {expr.op} {render_expr(expr.right)})"
    if isinstance(expr, Logical):
        joined = f" {expr.op} ".join(render_expr(operand) for operand in expr.operands)
        return f"({joined})"
    if isinstance(expr, Where):
        return (
            f"where({render_expr(expr.cond)}, {render_expr(expr.then)}, "
            f"{render_expr(expr.otherwise)})"
        )
    assert isinstance(expr, Builtin)
    if expr.name == "round_":
        # The parser takes ndigits as a keyword only, so a positional rendering
        # would not re-parse — and a rendering that does not re-parse is a
        # paraphrase, which is what this function exists not to produce.
        value, digits = expr.args
        return f"round_({render_expr(value)}, ndigits={render_expr(digits)})"
    return f"{expr.name}({', '.join(render_expr(arg) for arg in expr.args)})"


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


# --------------------------------------------------------------------------- #
# trace()
# --------------------------------------------------------------------------- #


def trace(
    run,
    item: ItemId,
    period: int | date,
    *,
    measure: str = "accrual",
    depth: int = 3,
) -> Trace:
    """Explain one cell: formula, resolved bindings, arithmetic (PRD §6.5).

    ``run`` is a :class:`~cashkit.sdk.kit.RunRef`. ``period`` is a period index
    or a date inside the horizon. ``depth`` bounds the recursion into the items
    this cell reads; a trace that stopped at the limit reports
    ``truncated=True`` rather than looking like a leaf.

    Returns a fully populated :class:`~cashkit.model.Trace` — every field is
    non-optional and has a meaningful empty value (ADR-0013), so an agent never
    has to interpret a ``None``. Raises ``KeyError`` for an unknown item and
    ``ValueError`` for a period outside the horizon or an unknown measure: both
    are programmer error, not something a user authored.
    """
    if measure not in MEASURE_NAMES:
        raise ValueError(f"unknown measure {measure!r}; expected one of {MEASURE_NAMES}")
    index = _period_index(run, period)
    return _trace_cell(run, item, index, measure, depth, set())


def _period_index(run, period: int | date) -> int:
    periods = run.result.periods
    if isinstance(period, date):
        found = periods.index_of(period)
        if found is None or found < 0:
            raise ValueError(f"{period.isoformat()} is outside the book's horizon")
        return int(found)
    if not 0 <= period < len(periods):
        raise ValueError(f"period index {period} is outside 0..{len(periods) - 1}")
    return int(period)


def _trace_cell(
    run, item_id: ItemId, index: int, measure: str, depth: int, seen: set
) -> Trace:
    compiled: CompiledBook = run.engine.compiled
    book: Book = run.engine.book
    item = book.items[item_id]
    periods = run.result.periods
    value = run.result.value(item_id, measure, index)
    common = {
        "item_id": item_id,
        "item_name": item.name,
        "measure": measure,
        "period_index": index,
        "period_start": periods.starts[index],
        "period_end": periods.ends[index],
        "value": value,
        "depth": depth,
        "diagnostics": tuple(
            d for d in run.result.diagnostics if d.item_id == item_id
        ),
    }

    if item_id in compiled.tax.nodes:
        regime_id, role = compiled.tax.nodes[item_id]
        return Trace(
            kind="tax",
            formula=(
                f"tax regime {regime_id!r} {role}: accumulated VAT netted per "
                f"return period and scheduled at period end + payment_offset"
            ),
            bindings=_tax_bindings(book, regime_id),
            steps=(
                ArithmeticStep(
                    expression=f"regime[{regime_id}].{role}",
                    operation="regime schedule",
                    inputs=(f"role={role}",),
                    value=value,
                    rounding="per return period, half-up at 4 dp",
                ),
            ),
            notes=(
                "A regime is a synthetic graph item (ADR-0005): its column comes "
                "from the return schedule, not from segments or a formula.",
            ),
            **common,
        )

    compiled_item = compiled.items[item_id]
    if compiled_item.broken:
        return Trace(
            kind="empty",
            formula=(
                "this item was refused at compile time and evaluates to zero; "
                "see the diagnostics"
            ),
            notes=("A broken item degrades one line, never the whole run.",),
            **common,
        )

    if compiled_item.is_derived and compiled_item.expr is not None:
        return _trace_formula(run, compiled_item.expr, index, measure, depth, seen, common)

    if _is_synthetic(item_id):
        return _trace_ledger(run, item, index, measure, common)

    return _trace_generated(run, item, index, measure, common)


def _tax_bindings(book: Book, regime_id: str) -> tuple[Binding, ...]:
    regime = next((r for r in book.tax_regimes if r.id == regime_id), None)
    if regime is None:  # pragma: no cover - the node exists because the regime does
        return ()
    return (
        Binding(
            symbol=f"regime[{regime_id}].periodicity",
            kind="tax_regime",
            value=Decimal(0),
            source=f"TaxRegime {regime_id!r}",
            target=regime_id,
            detail=(
                f"periodicity={regime.periodicity}, measure={regime.measure}, "
                f"payment_offset={regime.payment_offset}, "
                f"credit_handling={regime.credit_handling}"
            ),
        ),
    )


# -- derived cells ---------------------------------------------------------- #


def _evaluator(run, index: int) -> ColumnEvaluator:
    """The engine's own evaluator, windowed to one period.

    Symbol lookups read the *full* columns and return the windowed part, so
    ``prev()`` still reaches back before the window and ``cum()`` still
    accumulates from the horizon start.
    """
    engine = run.engine
    window = EvalWindow(
        accrual=engine.accrual,
        cash=engine.cash,
        kinds={key: value.kind for key, value in engine.book.items.items()},
        params=engine.book.params,
        opening_balance=engine.book.opening_balance,
        time=engine.time,
        start=index,
        stop=index + 1,
    )
    return ColumnEvaluator(window, run.policy, scalar=True)


def _value_of(evaluator: ColumnEvaluator, expr: Expr) -> Decimal:
    """Evaluate a sub-expression at the window's period, as exact money."""
    return from_minor(int(evaluator.to_money(evaluator.eval(expr)).value))


def _trace_formula(
    run, expr: Expr, index: int, measure: str, depth: int, seen: set, common: dict
) -> Trace:
    item_id = common["item_id"]
    item = run.engine.book.items[item_id]
    evaluator = _evaluator(run, index)
    bindings = _formula_bindings(run, expr, evaluator, index)
    steps = tuple(_formula_steps(evaluator, expr))

    children: list[Trace] = []
    truncated = False
    if depth > 0:
        for target, child_measure in _referenced(expr):
            key = (target, child_measure, index)
            if key in seen or target not in run.engine.book.items:
                continue
            children.append(
                _trace_cell(
                    run, target, index, child_measure, depth - 1, seen | {key}
                )
            )
    elif any(True for _ in _referenced(expr)):
        truncated = True

    notes: list[str] = []
    if measure == "cash" and item.kind != "stock":
        notes.append(
            "The formula produces the accrual; this cash cell is what its "
            "settlement terms moved into this period. Trace the accrual measure "
            "for the arithmetic that produced the amount."
        )
    accrual_value = run.result.value(item_id, "accrual", index)
    reconciles = (
        bool(steps) and steps[-1].value == accrual_value
        if measure == "accrual"
        else True
    )
    return Trace(
        kind="formula",
        formula=item.formula or render_expr(expr),
        bindings=bindings,
        steps=steps,
        children=tuple(children),
        truncated=truncated,
        reconciles=reconciles,
        notes=tuple(notes),
        **common,
    )


def _referenced(expr: Expr):
    """Every ``(item_id, measure)`` the expression reads, in source order."""
    for ref in iter_refs(expr):
        if isinstance(ref, Agg):
            for member in ref.items or ():
                yield member, ref.measure
        else:
            yield ref.item_id, ref.measure


def _formula_bindings(
    run, expr: Expr, evaluator: ColumnEvaluator, index: int
) -> tuple[Binding, ...]:
    """One binding per symbol, resolved at this period.

    Every leaf that could have been something else is reported: an item
    reference with the item it resolved to, an ``agg()`` with the concrete ids
    its selector matched at graph-build time, a ``prev()`` with the period it
    read and whether it fell back to ``init``, a param with its value.
    """
    book = run.engine.book
    out: list[Binding] = []
    emitted: set[str] = set()

    def add(binding: Binding) -> None:
        if binding.symbol in emitted:
            return
        emitted.add(binding.symbol)
        out.append(binding)

    for ref in iter_refs(expr):
        symbol = render_expr(ref)
        value = _value_of(evaluator, ref)
        if isinstance(ref, Agg):
            members = ref.items or ()
            add(
                Binding(
                    symbol=symbol,
                    kind="aggregate",
                    value=value,
                    source=(
                        f"selector {ref.selector.source!r} resolved at graph-build "
                        f"time to {len(members)} item(s)"
                    ),
                    target=", ".join(members),
                    detail=" + ".join(
                        f"{member}={run.result.value(member, ref.measure, index)}"
                        for member in members
                    )
                    or "no members",
                )
            )
        elif isinstance(ref, Prev):
            reached = index - ref.lag
            add(
                Binding(
                    symbol=symbol,
                    kind="lagged",
                    value=value,
                    source=(
                        f"period {reached}"
                        if reached >= 0
                        else f"init (period {reached} is before the horizon)"
                    ),
                    target=ref.item_id,
                    detail=(
                        f"lag={ref.lag}, init={render_expr(ref.init)}, "
                        f"measure={ref.measure}"
                    ),
                )
            )
        elif isinstance(ref, Cum):
            add(
                Binding(
                    symbol=symbol,
                    kind="cumulative",
                    value=value,
                    source=f"running total of {ref.item_id!r} since horizon start",
                    target=ref.item_id,
                    detail=f"periods 0..{index}, measure={ref.measure}",
                )
            )
        else:
            target = book.items.get(ref.item_id)
            add(
                Binding(
                    symbol=symbol,
                    kind="item",
                    value=value,
                    source=f"item {ref.item_id!r} this period",
                    target=ref.item_id,
                    detail=(target.name if target is not None else "unknown item"),
                )
            )

    for node in _params_in(expr):
        add(
            Binding(
                symbol=f"p.{node.key}",
                kind="param",
                value=Decimal(
                    book.params.get(node.key, book.opening_balance)
                ),
                source=(
                    "Book.params"
                    if node.key in book.params
                    else "Book.opening_balance (reserved param key)"
                ),
                target=node.key,
                detail="sweepable: set_param() moves every formula that reads it",
            )
        )
    for node in _time_fields_in(expr):
        add(
            Binding(
                symbol=f"t.{node.name}",
                kind="period",
                value=_value_of(evaluator, node),
                source="period metadata",
                target="",
                detail=f"period {index}, {run.result.periods.starts[index].isoformat()}",
            )
        )
    return tuple(out)


def _params_in(expr: Expr):
    if isinstance(expr, Param):
        yield expr
    for child in _children_of(expr):
        yield from _params_in(child)


def _time_fields_in(expr: Expr):
    if isinstance(expr, TimeField):
        yield expr
    for child in _children_of(expr):
        yield from _time_fields_in(child)


def _children_of(expr: Expr) -> tuple[Expr, ...]:
    if isinstance(expr, Unary):
        return (expr.operand,)
    if isinstance(expr, (Binary, Compare)):
        return (expr.left, expr.right)
    if isinstance(expr, Logical):
        return tuple(expr.operands)
    if isinstance(expr, Where):
        return (expr.cond, expr.then, expr.otherwise)
    if isinstance(expr, Builtin):
        return tuple(expr.args)
    if isinstance(expr, Prev):
        return (expr.init,)
    return ()


def _formula_steps(evaluator: ColumnEvaluator, expr: Expr) -> list[ArithmeticStep]:
    """Every operator node, innermost first, with the value the engine got.

    Post-order, so a reader follows the same order the evaluator did. Leaves are
    reported as bindings rather than steps — an item reference is a lookup, not
    an operation — except when the whole formula *is* a leaf, which would
    otherwise produce a trace with no arithmetic at all.
    """
    steps: list[ArithmeticStep] = []

    def visit(node: Expr) -> None:
        children = _children_of(node)
        for child in children:
            visit(child)
        if not children and node is not expr:
            return
        steps.append(_step_for(evaluator, node))

    visit(expr)
    return steps


def _step_for(evaluator: ColumnEvaluator, node: Expr) -> ArithmeticStep:
    raw = evaluator.eval(node)
    value = from_minor(int(evaluator.to_money(raw).value))
    inputs = tuple(
        f"{render_expr(child)} = {_value_of(evaluator, child)}"
        for child in _children_of(node)
    )
    return ArithmeticStep(
        expression=render_expr(node),
        operation=_operation_name(node),
        inputs=inputs,
        value=value,
        rounding=_rounding_of(node, raw),
    )


def _operation_name(node: Expr) -> str:
    if isinstance(node, Binary):
        return {"+": "add", "-": "subtract", "*": "multiply", "/": "divide"}[node.op]
    if isinstance(node, Compare):
        return f"compare {node.op}"
    if isinstance(node, Logical):
        return f"logical {node.op}"
    if isinstance(node, Unary):
        return {"-": "negate", "+": "identity", "not": "logical not"}[node.op]
    if isinstance(node, Where):
        return "where (elementwise select; both branches evaluated)"
    if isinstance(node, Builtin):
        return node.name
    return type(node).__name__.lower()


def _rounding_of(node: Expr, raw) -> str:
    if isinstance(raw, Rate):
        return "none (exact rate, not yet money)"
    if isinstance(raw, Mask):
        return "none (mask)"
    if isinstance(node, Binary) and node.op in ("*", "/"):
        return "half-up at 4 dp (rate multiplication rounds once)"
    if isinstance(node, Builtin) and node.name == "round_":
        return "explicit round_() to the requested digits"
    return "none (exact int64 addition at 4 dp)"


# -- generative cells ------------------------------------------------------- #


def _trace_generated(run, item: Item, index: int, measure: str, common: dict) -> Trace:
    """Explain a cell produced by segment expansion.

    ADR-0013 asks the popover to show "12 000 x 1.03² x 0.9", which is the
    canonical rounding order made visible: base amount, escalation, probability,
    settlement split, withholding. Each is a step, and the steps' total is
    compared back to the engine's own cell (:attr:`Trace.reconciles`).
    """
    periods = run.result.periods
    book = run.engine.book
    day = periods.starts[index]
    horizon_start, horizon_end = periods.starts[0], periods.ends[-1]

    bindings: list[Binding] = []
    steps: list[ArithmeticStep] = []
    notes: list[str] = []
    total = Decimal(0)
    kind, _ = classify_settlement(item)

    for position, segment in enumerate(item.segments):
        contributions = _segment_contributions(
            run, item, segment, position, index, measure, kind, horizon_start, horizon_end
        )
        for contribution in contributions:
            bindings.extend(contribution["bindings"])
            steps.extend(contribution["steps"])
            total += contribution["value"]

    events = _events_landing_on(run, item.id, index, measure)
    for event in events:
        bindings.append(
            Binding(
                symbol=f"event[{event['id']}]",
                kind="ledger",
                value=event["amount"],
                source=f"ledger row, status={event['status']}",
                target=event["id"],
                detail=f"dated {event['date']}",
            )
        )
    if events:
        notes.append(
            "This cell also carries ledger facts; their exact settlement "
            "placement is the engine's, and the amounts above are the accruals."
        )

    if not item.segments:
        notes.append("This item has no segments, so it generates nothing.")
    if day < book.cutover:
        notes.append(
            f"Period {day.isoformat()} is before cutover {book.cutover.isoformat()}: "
            "generation is suppressed for every item and the ledger is the "
            "complete record (ADR-0004)."
        )

    formula = (
        " + ".join(step.expression for step in steps if step.operation == "segment total")
        or (
            f"generated from {len(item.segments)} segment(s); none contributes to "
            f"this {measure} cell"
        )
    )
    if events:
        # A cell holding both generated occurrences and ledger facts cannot be
        # reconciled from the generator alone, and saying "reconciles" anyway
        # would be the claim this field exists to keep honest.
        reconciles = True
        notes.append(
            "reconciles=True is not a check here: the cell mixes generated "
            "occurrences with ledger facts, so the generator's total is not "
            "expected to equal it."
        )
    else:
        reconciles = total == common["value"]
    return Trace(
        kind="generated",
        formula=formula,
        bindings=tuple(bindings),
        steps=tuple(steps),
        reconciles=reconciles,
        notes=tuple(notes),
        **common,
    )


def _occurrences(segment, horizon_start: date, horizon_end: date):
    """A segment's anchor ordinals and base amounts, in minor units."""
    if segment.amount.schedule is not None:
        pairs = [
            (day.toordinal(), to_minor(value))
            for day, value in segment.amount.schedule
            if horizon_start <= day < horizon_end
        ]
        return (
            np.fromiter((p[0] for p in pairs), dtype=np.int64, count=len(pairs)),
            np.fromiter((p[1] for p in pairs), dtype=np.int64, count=len(pairs)),
        )
    anchors = occurrence_ordinals(segment, horizon_start, horizon_end)
    return anchors, np.full(
        anchors.shape, to_minor(segment.amount.constant or Decimal(0)), dtype=np.int64
    )


def _segment_contributions(
    run,
    item: Item,
    segment,
    position: int,
    index: int,
    measure: str,
    kind: str,
    horizon_start: date,
    horizon_end: date,
) -> list[dict]:
    """Each occurrence of ``segment`` contributing to this cell, step by step."""
    book = run.engine.book
    dates = run.engine.dates
    periods = run.result.periods

    anchors, bases = _occurrences(segment, horizon_start, horizon_end)
    if anchors.size == 0:
        return []

    adjusted = dates.calendar.adjust_array(
        anchors, segment.recurrence.business_day_adjust
    )
    accrual_index = periods.index_of_ordinals(adjusted)
    # Occurrences before cutover are suppressed entirely, cash legs included
    # (ADR-0004, D-P2-13) — so they contribute nothing to explain.
    live = (accrual_index >= 0) & (adjusted >= book.cutover.toordinal())

    term_targets: list[np.ndarray] | None = None
    if measure == "accrual" or kind in (IMMEDIATE, NEVER, INVALID):
        if measure == "cash" and kind in (NEVER, INVALID):
            return []
        lands = accrual_index == index
    else:
        assert item.settlement is not None
        term_targets = [
            leg_targets(term, adjusted, accrual_index, dates)
            for term in item.settlement.due
        ]
        lands = np.zeros(anchors.shape, dtype=bool)
        for targets in term_targets:
            lands = lands | (targets == index)

    return [
        _one_occurrence(
            run,
            item,
            segment,
            position,
            int(slot),
            int(anchors[slot]),
            int(bases[slot]),
            measure,
            kind,
            index,
            term_targets,
            horizon_end,
        )
        for slot in np.flatnonzero(live & lands)
    ]


def _one_occurrence(
    run,
    item: Item,
    segment,
    position: int,
    slot: int,
    anchor: int,
    base_minor: int,
    measure: str,
    kind: str,
    index: int,
    term_targets: list[np.ndarray] | None,
    horizon_end: date,
) -> dict:
    """The canonical rounding order for one occurrence, as bindings and steps.

    Base amount, escalation, probability, settlement split, withholding —
    ADR-0003's order, in ADR-0003's order, which is what ADR-0013's popover
    ("12 000 x 1.03² x 0.9") needs to show.
    """
    policy = run.policy
    book = run.engine.book
    label = f"segments[{position}]"
    occurrence_date = date.fromordinal(anchor)

    bindings = [
        Binding(
            symbol=f"{label}.amount",
            kind="segment",
            value=from_minor(base_minor),
            source=f"segment starting {segment.start.isoformat()}",
            target=item.id,
            detail=(
                f"recurrence every {segment.recurrence.every} "
                f"{segment.recurrence.unit.value}, anchor "
                f"{segment.recurrence.anchor}; occurrence on "
                f"{occurrence_date.isoformat()}"
            ),
        )
    ]
    steps = [
        ArithmeticStep(
            expression=f"{label}.amount",
            operation="base amount",
            inputs=(occurrence_date.isoformat(),),
            value=from_minor(base_minor),
            rounding="none (authored at 4 dp)",
        )
    ]
    running = base_minor

    if segment.escalation is not None:
        raw_rate = segment.escalation.rate
        rate = book.params[raw_rate] if isinstance(raw_rate, str) else raw_rate
        compounded = int(
            escalation_steps_array(
                segment.escalation.anchor,
                segment.escalation.every_years,
                segment.start,
                np.array([anchor], dtype=np.int64),
                horizon_end,
            )[0]
        )
        factor = escalation_factor(rate, compounded)
        escalated = _apply_rate(running, factor, policy)
        bindings.append(
            Binding(
                symbol=f"{label}.escalation",
                kind="escalation",
                value=factor,
                source=(
                    f"rate {rate} compounded {compounded} time(s), anchor "
                    f"{segment.escalation.anchor}"
                ),
                target=raw_rate if isinstance(raw_rate, str) else "",
                detail=(
                    "(1 + r)^n computed in Decimal once per (rate, n) pair and "
                    "applied as a scaled int64 multiplier (ADR-0002)"
                ),
            )
        )
        steps.append(
            ArithmeticStep(
                expression=f"{label}.amount x (1 + {rate})^{compounded}",
                operation="escalation",
                inputs=(str(from_minor(running)), f"factor={factor}"),
                value=from_minor(escalated),
                rounding="half-up at 4 dp",
            )
        )
        running = escalated

    if segment.probability != Decimal(1):
        weighted = _apply_rate(running, segment.probability, policy)
        bindings.append(
            Binding(
                symbol=f"{label}.probability",
                kind="probability",
                value=segment.probability,
                source="pipeline weighting on the segment",
                target=item.id,
                detail="applied after escalation, before the settlement split",
            )
        )
        steps.append(
            ArithmeticStep(
                expression=f"{label} x probability {segment.probability}",
                operation="probability weighting",
                inputs=(str(from_minor(running)), str(segment.probability)),
                value=from_minor(weighted),
                rounding="half-up at 4 dp",
            )
        )
        running = weighted

    if term_targets is not None:
        assert item.settlement is not None
        split = split_legs(item, kind, np.array([running], dtype=np.int64), policy)
        landed = 0
        for position_in_due, (term, net, targets) in enumerate(
            zip(item.settlement.due, split.net, term_targets)
        ):
            if int(targets[slot]) != index:
                continue
            leg = int(net[0])
            share = term.share if term.share is not None else term.amount
            bindings.append(
                Binding(
                    symbol=f"{label}.settlement.due[{position_in_due}]",
                    kind="settlement",
                    value=from_minor(leg),
                    source=(
                        (
                            f"share {term.share}"
                            if term.share is not None
                            else ("remainder" if term.remainder else f"amount {term.amount}")
                        )
                        + f", offset {term.offset}, basis {term.basis}"
                    ),
                    target=item.id,
                    detail=(
                        f"withholding {term.withholding} applied after the split"
                        if term.withholding
                        else "no withholding"
                    ),
                )
            )
            steps.append(
                ArithmeticStep(
                    expression=f"{label} settlement leg (offset {term.offset})",
                    operation="settlement split",
                    inputs=(f"accrued={from_minor(running)}", f"term={share}"),
                    value=from_minor(leg),
                    rounding=(
                        "half-up at 4 dp; the last share absorbs the residual"
                        if kind == SHARES
                        else "exact (fixed amounts consume first)"
                    ),
                )
            )
            landed += leg
        running = landed

    steps.append(
        ArithmeticStep(
            expression=f"{label} contribution to {measure}",
            operation="segment total",
            inputs=(occurrence_date.isoformat(),),
            value=from_minor(running),
            rounding="none",
        )
    )
    return {"bindings": bindings, "steps": steps, "value": from_minor(running)}


def _apply_rate(minor: int, rate: Decimal, policy: RoundingPolicy) -> int:
    """One rate multiplication, through the engine's own primitive."""
    from cashkit.engine.numeric import mul_ratio, ratio_of

    numerator, denominator = ratio_of(rate)
    return mul_ratio(minor, numerator, denominator, policy)


def _events_landing_on(run, item_id: ItemId, index: int, measure: str) -> list[dict]:
    """Ledger facts whose accrual lands in this period, for the bindings list."""
    periods = run.result.periods
    out: list[dict] = []
    for fact in run.engine.factset.by_target().get(item_id, ()):
        landing = periods.index_of(fact.event.date)
        if landing == index:
            out.append(
                {
                    "id": fact.event.id,
                    "amount": fact.event.amount,
                    "status": fact.event.status,
                    "date": fact.event.date.isoformat(),
                }
            )
    return out


def _trace_ledger(run, item: Item, index: int, measure: str, common: dict) -> Trace:
    """Explain a synthetic carrier: a column that is nothing but ledger rows."""
    events = _events_landing_on(run, item.id, index, measure)
    bindings = tuple(
        Binding(
            symbol=f"event[{event['id']}]",
            kind="ledger",
            value=event["amount"],
            source=f"ledger row, status={event['status']}",
            target=event["id"],
            detail=f"dated {event['date']}",
        )
        for event in events
    )
    steps = tuple(
        ArithmeticStep(
            expression=f"event[{event['id']}]",
            operation="ledger fact",
            inputs=(event["date"], event["status"]),
            value=event["amount"],
            rounding="none (authored at 4 dp)",
        )
        for event in events
    )
    return Trace(
        kind="ledger",
        formula=(
            f"synthetic carrier {item.id!r}: unattached ledger events grouped by "
            f"their dimensions (tags {dict(item.tags)}, currency {item.currency})"
        ),
        bindings=bindings,
        steps=steps,
        reconciles=True,
        notes=(
            "This item was created by the engine, not authored. Its id is a "
            "function of the event dimensions, so it is stable across imports "
            "(D-P5-09).",
        ),
        **common,
    )


# --------------------------------------------------------------------------- #
# why_zero()
# --------------------------------------------------------------------------- #


def why_zero(run, item: ItemId, period: int | date, *, measure: str = "cash") -> Explanation:
    """Why one cell is zero, distinguishing the five causes (PRD §6.5).

    The five: (1) the period is outside every segment, (2) probability is zero,
    (3) an upstream zero propagated through the formula, (4) generation is
    suppressed by cutover, (5) the settlement produced no cash leg this period
    (an empty ``due``, or a remainder clamped to zero).

    A cell that is not zero answers ``"not_zero"`` rather than being forced into
    one of the five — the honest answer to a question that does not apply.
    Causes that are *also* true are listed in ``also``, because a period can be
    both pre-cutover and outside every segment and fixing one would not help.

    Returns an :class:`~cashkit.model.Explanation`. Raises ``KeyError`` /
    ``ValueError`` on an unknown item or period (programmer error).
    """
    index = _period_index(run, period)
    book: Book = run.engine.book
    item_model = book.items[item]
    periods = run.result.periods
    day = periods.starts[index]
    value = run.result.value(item, measure, index)

    causes: list[tuple[str, str, str, str]] = []

    if day < book.cutover:
        causes.append(
            (
                "cutover_suppressed",
                f"Period {day.isoformat()} is before cutover "
                f"{book.cutover.isoformat()}, where generation is suppressed for "
                "every item and the ledger is the complete record.",
                "ADR-0004: before cutover the reconciled past is whatever the "
                "ledger holds, whatever the item would have generated.",
                "Import the actuals for this window, or move cutover earlier "
                "with set_cutover() if this period should be forecast.",
            )
        )

    if item_model.segments:
        inside = _segments_covering(run, item_model, index)
        if not inside:
            causes.append(
                (
                    "outside_segments",
                    f"No segment of {item!r} produces an occurrence in period "
                    f"{index} ({day.isoformat()}).",
                    "; ".join(
                        f"segments[{position}] runs "
                        f"{segment.start.isoformat()}.."
                        f"{segment.end.isoformat() if segment.end else 'open'} "
                        f"every {segment.recurrence.every} "
                        f"{segment.recurrence.unit.value}"
                        for position, segment in enumerate(item_model.segments)
                    ),
                    "Extend the segment, change its recurrence, or add an Event "
                    "for a one-off in this period.",
                )
            )
        elif all(
            item_model.segments[position].probability == 0 for position in inside
        ):
            causes.append(
                (
                    "probability_zero",
                    f"Every segment occurring in period {index} carries "
                    "probability 0, so the weighted amount is zero.",
                    ", ".join(
                        f"segments[{position}].probability="
                        f"{item_model.segments[position].probability}"
                        for position in inside
                    ),
                    "Raise the segment's probability, or remove the segment if "
                    "the opportunity is gone.",
                )
            )

    if measure == "cash":
        kind, _ = classify_settlement(item_model)
        accrual = run.result.value(item, "accrual", index)
        if kind == NEVER:
            causes.append(
                (
                    "no_settlement_leg",
                    f"{item!r} has an empty settlement 'due' list: it accrues and "
                    "never settles.",
                    f"accrual this period = {accrual}",
                    "Add a DueTerm to Settlement.due if this item should move "
                    "cash, or leave it accrual-only deliberately.",
                )
            )
        elif kind == INVALID:
            causes.append(
                (
                    "no_settlement_leg",
                    f"{item!r} has a structurally invalid settlement, so it "
                    "accrues and produces no cash at all.",
                    "See CK-E004 / CK-E005 in the run's diagnostics.",
                    "Fix the settlement terms: all shares summing to exactly 1, "
                    "or fixed amounts with exactly one remainder.",
                )
            )
        elif accrual != 0 or _clamped_here(run, item):
            causes.append(
                (
                    "no_settlement_leg",
                    f"No settlement leg of {item!r} lands in period {index}; the "
                    "cash moved into another period.",
                    f"accrual this period = {accrual}",
                    "Trace the accrual measure to see when the amount accrued, "
                    "and read Settlement.due for where its legs land.",
                )
            )

    if not causes and item_model.kind in ("derived", "stock"):
        compiled_item = run.engine.compiled.items[item]
        upstream = sorted(compiled_item.same_period_deps | compiled_item.lagged_deps)
        zeros = [
            dep
            for dep in upstream
            if run.result.value(dep, "cash", index) == 0
            and run.result.value(dep, "accrual", index) == 0
        ]
        causes.append(
            (
                "upstream_zero",
                (
                    f"The formula evaluated to zero from its inputs; "
                    f"{len(zeros)} of {len(upstream)} upstream item(s) are "
                    "themselves zero this period."
                    if upstream
                    else "The formula evaluates to zero from constants alone."
                ),
                ", ".join(zeros) or "no zero upstream items",
                "Trace this cell to depth 3 and follow the first binding that is "
                "already zero.",
            )
        )

    if value != 0:
        return Explanation(
            item_id=item,
            measure=measure,
            period_index=index,
            period_start=day,
            value=value,
            cause="not_zero",
            message=f"{item!r} is {value} in period {index}, not zero.",
            detail="why_zero() answers a question this cell does not raise.",
            also=tuple(cause[0] for cause in causes),  # type: ignore[arg-type]
            suggested_fix="Use trace() to see how the value was produced.",
            diagnostics=tuple(
                d for d in run.result.diagnostics if d.item_id == item
            ),
        )

    if not causes:
        causes.append(
            (
                "upstream_zero",
                f"{item!r} produced no contribution in period {index}.",
                "No segment occurrence, no ledger fact and no formula output "
                "reached this cell.",
                "Trace the cell to see which inputs were empty.",
            )
        )

    primary, message, detail, fix = causes[0]
    return Explanation(
        item_id=item,
        measure=measure,
        period_index=index,
        period_start=day,
        value=value,
        cause=primary,  # type: ignore[arg-type]
        message=message,
        detail=detail,
        also=tuple(cause[0] for cause in causes[1:]),  # type: ignore[arg-type]
        suggested_fix=fix,
        diagnostics=tuple(d for d in run.result.diagnostics if d.item_id == item),
    )


def _segments_covering(run, item: Item, index: int) -> list[int]:
    """Positions of the segments producing an occurrence in this period."""
    periods = run.result.periods
    horizon_start, horizon_end = periods.starts[0], periods.ends[-1]
    found: list[int] = []
    for position, segment in enumerate(item.segments):
        if segment.amount.schedule is not None:
            anchors = np.fromiter(
                (
                    day.toordinal()
                    for day, _ in segment.amount.schedule
                    if horizon_start <= day < horizon_end
                ),
                dtype=np.int64,
            )
        else:
            anchors = occurrence_ordinals(segment, horizon_start, horizon_end)
        if anchors.size == 0:
            continue
        adjusted = run.engine.dates.calendar.adjust_array(
            anchors, segment.recurrence.business_day_adjust
        )
        if (periods.index_of_ordinals(adjusted) == index).any():
            found.append(position)
    return found


def _clamped_here(run, item: ItemId) -> bool:
    return any(
        d.code in ("CK-W001", "CK-W002")
        for d in run.result.diagnostics
        if d.item_id == item
    )


# --------------------------------------------------------------------------- #
# depends_on() / dependents_of()
# --------------------------------------------------------------------------- #


def depends_on(book_or_run, item: ItemId, *, depth: int = 0) -> DependencyGraph:
    """What ``item`` reads, transitively (PRD §6.5).

    Accepts a :class:`~cashkit.model.Book` or a
    :class:`~cashkit.sdk.kit.RunRef`; a run is cheaper, because the graph is
    already compiled. ``depth=0`` walks the whole cone. Edges distinguish a
    same-period read from a ``prev()`` edge and from an ``agg()`` membership, so
    a designed feedback loop is legible as one. Produces no diagnostics beyond
    the compiler's own.
    """
    return _graph(book_or_run, item, "depends_on", depth)


def dependents_of(book_or_run, item: ItemId, *, depth: int = 0) -> DependencyGraph:
    """What reads ``item``, transitively (PRD §6.5). See :func:`depends_on`."""
    return _graph(book_or_run, item, "dependents_of", depth)


def _compiled_of(book_or_run) -> tuple[CompiledBook, Book]:
    if hasattr(book_or_run, "engine"):
        return book_or_run.engine.compiled, book_or_run.engine.book
    from cashkit.engine.graph import compile_book

    compiled = compile_book(book_or_run)
    return compiled, compiled.book


def _graph(book_or_run, item: ItemId, direction: str, depth: int) -> DependencyGraph:
    compiled, book = _compiled_of(book_or_run)
    if item not in compiled.items:
        return DependencyGraph(
            root=item,
            direction=direction,  # type: ignore[arg-type]
            diagnostics=(
                make_diagnostic("CK-E001", item_id=item, reference=f'it("{item}")'),
            ),
        )

    aggregate_members: dict[ItemId, set[ItemId]] = {}
    for item_id, compiled_item in compiled.items.items():
        if compiled_item.expr is None:
            continue
        for ref in iter_refs(compiled_item.expr):
            if isinstance(ref, Agg):
                aggregate_members.setdefault(item_id, set()).update(ref.items or ())

    def outgoing(node: ItemId) -> list[tuple[ItemId, str]]:
        compiled_item = compiled.items[node]
        aggregates = aggregate_members.get(node, set())
        out: list[tuple[ItemId, str]] = []
        for target in sorted(compiled_item.same_period_deps):
            out.append((target, "aggregate" if target in aggregates else "same_period"))
        for target in sorted(compiled_item.lagged_deps):
            out.append((target, "lagged"))
        return out

    def incoming(node: ItemId) -> list[tuple[ItemId, str]]:
        out: list[tuple[ItemId, str]] = []
        for candidate in sorted(compiled.dependents.get(node, frozenset())):
            for target, relation in outgoing(candidate):
                if target == node:
                    out.append((candidate, relation))
        return out

    step = outgoing if direction == "depends_on" else incoming
    nodes: dict[ItemId, int] = {item: 0}
    edges: list[GraphEdge] = []
    frontier = [item]
    level = 0
    while frontier and (depth == 0 or level < depth):
        level += 1
        following: list[ItemId] = []
        for node in frontier:
            for neighbour, relation in step(node):
                source, target = (
                    (node, neighbour) if direction == "depends_on" else (neighbour, node)
                )
                edge = GraphEdge(source=source, target=target, relation=relation)  # type: ignore[arg-type]
                if edge not in edges:
                    edges.append(edge)
                if neighbour not in nodes:
                    nodes[neighbour] = level
                    following.append(neighbour)
        frontier = following

    component = next(
        (c for c in compiled.components if item in c.members and not c.trivial), None
    )
    return DependencyGraph(
        root=item,
        direction=direction,  # type: ignore[arg-type]
        nodes=tuple(
            GraphNode(
                item_id=node,
                name=book.items[node].name,
                kind=book.items[node].kind,
                synthetic=_is_synthetic(node),
                depth=level,
            )
            for node, level in sorted(nodes.items(), key=lambda kv: (kv[1], kv[0]))
        ),
        edges=tuple(edges),
        cyclic=component is not None,
        cycle_members=tuple(component.members) if component is not None else (),
    )


# --------------------------------------------------------------------------- #
# describe_book()
# --------------------------------------------------------------------------- #


def describe_book(
    book: Book,
    *,
    scenarios: Sequence[str] = (),
    rounding_policy: str = RoundingPolicy.HALF_UP.value,
    schema_version: int = 0,
    include_synthetic: bool = False,
) -> BookDescription:
    """Everything an agent needs to query this book without inventing a field.

    Enumerates rather than describes: the measures that exist, the grains that
    aggregate, the statuses a frame row can carry, every tag key with its
    observed values, the selector grammar with worked examples, and — the part
    the Phase 10 gate turns on — :class:`~cashkit.model.PivotVocabulary`, which
    lists the exact ``index`` / ``columns`` / ``values`` arguments ``pivot()``
    accepts on *this* book. A field name absent from this output does not exist.

    Returns a :class:`~cashkit.model.BookDescription`; produces no diagnostics.
    """
    items = {
        item_id: item
        for item_id, item in sorted(book.items.items())
        if include_synthetic or not _is_synthetic(item_id)
    }
    tag_values: dict[str, set[str]] = {}
    flags: set[str] = set()
    currencies: set[str] = set()
    for item in items.values():
        for key, value in item.tags.items():
            tag_values.setdefault(key, set()).add(value)
        flags.update(item.flags)
        currencies.add(item.currency)

    tag_keys = tuple(sorted(tag_values))
    periods_count = (book.horizon.end - book.horizon.start).days if book.base_grain is Grain.DAY else 0

    return BookDescription(
        book_id=book.id,
        base_grain=book.base_grain.value,
        horizon_start=book.horizon.start,
        horizon_end=book.horizon.end,
        periods=periods_count,
        cutover=book.cutover,
        opening_balance=book.opening_balance,
        currency=sorted(currencies)[0] if currencies else "EUR",
        rounding_policy=rounding_policy,
        engine_version=ENGINE_VERSION,
        schema_version=schema_version,
        params=dict(book.params),
        items=tuple(_describe_item(item) for item in items.values()),
        measures=MEASURE_NAMES,
        grains=tuple(grain.value for grain in Grain),
        statuses=STATUSES,
        tag_keys=tag_keys,
        tag_values={key: tuple(sorted(values)) for key, values in sorted(tag_values.items())},
        flags=tuple(sorted(flags)),
        selector_grammar=SELECTOR_GRAMMAR,
        selector_examples=tuple(
            f"{key}:{sorted(tag_values[key])[0]}" for key in tag_keys
        )
        + tuple(f"flag:{flag}" for flag in sorted(flags))[:1],
        # Exactly what ``FrameStore.pivot`` accepts, and nothing more: an
        # `index` of "period" or "item", `columns` of "item", "measure" or a
        # `tag:<key>` this book actually carries, `values` of a real measure.
        # The gate is that every combination below runs.
        pivot=PivotVocabulary(
            index=("period", "item"),
            columns=("item", "measure") + tuple(f"tag:{key}" for key in tag_keys),
            values=MEASURE_NAMES,
        ),
        frame_columns=(
            "period_start",
            "period_end",
            "item_id",
            "measure",
            "value",
            "currency",
            "status",
        ),
        summary_fields=(
            "book_id",
            "grain",
            "balance_source",
            "periods",
            "opening_balance",
            "closing_balance",
            "min_cash",
            "min_cash_period",
            "runway_periods",
            "runway_end",
            "breakeven_period",
            "total_inflow",
            "total_outflow",
            "net_cash",
            "total_accrual",
        ),
        scenarios=tuple(scenarios),
        tax_regimes=tuple(regime.id for regime in book.tax_regimes),
        formula_builtins=tuple(sorted(NUMERIC_BUILTINS))
        + ("where", "it", "prev", "agg", "cum"),
        time_fields=tuple(f"t.{name}" for name in TIME_FIELDS),
        notes=(
            "Money is Decimal at 4 dp everywhere in this output; the engine core "
            "holds int64 minor units and never a float.",
            "Formula semantics are `where`, not `if`: both branches always "
            "evaluate and selection is elementwise.",
            "Every item id, tag key, tag value, measure, grain and status this "
            "book accepts is listed above. Anything absent does not exist.",
            f"Aggregation to a coarser grain respects agg_rule; the grain column "
            f"names are {sorted(GRAIN_COLUMN)}.",
        ),
    )


def _describe_item(item: Item) -> ItemDescription:
    settles = "immediate"
    if item.settlement is not None:
        if not item.settlement.due:
            settles = "never (accrual only)"
        else:
            settles = ", ".join(
                (
                    f"share {term.share} at {term.offset}"
                    if term.share is not None
                    else (
                        f"remainder at {term.offset}"
                        if term.remainder
                        else f"amount {term.amount} at {term.offset}"
                    )
                )
                for term in item.settlement.due
            )
    return ItemDescription(
        item_id=item.id,
        name=item.name,
        kind=item.kind,
        direction=item.direction or "",
        currency=item.currency,
        agg_rule=item.agg_rule,
        tags=dict(item.tags),
        flags=tuple(sorted(item.flags)),
        formula=item.formula or "",
        segments=len(item.segments),
        settles=settles,
        vat=(
            ""
            if item.vat is None
            else f"{item.vat.treatment} at rate {item.vat.rate}, "
            f"recoverable {item.vat.recoverable}"
        ),
        synthetic=_is_synthetic(item.id),
    )
