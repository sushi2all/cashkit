"""A small authored book for the scenario tests.

Three generative items and one feedback item, so a resolved scenario is
something both engines can actually evaluate — the Phase 7 gate is about
resolution, but a resolution that produces an unevaluable book would be a
hollow pass.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from cashkit.model import (
    Amount,
    Book,
    CalendarSpec,
    DueTerm,
    Escalation,
    Grain,
    Item,
    PeriodRange,
    Recurrence,
    Segment,
    Settlement,
)

MONTHLY = Recurrence(every=1, unit=Grain.MONTH, anchor="period_start")


def monthly_segment(
    start: date,
    end: date | None,
    amount: str,
    *,
    escalation: Escalation | None = None,
    probability: str = "1",
) -> Segment:
    """One monthly segment paying ``amount`` from ``start``. No diagnostics."""
    return Segment(
        start=start,
        end=end,
        recurrence=MONTHLY,
        amount=Amount(constant=Decimal(amount)),
        escalation=escalation,
        probability=Decimal(probability),
    )


def build_scenario_book() -> Book:
    """Return the authored base book the scenario tests fork from."""
    return Book(
        id="scenario-book",
        base_grain=Grain.DAY,
        calendar=CalendarSpec(fiscal_year_start_month=1, weekend={5, 6}),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2028, 1, 1)),
        opening_balance=Decimal("100000.0000"),
        cutover=date(2026, 1, 1),
        params={"escalation": Decimal("0.03"), "churn": Decimal("0.10")},
        items={
            "acme": Item(
                id="acme",
                name="Acme maintenance",
                kind="flow",
                direction="in",
                tags={"cat": "revenue", "customer": "acme"},
                flags={"committed"},
                segments=[
                    monthly_segment(
                        date(2026, 1, 1),
                        date(2027, 1, 1),
                        "10000.0000",
                    ),
                    monthly_segment(
                        date(2027, 1, 1),
                        None,
                        "12000.0000",
                        escalation=Escalation(rate="escalation", every_years=1),
                    ),
                ],
                settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="30d")]),
            ),
            "rent": Item(
                id="rent",
                name="Office rent",
                kind="flow",
                direction="out",
                tags={"cat": "opex", "site": "milan"},
                segments=[monthly_segment(date(2026, 1, 1), None, "-4000.0000")],
                settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="0d")]),
            ),
            "payroll": Item(
                id="payroll",
                name="Payroll",
                kind="flow",
                direction="out",
                tags={"cat": "opex", "site": "milan"},
                segments=[monthly_segment(date(2026, 1, 1), None, "-25000.0000")],
                settlement=Settlement(due=[DueTerm(share=Decimal(1), offset="0d")]),
            ),
            "cash": Item(
                id="cash",
                name="Cash balance",
                kind="stock",
                tags={"cat": "balance"},
                formula=(
                    'prev("cash", n=1, init=p.opening_balance) '
                    '+ agg(tag="cat:revenue") + agg(tag="cat:opex")'
                ),
            ),
        },
    )
