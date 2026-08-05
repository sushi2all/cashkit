"""Fixture entities for the Phase 6 VAT gate.

Two books, both hand-computable end to end:

* :func:`build_f24_book` — mixed VAT rates, an exempt item, a reverse-charge
  item, partial deductibility on a car lease, 60-day customer terms and
  quarterly accrual-basis VAT with the 1% ``IVA trimestrale`` surcharge. Its
  expected F24 schedule is committed as ``tests/fixtures/f24_schedule.csv``.
* :func:`build_credit_book` — input exceeding output for two consecutive
  quarters, then sales large enough to consume the accumulated credit, so the
  gate sees both halves of the carry-forward rule.

The horizon runs one month past the calendar year so the fourth quarter's F24 —
due on 16 January — lands inside it, and so the incomplete first quarter of 2027
exercises the "a return that does not close inside the horizon recognises
nothing" rule.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
    TaxRegime,
    VatSpec,
)

START = date(2026, 1, 1)
END = date(2027, 2, 1)
YEAR_END = date(2027, 1, 1)

CALENDAR = CalendarSpec(fiscal_year_start_month=1, country="IT", holidays=[])

PARAMS = {
    "vat_standard": Decimal("0.22"),
    "vat_reduced": Decimal("0.10"),
}

NET_60 = Settlement(due=[DueTerm(share=Decimal(1), offset="60d")])
NET_30 = Settlement(due=[DueTerm(share=Decimal(1), offset="30d")])


def _monthly(day: int) -> Recurrence:
    return Recurrence(every=1, unit=Grain.MONTH, anchor="day_of_month", day=day)


def _item(
    item_id: str,
    amount: str,
    day: int,
    *,
    direction: str,
    vat: VatSpec | None,
    settlement: Settlement | None = None,
    tags: dict[str, str] | None = None,
    start: date = START,
    end: date | None = YEAR_END,
) -> Item:
    return Item(
        id=item_id,
        name=item_id.replace("_", " ").title(),
        kind="flow",
        direction=direction,
        tags=tags or {"cat": "revenue" if direction == "in" else "cost"},
        flags={"cashflow"},
        segments=[
            Segment(
                start=start,
                end=end,
                recurrence=_monthly(day),
                amount=Amount(constant=Decimal(amount)),
            )
        ],
        settlement=settlement,
        vat=vat,
    )


def build_f24_book(*, measure: str = "accrual", surcharge: str = "0.01") -> Book:
    """The mixed-rate fixture entity the F24 gate reproduces.

    Six lines, one of each thing that changes the answer: a standard-rated sale
    on 60-day terms, a reduced-rate sale settling immediately, an exempt sale, a
    reverse-charge purchase, a fully deductible purchase, and a car lease at the
    Italian 40% deductibility.
    """
    items = [
        _item(
            "consulting",
            "10000",
            15,
            direction="in",
            vat=VatSpec(rate="vat_standard"),
            settlement=NET_60,
        ),
        _item(
            "training",
            "4000",
            20,
            direction="in",
            vat=VatSpec(rate="vat_reduced"),
        ),
        _item(
            "grants",
            "2000",
            5,
            direction="in",
            vat=VatSpec(rate="vat_standard", treatment="exempt"),
        ),
        _item(
            "contractor",
            "-3000",
            25,
            direction="out",
            vat=VatSpec(rate="vat_standard", treatment="reverse_charge"),
            settlement=NET_30,
        ),
        _item("rent", "-2500", 1, direction="out", vat=VatSpec(rate="vat_standard")),
        _item(
            "car_lease",
            "-800",
            10,
            direction="out",
            vat=VatSpec(rate="vat_standard", recoverable=Decimal("0.4")),
        ),
    ]
    regime = TaxRegime(
        id="vat",
        accumulates="",  # the default base: every item carrying a VatSpec
        measure=measure,
        periodicity="quarterly",
        payment_offset="16d",
        surcharge=Decimal(surcharge),
    )
    return Book(
        id="f24-fixture",
        base_grain=Grain.DAY,
        calendar=CALENDAR,
        horizon=PeriodRange(start=START, end=END),
        opening_balance=Decimal("100000"),
        cutover=START,
        params=dict(PARAMS),
        items={item.id: item for item in items},
        tax_regimes=[regime],
    )


def build_credit_book() -> Book:
    """Input exceeding output for two quarters, then sales that consume the credit."""
    sales = Item(
        id="sales",
        name="Sales",
        kind="flow",
        direction="in",
        tags={"cat": "revenue"},
        flags={"cashflow"},
        segments=[
            Segment(
                start=START,
                end=date(2026, 7, 1),
                recurrence=_monthly(10),
                amount=Amount(constant=Decimal("1000")),
            ),
            Segment(
                start=date(2026, 7, 1),
                end=YEAR_END,
                recurrence=_monthly(10),
                amount=Amount(constant=Decimal("30000")),
            ),
        ],
        vat=VatSpec(rate="vat_standard"),
    )
    equipment = _item(
        "equipment",
        "-10000",
        20,
        direction="out",
        vat=VatSpec(rate="vat_standard"),
        end=date(2026, 7, 1),
    )
    regime = TaxRegime(
        id="vat",
        accumulates="",
        measure="accrual",
        periodicity="quarterly",
        payment_offset="16d",
    )
    return Book(
        id="credit-fixture",
        base_grain=Grain.DAY,
        calendar=CALENDAR,
        horizon=PeriodRange(start=START, end=END),
        opening_balance=Decimal("100000"),
        cutover=START,
        params=dict(PARAMS),
        items={sales.id: sales, equipment.id: equipment},
        tax_regimes=[regime],
    )
