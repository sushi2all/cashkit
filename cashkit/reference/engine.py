"""The reference engine — a naive ``Decimal`` oracle (PRD §5.2).

Correctness only; performance is irrelevant. Every value is a ``Decimal``
quantized to 4 decimal places at each declared rounding boundary, computed one
period and one item at a time. This is the artifact that makes every later
optimization safe, and it ships forever: the dual-engine gate re-proves on every
test run that the vectorized engine agrees with it byte-for-byte.

What is deliberately **shared** with the vectorized engine:

* the data model,
* the formula front-end (ADR-0001 — one language, one definition),
* the escalation factor table (ADR-0002 — both engines must consume the same
  exact factors or byte-equality is unattainable),
* the dependency graph and its condensation (structure, not arithmetic),
* the calendar's recurrence-date generator.

What is deliberately **duplicated**, because this is where silent numerical
error lives: all arithmetic and rounding, segment amount computation, settlement
splitting, and the evaluation of every formula node. The vectorized engine does
these as int64 column operations; this engine does them as scalar ``Decimal``
arithmetic. Agreement between two independent implementations of the canonical
rounding order (ADR-0003) is the whole point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction

import numpy as np

from cashkit.engine.calendars import (
    BusinessCalendar,
    PeriodIndex,
    add_duration,
    month_length,
    occurrence_dates,
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
    Param,
    Prev,
    TimeField,
    Unary,
    Where,
)
from cashkit.engine.expand import (
    FIXED,
    IMMEDIATE,
    INVALID,
    NEVER,
    SHARES,
    classify_settlement,
    escalation_boundary,
)
from cashkit.engine.facts import FactSet, augmented_items, resolve_facts
from cashkit.engine.graph import CompiledBook, CompiledItem, compile_book
from cashkit.engine.tax import TaxPlan
from cashkit.engine.vat import VatColumns, VatRule, is_output_side, rule_for
from cashkit.engine.numeric import (
    MAX_ESCALATION_EXPONENT,
    RoundingPolicy,
    escalation_factor,
    to_minor,
)
from cashkit.engine.result import RunResult
from cashkit.model import (
    Book,
    Diagnostic,
    DueTerm,
    Event,
    Item,
    ItemId,
    Segment,
    Settlement,
)
from cashkit.model.diagnostics import make_diagnostic

__all__ = ["run"]

QUANTUM = Decimal("0.0001")
ZERO = Decimal(0)
ONE = Decimal(1)

_DECIMAL_ROUNDING = {
    RoundingPolicy.HALF_UP: ROUND_HALF_UP,
    RoundingPolicy.HALF_EVEN: ROUND_HALF_EVEN,
}

#: Working precision for division. Provably enough to avoid double rounding:
#: a quotient of two 4 dp money values that lands exactly on a 4 dp half-way
#: boundary terminates in at most ~25 digits, and one that does not land on a
#: boundary sits at least 5e-23 away from one — far beyond this precision's error.
_DIVISION_PRECISION = 80


def _quantize(value: Decimal, policy: RoundingPolicy) -> Decimal:
    """Round to 4 dp under ``policy`` — the one declared rounding boundary."""
    return value.quantize(QUANTUM, rounding=_DECIMAL_ROUNDING[policy])


def _multiply(amount: Decimal, factor: Decimal, policy: RoundingPolicy) -> Decimal:
    """Multiply exactly, then round once. The context is sized to the operands so
    the product itself is never rounded — only the final 4 dp quantization is."""
    needed = len(amount.as_tuple().digits) + len(factor.as_tuple().digits) + 10
    with localcontext() as ctx:
        ctx.prec = max(60, needed)
        product = amount * factor
    return _quantize(product, policy)


def _divide(numerator: Decimal, denominator: Decimal, policy: RoundingPolicy) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = _DIVISION_PRECISION
        quotient = numerator / denominator
    return _quantize(quotient, policy)


# --------------------------------------------------------------------------- #
# Evaluator value kinds
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _Money:
    """A money value: a ``Decimal`` already quantized to 4 dp."""

    value: Decimal
    zero_div: bool = False


@dataclass(frozen=True)
class _Rate:
    """A dimensionless scalar held as an exact rational — params and literals.

    Rates stay exact until they meet money; that is what keeps a 22% VAT rate or
    a 3.1% escalation from drifting.
    """

    value: Fraction
    zero_div: bool = False


@dataclass(frozen=True)
class _Bool:
    """A mask value from a comparison or logical operation."""

    value: bool
    zero_div: bool = False


_Value = _Money | _Rate | _Bool


def _to_money(value: _Value, policy: RoundingPolicy) -> _Money:
    """Coerce any value to money, rounding a rate to 4 dp at this boundary."""
    if isinstance(value, _Money):
        return value
    if isinstance(value, _Bool):
        return _Money(ONE if value.value else ZERO, value.zero_div)
    return _Money(_quantize(_exact_decimal(value.value), policy), value.zero_div)


def _truth(value: _Value) -> bool:
    if isinstance(value, _Bool):
        return value.value
    if isinstance(value, _Money):
        return value.value != ZERO
    return value.value != 0


class _VatLedger:
    """One item's VAT columns as ``Decimal`` lists — the oracle's own storage."""

    def __init__(self, length: int) -> None:
        self.output_accrual = [ZERO] * length
        self.input_accrual = [ZERO] * length
        self.output_cash = [ZERO] * length
        self.input_cash = [ZERO] * length

    def to_columns(self) -> VatColumns:
        """Convert to the int64 form both engines are compared on."""
        return VatColumns(
            output_accrual=np.array(
                [to_minor(v) for v in self.output_accrual], dtype=np.int64
            ),
            input_accrual=np.array(
                [to_minor(v) for v in self.input_accrual], dtype=np.int64
            ),
            output_cash=np.array(
                [to_minor(v) for v in self.output_cash], dtype=np.int64
            ),
            input_cash=np.array([to_minor(v) for v in self.input_cash], dtype=np.int64),
        )


def _allocate(
    total: Decimal, legs: list[Decimal], policy: RoundingPolicy
) -> list[Decimal]:
    """Split one line's VAT across its settlement legs, in proportion.

    The last leg absorbs the residual, so the parts sum to the total exactly —
    the residual-absorption rule ADR-0003 already fixes for the share split, and
    the reason an invoice's VAT legs always add up to the VAT it states.
    """
    if not legs:
        return []
    if len(legs) == 1:
        return [total]
    denominator = sum(legs, start=ZERO)
    if denominator == ZERO:
        return [ZERO] * len(legs)
    parts: list[Decimal] = []
    running = ZERO
    for leg in legs[:-1]:
        part = _divide(_exact_product(total, leg), denominator, policy)
        running += part
        parts.append(part)
    parts.append(total - running)
    return parts


def _exact_product(left: Decimal, right: Decimal) -> Decimal:
    """Multiply without rounding: the context is sized to the operands."""
    needed = len(left.as_tuple().digits) + len(right.as_tuple().digits) + 10
    with localcontext() as ctx:
        ctx.prec = max(60, needed)
        return left * right


# --------------------------------------------------------------------------- #
# Settlement dates
# --------------------------------------------------------------------------- #


def _due_date(
    term: DueTerm,
    accrual_date: date,
    period_end_inclusive: date,
    calendar: BusinessCalendar,
) -> date:
    if term.basis == "accrual":
        base = accrual_date
    elif term.basis == "period_end":
        base = period_end_inclusive
    else:
        base = date(
            accrual_date.year,
            accrual_date.month,
            month_length(accrual_date.year, accrual_date.month),
        )
    return calendar.adjust(add_duration(base, term.offset), term.adjust)


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #


class _Reference:
    def __init__(
        self, book: Book, policy: RoundingPolicy, events: tuple[Event, ...] = ()
    ) -> None:
        self.policy = policy
        # The fact union precedes graph construction here for the same reason it
        # does in the vectorized engine: `agg()` selectors resolve to concrete
        # ids at graph-build time, so a synthetic carrier that appears later
        # would be invisible to every aggregate.
        self.factset: FactSet = resolve_facts(book, events)
        if self.factset.synthetic_items:
            book = book.model_copy(
                update={"items": augmented_items(book, self.factset)}
            )
        self.periods = PeriodIndex.build(
            book.horizon, book.base_grain, book.calendar.fiscal_year_start_month
        )
        self.compiled: CompiledBook = compile_book(book, self.periods)
        # `compile_book` injects the regimes' synthetic items (ADR-0005).
        book = self.compiled.book
        self.book = book
        self.calendar = BusinessCalendar.from_spec(book.calendar)
        self.length = len(self.periods)
        self.accrual: dict[ItemId, list[Decimal]] = {
            item_id: [ZERO] * self.length for item_id in book.items
        }
        self.cash: dict[ItemId, list[Decimal]] = {
            item_id: [ZERO] * self.length for item_id in book.items
        }
        self._rules: dict[ItemId, VatRule] = {}
        for item_id, item in book.items.items():
            if item.vat is None:
                continue
            rule, problem = rule_for(item, book.params)
            if problem is None:
                self._rules[item_id] = rule
        # Every item with a resolvable VatSpec reports columns, zero or not; an
        # item an event gives VAT to gains its ledger on the way past.
        self.vat: dict[ItemId, _VatLedger] = {
            item_id: _VatLedger(self.length) for item_id in self._rules
        }
        self.diagnostics: list[Diagnostic] = list(self.compiled.diagnostics)
        self.diagnostics.extend(self.factset.diagnostics)
        self._emitted: set[tuple[str, ItemId | None]] = set()
        self._settlement_kind: dict[ItemId, str] = {}
        self._cum_cache: dict[tuple[ItemId, str], list[Decimal]] = {}
        self._component_members: frozenset[ItemId] = frozenset()
        self._regimes: dict[
            str, tuple[list[Decimal], list[Decimal], list[Decimal]]
        ] = {}

    # -- diagnostics ------------------------------------------------------ #

    def _emit_once(self, code: str, item_id: ItemId | None, **details: object) -> None:
        """Emit a diagnostic at most once per (code, item). A monthly settlement
        clamp over a five-year horizon is one modelling fact, not sixty."""
        key = (code, item_id)
        if key in self._emitted:
            return
        self._emitted.add(key)
        self.diagnostics.append(make_diagnostic(code, item_id=item_id, **details))

    def _emit_built_once(self, diagnostic: Diagnostic) -> None:
        """Append an already-built diagnostic under the same deduplication."""
        key = (diagnostic.code, diagnostic.item_id)
        if key in self._emitted:
            return
        self._emitted.add(key)
        self.diagnostics.append(diagnostic)

    def _settlement_of(self, item: Item) -> str:
        kind, diagnostics = classify_settlement(item)
        self._settlement_kind[item.id] = kind
        for diagnostic in diagnostics:
            self._emit_built_once(diagnostic)
        return kind

    # -- columns ---------------------------------------------------------- #

    def _column(self, item_id: ItemId, measure: str) -> list[Decimal]:
        """A stock is a level, not a movement: both measures read its level."""
        if self.book.items[item_id].kind == "stock":
            return self.accrual[item_id]
        return self.accrual[item_id] if measure == "accrual" else self.cash[item_id]

    # -- generative expansion --------------------------------------------- #

    def expand(self) -> None:
        for item_id, compiled in sorted(self.compiled.items.items()):
            if compiled.broken or compiled.is_derived:
                continue
            if item_id in self.compiled.tax.nodes:
                continue  # a regime's columns come from its schedule
            kind = self._settlement_of(compiled.item)
            for segment in compiled.item.segments:
                self._expand_segment(compiled.item, segment, kind)
        self._apply_event_facts()

    def _apply_event_facts(self) -> None:
        """Union the ledger's facts into the same columns, one row at a time.

        Events are never suppressed by cutover: before it the ledger is the
        complete record and its rows are taken as-is (ADR-0004). An event dated
        outside the horizon is outside the model, cash legs included — the rule
        generative occurrences already follow (DECISIONS D-P2-03).
        """
        for fact in self.factset.facts:
            index = self.periods.index_of(fact.event.date)
            if index is None:
                continue
            carrier = fact.carrier
            kind, structural = classify_settlement(carrier)
            for diagnostic in structural:
                self._emit_built_once(diagnostic)
            amount = fact.event.amount
            self.accrual[fact.target][index] += amount
            self._settle(carrier, amount, fact.event.date, index, kind)

    def _expand_segment(self, item: Item, segment: Segment, settlement_kind: str) -> None:
        horizon = self.book.horizon
        if segment.amount.schedule is not None:
            # An explicit schedule *is* the occurrence series: every authored
            # (date, amount) pair is used exactly once (DECISIONS D-P2-02).
            occurrences = [
                (day, value)
                for day, value in segment.amount.schedule
                if horizon.start <= day < horizon.end
            ]
        else:
            assert segment.amount.constant is not None
            occurrences = [
                (day, segment.amount.constant)
                for day in occurrence_dates(
                    segment.recurrence,
                    segment.start,
                    segment.end,
                    horizon.start,
                    horizon.end,
                )
            ]

        for anchor, base in occurrences:
            accrual_date = self.calendar.adjust(
                anchor, segment.recurrence.business_day_adjust
            )
            index = self.periods.index_of(accrual_date)
            if index is None:
                continue
            # ADR-0004: before cutover the ledger is the complete record, so
            # generative expansion is suppressed for every item.
            if accrual_date < self.book.cutover:
                continue
            amount = self._escalate(segment, anchor, base)
            if segment.probability != ONE:
                amount = _multiply(amount, segment.probability, self.policy)
            self.accrual[item.id][index] += amount
            self._settle(item, amount, accrual_date, index, settlement_kind)

    def _escalate(self, segment: Segment, anchor: date, base: Decimal) -> Decimal:
        escalation = segment.escalation
        if escalation is None:
            return base
        rate = escalation.rate
        if isinstance(rate, str):
            rate = self.book.params[rate]
        steps = _escalation_steps(escalation.anchor, escalation.every_years, segment.start, anchor)
        if steps == 0:
            return base
        return _multiply(base, escalation_factor(rate, steps), self.policy)

    def _settle(
        self,
        item: Item,
        accrued: Decimal,
        accrual_date: date,
        accrual_index: int,
        kind: str,
    ) -> None:
        """Turn one accrual into cash legs, in the canonical order (ADR-0003):
        the split, then withholding, then VAT — which closes the chain."""
        rule = self._rules.get(item.id) if item.vat is not None else None
        if rule is not None and item.vat != self.book.items[item.id].vat:
            # A ledger row may override its item's VatSpec (PRD §4.3).
            rule, _ = rule_for(item, self.book.params)
        cash_vat, output_vat, input_vat = self._vat_of(rule, accrued)
        active = rule is not None and not rule.inert
        if active:
            ledger = self._ledger(item.id)
            ledger.output_accrual[accrual_index] += output_vat
            ledger.input_accrual[accrual_index] += input_vat

        if kind in (NEVER, INVALID):
            return
        if kind == IMMEDIATE:
            self.cash[item.id][accrual_index] += accrued + cash_vat
            if active:
                ledger = self._ledger(item.id)
                ledger.output_cash[accrual_index] += output_vat
                ledger.input_cash[accrual_index] += input_vat
            return
        assert item.settlement is not None
        legs = self._split(item, accrued, item.settlement, kind)
        period_end_inclusive = self.periods.ends[accrual_index] - timedelta(days=1)
        gross = [leg for _, leg in legs]
        cash_parts = _allocate(cash_vat, gross, self.policy)
        output_parts = _allocate(output_vat, gross, self.policy)
        input_parts = _allocate(input_vat, gross, self.policy)
        for position, (term, leg) in enumerate(legs):
            net = leg
            if term.withholding != ZERO:
                net = leg - _multiply(leg, term.withholding, self.policy)
            due = _due_date(term, accrual_date, period_end_inclusive, self.calendar)
            index = self.periods.index_of(due)
            if index is None:
                continue
            self.cash[item.id][index] += net + cash_parts[position]
            if active:
                ledger = self._ledger(item.id)
                ledger.output_cash[index] += output_parts[position]
                ledger.input_cash[index] += input_parts[position]

    def _ledger(self, item_id: ItemId) -> "_VatLedger":
        """This item's VAT ledger, created the first time it is needed."""
        ledger = self.vat.get(item_id)
        if ledger is None:
            ledger = _VatLedger(self.length)
            self.vat[item_id] = ledger
        return ledger

    def _vat_of(
        self, rule: VatRule | None, accrued: Decimal
    ) -> tuple[Decimal, Decimal, Decimal]:
        """Decompose one line into ``(cash_vat, output, input)`` — position 5 of
        the canonical rounding order, computed on the taxable amount and not on
        what is left after withholding."""
        if rule is None or rule.inert:
            return ZERO, ZERO, ZERO
        line = _multiply(accrued, rule.rate, self.policy)
        outputs = is_output_side(rule, accrued >= ZERO)
        if rule.recoverable == ONE:
            deductible = line
        elif rule.recoverable == ZERO:
            deductible = ZERO
        else:
            deductible = _multiply(line, rule.recoverable, self.policy)
        input_vat = ZERO if outputs else deductible
        if rule.treatment.self_assess:
            return ZERO, (ZERO if outputs else -line), input_vat
        cash_vat = line if rule.treatment.collects_cash else ZERO
        return cash_vat, (line if outputs else ZERO), input_vat

    def _split(
        self, item: Item, accrued: Decimal, settlement: Settlement, kind: str
    ) -> list[tuple[DueTerm, Decimal]]:
        terms = settlement.due
        if kind == SHARES:
            legs: list[tuple[DueTerm, Decimal]] = []
            running = ZERO
            for position, term in enumerate(terms):
                assert term.share is not None
                if position == len(terms) - 1:
                    # The last term absorbs the rounding residual, so the legs
                    # sum to the accrued amount exactly (ADR-0003).
                    leg = accrued - running
                else:
                    leg = _multiply(accrued, term.share, self.policy)
                    running += leg
                legs.append((term, leg))
            return legs

        fixed = [term for term in terms if term.amount is not None]
        if accrued < ZERO:
            # Fixed legs never flip sign: a credit note routes entirely through
            # the remainder (PRD §4.4, CK-W002).
            if fixed:
                self._emit_once("CK-W002", item.id, field="settlement.due")
            return [
                (term, accrued if term.remainder else ZERO)
                for term in terms
            ]
        fixed_total = sum((term.amount for term in fixed), start=ZERO)
        leftover = accrued - fixed_total
        if leftover < ZERO:
            self._emit_once(
                "CK-W001",
                item.id,
                field="settlement.due",
                fixed_total=fixed_total,
                accrued=accrued,
            )
            leftover = ZERO
        return [
            (term, leftover if term.remainder else term.amount)
            for term in terms
        ]

    # -- derived evaluation ----------------------------------------------- #

    def evaluate(self) -> None:
        for component in self.compiled.components:
            self._cum_cache = {}
            self._component_members = frozenset(component.members)
            if component.trivial:
                self._evaluate_trivial(component.members[0])
            else:
                self._evaluate_fold(component.members)

    def _evaluate_trivial(self, item_id: ItemId) -> None:
        if item_id in self.compiled.tax.nodes:
            self._evaluate_tax(item_id)
            return
        compiled = self.compiled.items[item_id]
        if compiled.broken or not compiled.is_derived or compiled.expr is None:
            return
        kind = self._settlement_of(compiled.item)
        for period in range(self.length):
            self._evaluate_cell(compiled, period)
        if compiled.item.kind != "stock":
            for period in range(self.length):
                self._settle(
                    compiled.item,
                    self.accrual[item_id][period],
                    self.periods.starts[period],
                    period,
                    kind,
                )

    def _evaluate_fold(self, members: tuple[ItemId, ...]) -> None:
        for item_id in members:
            compiled = self.compiled.items[item_id]
            if compiled.is_derived and not compiled.broken:
                self._settlement_of(compiled.item)
        for period in range(self.length):
            for item_id in members:
                compiled = self.compiled.items[item_id]
                if compiled.broken or not compiled.is_derived or compiled.expr is None:
                    continue
                self._evaluate_cell(compiled, period)
                if compiled.item.kind != "stock":
                    self._settle(
                        compiled.item,
                        self.accrual[item_id][period],
                        self.periods.starts[period],
                        period,
                        self._settlement_kind[item_id],
                    )

    # -- tax regimes (ADR-0005) ------------------------------------------- #

    def _evaluate_tax(self, item_id: ItemId) -> None:
        """Fill a regime's synthetic columns, one return period at a time.

        The liability and the credit are one recurrence — this period's credit
        depends on the last one's — so both are computed when the credit comes
        up, which the liability depends on and therefore follows.
        """
        if self.compiled.items[item_id].broken:
            # The regime was caught in a cycle and refused; a zero column plus a
            # loud error beats a plausible-looking half-answer.
            return
        plan = self.compiled.tax.plan_for(item_id)
        if plan is None:  # pragma: no cover - guarded by the caller
            return
        cached = self._regimes.get(plan.regime.id)
        if cached is None:
            cached = self._fold_regime(plan)
            self._regimes[plan.regime.id] = cached
        liability_accrual, liability_cash, credit_level = cached
        if item_id == plan.credit:
            self.accrual[item_id] = list(credit_level)
        else:
            self.accrual[item_id] = list(liability_accrual)
            self.cash[item_id] = list(liability_cash)

    def _fold_regime(
        self, plan: TaxPlan
    ) -> tuple[list[Decimal], list[Decimal], list[Decimal]]:
        regime = plan.regime
        accrual_basis = regime.measure == "accrual"
        net = [ZERO] * self.length
        for base_id in plan.base:
            ledger = self.vat.get(base_id)
            if ledger is None:
                continue
            outputs = ledger.output_accrual if accrual_basis else ledger.output_cash
            inputs = ledger.input_accrual if accrual_basis else ledger.input_cash
            for period in range(self.length):
                net[period] += outputs[period] + inputs[period]

        liability_accrual = [ZERO] * self.length
        liability_cash = [ZERO] * self.length
        credit_level = [ZERO] * self.length
        credit = ZERO
        for period in plan.periods:
            if not period.closes:
                continue
            due = sum(net[period.lo : period.hi], start=ZERO)
            if due >= ZERO:
                applied = min(credit, due)
                payable = due - applied
                credit -= applied
                if payable != ZERO and regime.surcharge != ZERO:
                    payable += _multiply(payable, regime.surcharge, self.policy)
                movement = -payable
            else:
                # Input exceeded output: a credit stock, never a negative payment.
                credit += -due
                movement = ZERO
            if (
                regime.credit_handling == "refund_annual"
                and period.end.month == regime.annual_adjustment_month
                and credit != ZERO
            ):
                movement += credit
                credit = ZERO
            if movement != ZERO:
                liability_accrual[period.recognition] += movement
                if period.payment >= 0:
                    liability_cash[period.payment] += movement
            for index in range(period.recognition, self.length):
                credit_level[index] = credit
        return liability_accrual, liability_cash, credit_level

    def _evaluate_cell(self, compiled: CompiledItem, period: int) -> None:
        assert compiled.expr is not None
        result = self._eval(compiled.expr, period, compiled.id)
        money = _to_money(result, self.policy)
        self.accrual[compiled.id][period] = money.value
        if money.zero_div:
            self._emit_once(
                "CK-W005",
                compiled.id,
                field="formula",
                period=self.periods.starts[period].isoformat(),
            )

    # -- expression evaluation -------------------------------------------- #

    def _eval(self, expr: Expr, period: int, owner: ItemId) -> _Value:
        if isinstance(expr, Literal):
            return _Rate(Fraction(expr.value))
        if isinstance(expr, Param):
            return _Rate(Fraction(self._param(expr.key)))
        if isinstance(expr, TimeField):
            return self._time_field(expr.name, period)
        if isinstance(expr, ItemRef):
            return _Money(self._column(expr.item_id, expr.measure)[period])
        if isinstance(expr, Prev):
            if period - expr.lag < 0:
                if isinstance(expr.init, Param):
                    return _to_money(_Rate(Fraction(self._param(expr.init.key))), self.policy)
                return _to_money(_Rate(Fraction(expr.init.value)), self.policy)
            return _Money(self._column(expr.item_id, expr.measure)[period - expr.lag])
        if isinstance(expr, Agg):
            total = ZERO
            for member in expr.items or ():
                total += self._column(member, expr.measure)[period]
            return _Money(total)
        if isinstance(expr, Cum):
            return _Money(self._cumulative(expr.item_id, expr.measure, period))
        if isinstance(expr, Unary):
            return self._unary(expr, period, owner)
        if isinstance(expr, Binary):
            return self._binary(expr, period, owner)
        if isinstance(expr, Compare):
            return self._compare(expr, period, owner)
        if isinstance(expr, Logical):
            return self._logical(expr, period, owner)
        if isinstance(expr, Where):
            condition = self._eval(expr.cond, period, owner)
            chosen_then = self._eval(expr.then, period, owner)
            chosen_else = self._eval(expr.otherwise, period, owner)
            # Both branches always evaluate (D8); selection is elementwise.
            taken = chosen_then if _truth(condition) else chosen_else
            # `where` is a masked column operation, so its result is always
            # money: a per-period exact rate has no column representation
            # (DECISIONS D-P2-20).
            return _with_flag(
                _to_money(taken, self.policy), condition.zero_div or taken.zero_div
            )
        assert isinstance(expr, Builtin)
        return self._builtin(expr, period, owner)

    def _param(self, key: str) -> Decimal:
        if key in self.book.params:
            return self.book.params[key]
        return self.book.opening_balance

    def _time_field(self, name: str, period: int) -> _Value:
        start = self.periods.starts[period]
        if name == "index":
            return _Money(Decimal(period).quantize(QUANTUM))
        if name == "month":
            return _Money(Decimal(start.month).quantize(QUANTUM))
        if name == "is_quarter_end":
            return _Bool(self.periods.is_quarter_end(self.periods.ends[period] - timedelta(days=1)))
        return _Bool(self.calendar.is_business_day(start))

    def _cumulative(self, item_id: ItemId, measure: str, period: int) -> Decimal:
        column = self._column(item_id, measure)
        if item_id in self._component_members:
            # Inside a feedback component the column is still filling in, so the
            # running total is summed fresh each period — naive by design.
            return sum(column[: period + 1], start=ZERO)
        key = (item_id, measure)
        prefix = self._cum_cache.get(key)
        if prefix is None:
            prefix = []
            running = ZERO
            for value in column:
                running += value
                prefix.append(running)
            self._cum_cache[key] = prefix
        return prefix[period]

    def _unary(self, expr: Unary, period: int, owner: ItemId) -> _Value:
        operand = self._eval(expr.operand, period, owner)
        if expr.op == "not":
            return _Bool(not _truth(operand), operand.zero_div)
        if expr.op == "+":
            return operand
        if isinstance(operand, _Rate):
            return _Rate(-operand.value, operand.zero_div)
        money = _to_money(operand, self.policy)
        return _Money(-money.value, money.zero_div)

    def _binary(self, expr: Binary, period: int, owner: ItemId) -> _Value:
        left = self._eval(expr.left, period, owner)
        right = self._eval(expr.right, period, owner)
        flag = left.zero_div or right.zero_div
        both_rate = isinstance(left, _Rate) and isinstance(right, _Rate)

        if expr.op in ("+", "-"):
            if both_rate:
                total = left.value + right.value if expr.op == "+" else left.value - right.value
                return _Rate(total, flag)
            lhs = _to_money(left, self.policy).value
            rhs = _to_money(right, self.policy).value
            return _Money(lhs + rhs if expr.op == "+" else lhs - rhs, flag)

        if expr.op == "*":
            if both_rate:
                return _Rate(left.value * right.value, flag)
            if isinstance(left, _Rate):
                money, rate = _to_money(right, self.policy), left
            elif isinstance(right, _Rate):
                money, rate = _to_money(left, self.policy), right
            else:
                lhs = _to_money(left, self.policy).value
                rhs = _to_money(right, self.policy).value
                return _Money(_multiply(lhs, rhs, self.policy), flag)
            return _Money(
                _multiply(money.value, _exact_decimal(rate.value), self.policy), flag
            )

        assert expr.op == "/"
        if both_rate:
            if right.value == 0:
                return _Rate(Fraction(0), True)
            return _Rate(left.value / right.value, flag)
        numerator = _to_money(left, self.policy).value
        denominator = (
            _exact_decimal(right.value)
            if isinstance(right, _Rate)
            else _to_money(right, self.policy).value
        )
        if denominator == ZERO:
            # Masked-safe division: both `where` branches always evaluate, so
            # a/0 executes by design and yields 0 (PRD §5.4, CK-W005).
            return _Money(ZERO, True)
        return _Money(_divide(numerator, denominator, self.policy), flag)

    def _compare(self, expr: Compare, period: int, owner: ItemId) -> _Value:
        left = self._eval(expr.left, period, owner)
        right = self._eval(expr.right, period, owner)
        flag = left.zero_div or right.zero_div
        if isinstance(left, _Rate) and isinstance(right, _Rate):
            lhs: object = left.value
            rhs: object = right.value
        else:
            lhs = _to_money(left, self.policy).value
            rhs = _to_money(right, self.policy).value
        outcome = {
            "==": lhs == rhs,
            "!=": lhs != rhs,
            "<": lhs < rhs,  # type: ignore[operator]
            "<=": lhs <= rhs,  # type: ignore[operator]
            ">": lhs > rhs,  # type: ignore[operator]
            ">=": lhs >= rhs,  # type: ignore[operator]
        }[expr.op]
        return _Bool(bool(outcome), flag)

    def _logical(self, expr: Logical, period: int, owner: ItemId) -> _Value:
        values = [self._eval(operand, period, owner) for operand in expr.operands]
        flag = any(value.zero_div for value in values)
        truths = [_truth(value) for value in values]
        # Never short-circuit: every operand above is already evaluated.
        return _Bool(all(truths) if expr.op == "and" else any(truths), flag)

    def _builtin(self, expr: Builtin, period: int, owner: ItemId) -> _Value:
        args = [self._eval(arg, period, owner) for arg in expr.args]
        flag = any(arg.zero_div for arg in args)
        if expr.name == "abs_":
            money = _to_money(args[0], self.policy)
            return _Money(money.value.copy_abs(), flag)
        if expr.name == "round_":
            money = _to_money(args[0], self.policy)
            digits = int(_to_money(args[1], self.policy).value)
            quantum = Decimal(1).scaleb(-digits)
            rounded = money.value.quantize(quantum, rounding=_DECIMAL_ROUNDING[self.policy])
            return _Money(_quantize(rounded, self.policy), flag)
        if expr.name == "clip":
            value = _to_money(args[0], self.policy).value
            low = _to_money(args[1], self.policy).value
            high = _to_money(args[2], self.policy).value
            return _Money(min(max(value, low), high), flag)
        values = [_to_money(arg, self.policy).value for arg in args]
        return _Money(min(values) if expr.name == "min" else max(values), flag)

    # -- output ----------------------------------------------------------- #

    def finish(self) -> RunResult:
        return RunResult(
            book_id=self.book.id,
            periods=self.periods,
            accrual={
                item_id: np.array([to_minor(v) for v in column], dtype=np.int64)
                for item_id, column in self.accrual.items()
            },
            cash={
                item_id: np.array([to_minor(v) for v in column], dtype=np.int64)
                for item_id, column in self.cash.items()
            },
            diagnostics=tuple(self.diagnostics),
            currencies={item_id: item.currency for item_id, item in self.book.items.items()},
            vat={
                item_id: ledger.to_columns()
                for item_id, ledger in sorted(self.vat.items())
            },
        )


def _with_flag(value: _Value, flag: bool) -> _Value:
    if isinstance(value, _Money):
        return _Money(value.value, flag)
    if isinstance(value, _Rate):
        return _Rate(value.value, flag)
    return _Bool(value.value, flag)


def _exact_decimal(value: Fraction) -> Decimal:
    with localcontext() as ctx:
        ctx.prec = _DIVISION_PRECISION
        return Decimal(value.numerator) / Decimal(value.denominator)


def _escalation_steps(anchor: str, every_years: int, segment_start: date, occurrence: date) -> int:
    """Completed escalation steps at ``occurrence``, counted naively.

    Steps are counted by walking the anniversary boundaries defined in
    :func:`~cashkit.engine.expand.escalation_boundary` — the same definition the
    vectorized engine resolves with a binary search. Keyed to the *unadjusted*
    occurrence date, so a business-day roll across New Year cannot change an
    amount.
    """
    steps = 0
    while steps < MAX_ESCALATION_EXPONENT:
        boundary = escalation_boundary(anchor, every_years, segment_start, steps + 1)
        if occurrence < boundary:
            break
        steps += 1
    return steps


def run(
    book: Book,
    *,
    policy: RoundingPolicy = RoundingPolicy.HALF_UP,
    events: tuple[Event, ...] | list[Event] = (),
) -> RunResult:
    """Evaluate ``book`` the slow, obvious way.

    Returns a :class:`~cashkit.engine.result.RunResult` whose columns are int64
    minor units at 4 dp. Diagnostics cover formula rejection (``CK-E003``),
    unresolvable references (``CK-E001``), illegal cycles (``CK-E002``), unknown
    params (``CK-E008``), cross-currency aggregation (``CK-E020``), settlement
    structure (``CK-E004``/``CK-E005``), clamped remainders (``CK-W001``),
    credit notes on fixed terms (``CK-W002``) and masked division by zero
    (``CK-W005``). Never raises on book content.
    """
    engine = _Reference(book, policy, tuple(events))
    engine.expand()
    engine.evaluate()
    return engine.finish()
