"""Tax regimes as synthetic graph items (PRD §4.5, ADR-0005).

A ``TaxRegime`` is *a rate at the line, a schedule at the entity*. The rate lives
on each item's ``VatSpec`` and is applied in :mod:`cashkit.engine.vat`; the
schedule lives here. Each regime materializes as two synthetic items injected
into the dependency graph **before condensation**:

* ``_tax:<id>:liability`` — a flow: the periodic payment, recognised at the
  regime period's end and moving cash at ``period_end + payment_offset``;
* ``_tax:<id>:credit`` — a stock: the carried VAT credit.

Injecting them into the graph rather than bolting a post-pass onto the end of
evaluation is the whole point of ADR-0005: the cash fold then sees tax payments
like any other flow, overdraft interest included, and `trace()` explains a tax
number with the same machinery as everything else. Their ids are outside the
``ItemId`` grammar on purpose — no authored book can collide with them.

**Credit carry-forward is a stock, never a negative payment.** Input exceeding
output in a period is an asset that offsets future liability; booking it as a
cash inflow would overstate cash in exactly the year — an investment year — when
the forecast matters most.

**Tax point.** ``measure="accrual"`` (the Italian default) recognises VAT on the
invoice date: with 60-day customer terms you pay the state on 16 March for an
invoice that settles in May, and that working-capital hole is precisely what a
cash forecast exists to surface. ``measure="cash"`` (``IVA per cassa``)
recognises it as the cash legs land.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

import numpy as np

from cashkit.model import Book, Diagnostic, Item, ItemId, TaxRegime
from cashkit.model.diagnostics import make_diagnostic

from .calendars import PeriodIndex, add_duration, add_months
from .formula import parse_selector
from .numeric import RoundingPolicy, mul_ratio, ratio_of
from .vat import VatColumns

__all__ = [
    "SYNTHETIC_TAX_PREFIX",
    "RegimePeriod",
    "TaxPlan",
    "TaxPlanning",
    "credit_id",
    "evaluate_regime",
    "liability_id",
    "plan_regimes",
    "regime_periods",
    "tax_diagnostics",
]

#: Prefix of every synthetic tax item. Outside the ``ItemId`` grammar, which
#: requires a leading lowercase letter, so collision with an authored id is
#: structurally impossible.
SYNTHETIC_TAX_PREFIX = "_tax:"

#: Months per regime period.
_MONTHS_PER_PERIODICITY = {"monthly": 1, "quarterly": 3, "annual": 12}


def liability_id(regime_id: str) -> ItemId:
    """The synthetic flow item carrying a regime's payments. No diagnostics."""
    return f"{SYNTHETIC_TAX_PREFIX}{regime_id}:liability"


def credit_id(regime_id: str) -> ItemId:
    """The synthetic stock item carrying a regime's credit. No diagnostics."""
    return f"{SYNTHETIC_TAX_PREFIX}{regime_id}:credit"


# --------------------------------------------------------------------------- #
# The schedule
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegimePeriod:
    """One return period, resolved against the book's base-grain periods."""

    start: date
    #: Last day of the period, inclusive — the ``period_end`` the payment offset
    #: is measured from.
    end: date
    #: Half-open slice of base-grain period indices this return covers.
    lo: int
    hi: int
    #: Base-grain period the liability is recognised in, or ``-1`` when the
    #: return period does not close inside the horizon.
    recognition: int
    #: Base-grain period the payment moves cash in, or ``-1`` when it falls
    #: beyond the horizon.
    payment: int

    @property
    def closes(self) -> bool:
        """True when this return closes inside the horizon. No diagnostics."""
        return self.recognition >= 0


def _block_start(day: date, months: int, fiscal_start_month: int) -> date:
    """The start of the return period containing ``day``.

    Blocks are phased on ``CalendarSpec.fiscal_year_start_month``, like every
    other quarter in the system (DECISIONS D-P2-07): with the default fiscal
    year starting in January these are the calendar quarters an Italian entity
    files on, and an entity with a July fiscal year gets its own.
    """
    total = day.year * 12 + (day.month - 1)
    anchor = fiscal_start_month - 1
    offset = (total - anchor) % months
    aligned = total - offset
    return date(aligned // 12, aligned % 12 + 1, 1)


def regime_periods(regime: TaxRegime, periods: PeriodIndex) -> tuple[RegimePeriod, ...]:
    """Cut the horizon into ``regime``'s return periods.

    A return period that does not *close* inside the horizon recognises nothing:
    the return is not due yet, so inventing a payment for it would put money in
    the forecast that nobody owes. A period that closes inside the horizon but
    opened before it accumulates only what the horizon contains, consistent with
    the pre-horizon world being represented by ``opening_balance`` (D-P2-03).

    Returns the periods in order; produces no diagnostics.
    """
    months = _MONTHS_PER_PERIODICITY[regime.periodicity]
    horizon_start = periods.starts[0]
    horizon_end = periods.ends[-1]
    starts = periods.start_ordinals

    out: list[RegimePeriod] = []
    cursor = _block_start(horizon_start, months, periods.fiscal_year_start_month)
    while cursor < horizon_end:
        stop = add_months(cursor, months)
        end_inclusive = stop - timedelta(days=1)
        lo = int(np.searchsorted(starts, cursor.toordinal(), side="left"))
        hi = int(np.searchsorted(starts, stop.toordinal(), side="left"))
        recognition = periods.index_of(end_inclusive)
        payment = periods.index_of(add_duration(end_inclusive, regime.payment_offset))
        out.append(
            RegimePeriod(
                start=cursor,
                end=end_inclusive,
                lo=lo,
                hi=hi,
                recognition=-1 if recognition is None else recognition,
                payment=-1 if payment is None else payment,
            )
        )
        cursor = stop
    return tuple(out)


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TaxPlan:
    """One regime, ready to evaluate: its base, its schedule, its two items."""

    regime: TaxRegime
    base: tuple[ItemId, ...]
    periods: tuple[RegimePeriod, ...]

    @property
    def liability(self) -> ItemId:
        """Id of the liability flow item. No diagnostics."""
        return liability_id(self.regime.id)

    @property
    def credit(self) -> ItemId:
        """Id of the credit stock item. No diagnostics."""
        return credit_id(self.regime.id)


@dataclass(frozen=True)
class TaxPlanning:
    """Every regime's plan, plus the synthetic items to inject into the graph."""

    plans: dict[str, TaxPlan] = field(default_factory=dict)
    items: dict[ItemId, Item] = field(default_factory=dict)
    #: Synthetic item id -> ``(regime_id, role)`` where role is
    #: ``"liability"`` or ``"credit"``.
    nodes: dict[ItemId, tuple[str, str]] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()

    def plan_for(self, item_id: ItemId) -> TaxPlan | None:
        """The plan owning a synthetic item, or ``None``. No diagnostics."""
        entry = self.nodes.get(item_id)
        return None if entry is None else self.plans[entry[0]]


def _synthetic_item(item_id: ItemId, regime: TaxRegime, role: str, currency: str) -> Item:
    """Build a synthetic tax item.

    ``model_construct`` on purpose (DECISIONS D-P5-10): the id is deliberately
    outside the authored grammar, and every field here comes from an
    already-validated ``TaxRegime``.

    Tagged ``cat:tax`` — the convention PRD §9.5 already asks manual tax items to
    follow — so ``agg(tag="cat:tax")`` is the single way to pull every tax
    outflow, native or manual, into a cash balance.
    """
    return Item.model_construct(
        id=item_id,
        name=f"{regime.id} {role}",
        kind="stock" if role == "credit" else "flow",
        direction=None,
        tags={"cat": "tax", "regime": regime.id},
        flags=set(),
        currency=currency,
        segments=[],
        formula=None,
        settlement=None,
        vat=None,
        agg_rule="last" if role == "credit" else "sum",
    )


def plan_regimes(book: Book, periods: PeriodIndex) -> TaxPlanning:
    """Resolve every regime's base and schedule, and build its graph items.

    ``accumulates`` empty means "every item carrying a ``VatSpec``" — the VAT
    default of ADR-0005; anything else is a §5.4 tag selector. Resolution
    happens here, at graph-build time, for the same reason ``agg()`` does: the
    DAG must be static for the whole run.

    Returns a :class:`TaxPlanning`. Diagnostics: ``CK-E019`` for a regime whose
    selector matches nothing, whose base spans currencies, or which asks for an
    annual refund without naming the month. A regime that produces a diagnostic
    is dropped entirely rather than evaluated with a guessed configuration.
    """
    planning = TaxPlanning()
    diagnostics: list[Diagnostic] = []

    for regime in book.tax_regimes:
        problem = _configuration_problem(regime)
        if problem is not None:
            diagnostics.append(problem)
            continue
        base, problem = _resolve_base(book, regime)
        if problem is not None:
            diagnostics.append(problem)
            continue
        currencies = {book.items[item_id].currency for item_id in base}
        if len(currencies) > 1:
            diagnostics.append(
                make_diagnostic(
                    "CK-E020",
                    field=f"tax_regimes[{regime.id}].accumulates",
                    currencies=", ".join(sorted(currencies)),
                )
            )
            continue
        currency = currencies.pop() if currencies else "EUR"
        plan = TaxPlan(regime=regime, base=base, periods=regime_periods(regime, periods))
        planning.plans[regime.id] = plan
        for role, item_id in (("credit", plan.credit), ("liability", plan.liability)):
            planning.items[item_id] = _synthetic_item(item_id, regime, role, currency)
            planning.nodes[item_id] = (regime.id, role)

    return TaxPlanning(
        plans=planning.plans,
        items=planning.items,
        nodes=planning.nodes,
        diagnostics=tuple(diagnostics),
    )


def _configuration_problem(regime: TaxRegime) -> Diagnostic | None:
    if regime.credit_handling == "refund_annual" and regime.annual_adjustment_month is None:
        return make_diagnostic(
            "CK-E019",
            field=f"tax_regimes[{regime.id}].annual_adjustment_month",
            regime_id=regime.id,
            reason=(
                "credit_handling='refund_annual' needs annual_adjustment_month "
                "to say when the refund is claimed"
            ),
        )
    return None


def _resolve_base(book: Book, regime: TaxRegime) -> tuple[tuple[ItemId, ...], Diagnostic | None]:
    if not regime.accumulates.strip():
        base = tuple(
            sorted(
                item_id
                for item_id, item in book.items.items()
                if item.vat is not None and not item_id.startswith(SYNTHETIC_TAX_PREFIX)
            )
        )
        if not base:
            return (), make_diagnostic(
                "CK-E019",
                field=f"tax_regimes[{regime.id}].accumulates",
                regime_id=regime.id,
                reason=(
                    "the default base is every item carrying a VatSpec, and this "
                    "book has none"
                ),
            )
        return base, None

    selector, reason = parse_selector(regime.accumulates)
    if selector is None:
        return (), make_diagnostic(
            "CK-E019",
            field=f"tax_regimes[{regime.id}].accumulates",
            regime_id=regime.id,
            reason=reason or "unparseable selector",
        )
    base = tuple(
        sorted(
            item_id
            for item_id, item in book.items.items()
            if not item_id.startswith(SYNTHETIC_TAX_PREFIX)
            and selector.matches(item.tags, item.flags)
        )
    )
    if not base:
        return (), make_diagnostic(
            "CK-E019",
            field=f"tax_regimes[{regime.id}].accumulates",
            regime_id=regime.id,
            reason=f"selector {regime.accumulates!r} matches no item",
        )
    return base, None


# --------------------------------------------------------------------------- #
# Evaluation (vectorized side; the reference engine duplicates the arithmetic)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RegimeResult:
    """A regime's two columns, in int64 minor units."""

    liability_accrual: np.ndarray
    liability_cash: np.ndarray
    credit_level: np.ndarray


def net_by_period(
    plan: TaxPlan, vat: dict[ItemId, VatColumns], length: int
) -> np.ndarray:
    """Sum the base's VAT into one net column, on the regime's tax point.

    Returns an int64 column: positive is VAT owed, negative is VAT reclaimable.
    No diagnostics.
    """
    total = np.zeros(length, dtype=np.int64)
    accrual_basis = plan.regime.measure == "accrual"
    for item_id in plan.base:
        columns = vat.get(item_id)
        if columns is None:
            continue
        # In place: a base of forty items would otherwise allocate eighty
        # horizon-length arrays per run, which the delta budget notices.
        if accrual_basis:
            total += columns.output_accrual
            total += columns.input_accrual
        else:
            total += columns.output_cash
            total += columns.input_cash
    return total


def evaluate_regime(
    plan: TaxPlan,
    vat: dict[ItemId, VatColumns],
    length: int,
    policy: RoundingPolicy,
) -> RegimeResult:
    """Net each return period, carry the credit, and schedule the payment.

    The recurrence is inherently sequential — this period's credit depends on
    the last one's — but it iterates over return periods, of which a five-year
    day-grain book has twenty, not over the 1,826 base-grain periods.

    Returns a :class:`RegimeResult`. A payment is negative (an outflow) and an
    annual refund positive; the credit level is a positive stock. Produces no
    diagnostics.
    """
    regime = plan.regime
    net = net_by_period(plan, vat, length)
    liability_accrual = np.zeros(length, dtype=np.int64)
    liability_cash = np.zeros(length, dtype=np.int64)
    credit_level = np.zeros(length, dtype=np.int64)

    surcharge_ratio = ratio_of(regime.surcharge) if regime.surcharge else None
    credit = 0
    for period in plan.periods:
        if not period.closes:
            continue
        due = int(net[period.lo : period.hi].sum())
        if due >= 0:
            applied = min(credit, due)
            payable = due - applied
            credit -= applied
            if payable and surcharge_ratio is not None:
                payable += mul_ratio(payable, *surcharge_ratio, policy)
            movement = -payable
        else:
            # Input exceeded output: a credit stock, never a negative payment.
            credit += -due
            movement = 0
        if (
            regime.credit_handling == "refund_annual"
            and period.end.month == regime.annual_adjustment_month
            and credit
        ):
            movement += credit
            credit = 0
        if movement:
            liability_accrual[period.recognition] += movement
            if period.payment >= 0:
                liability_cash[period.payment] += movement
        credit_level[period.recognition :] = credit
    return RegimeResult(
        liability_accrual=liability_accrual,
        liability_cash=liability_cash,
        credit_level=credit_level,
    )


# --------------------------------------------------------------------------- #
# Book-level tax diagnostics (PRD §9.5, ADR-0005)
# --------------------------------------------------------------------------- #


def tax_diagnostics(book: Book) -> tuple[Diagnostic, ...]:
    """The tax warnings ``validate()`` owes the user.

    ``CK-W004``: withholding reduces one cash leg only. The counter-leg — the
    F24 remittance when you are the payer, the tax credit when your client
    withholds — is deliberately not generated (ADR-0005), so its absence is
    surfaced loudly rather than silently understating cash.

    ``CK-I001``: a book with a ``TaxRegime`` but no non-VAT ``cat:tax`` items,
    on the reasonable assumption that a real entity owes more than VAT —
    IRES/IRAP advances, INPS, TFR — none of which this engine models (§7.2).

    Returns the diagnostics; raises nothing. Phase 10's ``validate()`` folds
    these into the full catalogue sweep.
    """
    out: list[Diagnostic] = []
    withholding_items = sorted(
        item_id
        for item_id, item in book.items.items()
        if item.settlement is not None
        and any(term.withholding != Decimal(0) for term in item.settlement.due)
    )
    tax_items = {
        item_id
        for item_id, item in book.items.items()
        if item.tags.get("cat") == "tax"
    }
    if withholding_items and not tax_items:
        out.append(make_diagnostic("CK-W004", item_id=withholding_items[0]))
    if book.tax_regimes and not tax_items:
        out.append(make_diagnostic("CK-I001"))
    return tuple(out)
