"""The 20-item gate fixture (Phase 2) and the corpus generator (Phase 3).

:func:`build_gate_book` is the book the Phase 2 gate hand-verifies against
``tests/fixtures/hand_verified.csv``. It is deliberately dense: multi-segment
items, escalation crossing an anniversary boundary, share splits, fixed-amount
splits with a clamped remainder, a credit note through a fixed-amount
settlement, withholding, business-day rolls in both directions, an explicit
schedule, an accrual-only item, probability weighting, ``agg``/``cum``/``where``
formulas, a masked division by zero, and a genuine ``prev()`` feedback loop
between the cash balance and quarter-end interest.

Horizon is six months at day grain (181 periods) so the naive engine stays
usable in a test suite while every semantic above is exercised.
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

CASHFLOW = {"cashflow"}

#: Business-day rules for the gate book. 2026-01-01 is a Thursday; the holidays
#: are New Year, Epiphany (an Italian public holiday) and Easter Monday 2026.
GATE_CALENDAR = CalendarSpec(
    fiscal_year_start_month=1,
    country="IT",
    holidays=[date(2026, 1, 1), date(2026, 1, 6), date(2026, 4, 6)],
    weekend={5, 6},
)

GATE_PARAMS = {
    "vat_standard": Decimal("0.22"),
    "inflation": Decimal("0.03"),
    "esc_acme": Decimal("0.05"),
    "bonus_rate": Decimal("0.10"),
    "fee_rate": Decimal("0.025"),
    "deposit_rate": Decimal("0.005"),
}


def _monthly(day: int | None = None, anchor: str = "period_start", adjust: str = "none") -> Recurrence:
    return Recurrence(
        every=1,
        unit=Grain.MONTH,
        anchor=anchor,
        day=day,
        business_day_adjust=adjust,
    )


def build_gate_book() -> Book:
    """Return the 20-item hand-verified gate book.

    Returns a :class:`~cashkit.model.Book`; produces no diagnostics. Evaluating
    it is expected to yield exactly three warnings — ``CK-W001`` on
    ``partial_delivery``, ``CK-W002`` on ``credit_note`` and ``CK-W005`` on
    ``zero_guard`` — and no errors.
    """
    return Book(
        id="gate-book",
        base_grain=Grain.DAY,
        calendar=GATE_CALENDAR,
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2026, 7, 1)),
        opening_balance=Decimal("100000"),
        cutover=date(2026, 1, 1),
        params=dict(GATE_PARAMS),
        items={
            # -- generative revenue ---------------------------------------- #
            "acme_impl": Item(
                id="acme_impl",
                name="Acme implementation",
                kind="flow",
                direction="in",
                tags={"customer": "acme", "cat": "revenue"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        end=date(2026, 4, 1),
                        recurrence=_monthly(anchor="eom"),
                        amount=Amount(constant=Decimal("12000")),
                    ),
                    Segment(
                        start=date(2026, 4, 1),
                        recurrence=_monthly(day=15, anchor="day_of_month"),
                        amount=Amount(constant=Decimal("15000")),
                        escalation=Escalation(
                            rate="esc_acme", every_years=1, anchor="segment_start"
                        ),
                    ),
                ],
                settlement=Settlement.split([(Decimal("0.3"), "0d"), (Decimal("0.7"), "60d")]),
            ),
            "acme_maint": Item(
                id="acme_maint",
                name="Acme maintenance",
                kind="flow",
                direction="in",
                tags={"customer": "acme", "cat": "revenue"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=_monthly(),
                        amount=Amount(constant=Decimal("2000")),
                    )
                ],
                settlement=Settlement.immediate(),
            ),
            "beta_project": Item(
                id="beta_project",
                name="Beta project (pipeline)",
                kind="flow",
                direction="in",
                tags={"customer": "beta", "cat": "revenue"},
                flags={"cashflow", "pipeline"},
                segments=[
                    Segment(
                        start=date(2026, 2, 1),
                        end=date(2026, 5, 1),
                        recurrence=_monthly(day=10, anchor="day_of_month", adjust="next"),
                        amount=Amount(constant=Decimal("8000")),
                        probability=Decimal("0.8"),
                    )
                ],
                settlement=Settlement.net(30),
            ),
            "gamma_deposit": Item(
                id="gamma_deposit",
                name="Gamma deposit then balance",
                kind="flow",
                direction="in",
                tags={"customer": "gamma", "cat": "revenue"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 3, 1),
                        end=date(2026, 6, 1),
                        recurrence=_monthly(),
                        amount=Amount(constant=Decimal("20000")),
                    )
                ],
                settlement=Settlement(
                    due=[
                        DueTerm(amount=Decimal("5000"), offset="0d"),
                        DueTerm(remainder=True, offset="45d"),
                    ]
                ),
            ),
            "partial_delivery": Item(
                id="partial_delivery",
                name="Delta partial delivery (deposit exceeds accrual)",
                kind="flow",
                direction="in",
                tags={"customer": "delta", "cat": "revenue"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 2, 1),
                        end=date(2026, 3, 1),
                        recurrence=_monthly(),
                        amount=Amount(constant=Decimal("3000")),
                    )
                ],
                settlement=Settlement(
                    due=[
                        DueTerm(amount=Decimal("5000"), offset="0d"),
                        DueTerm(remainder=True, offset="30d"),
                    ]
                ),
            ),
            "credit_note": Item(
                id="credit_note",
                name="Acme credit note",
                kind="flow",
                tags={"customer": "acme", "cat": "revenue"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 5, 1),
                        end=date(2026, 6, 1),
                        recurrence=_monthly(),
                        amount=Amount(constant=Decimal("-4000")),
                    )
                ],
                settlement=Settlement(
                    due=[
                        DueTerm(amount=Decimal("1000"), offset="0d"),
                        DueTerm(remainder=True, offset="0d"),
                    ]
                ),
            ),
            # -- generative costs ------------------------------------------ #
            "rent": Item(
                id="rent",
                name="Office rent",
                kind="flow",
                direction="out",
                tags={"cat": "opex", "vendor": "immobiliare"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2025, 1, 1),
                        recurrence=_monthly(day=1, anchor="day_of_month", adjust="next"),
                        amount=Amount(constant=Decimal("-3500")),
                        escalation=Escalation(
                            rate="inflation", every_years=1, anchor="segment_start"
                        ),
                    )
                ],
            ),
            "insurance": Item(
                id="insurance",
                name="Insurance premium",
                kind="flow",
                direction="out",
                tags={"cat": "opex", "vendor": "assicurazioni"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2024, 3, 15),
                        recurrence=_monthly(day=15, anchor="day_of_month"),
                        amount=Amount(constant=Decimal("-1200")),
                        escalation=Escalation(
                            rate="inflation", every_years=1, anchor="segment_start"
                        ),
                    )
                ],
            ),
            "salaries": Item(
                id="salaries",
                name="Salaries",
                kind="flow",
                direction="out",
                tags={"cat": "payroll"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=_monthly(anchor="eom", adjust="prev"),
                        amount=Amount(constant=Decimal("-25000")),
                    )
                ],
            ),
            "consultant": Item(
                id="consultant",
                name="Consultant (ritenuta d'acconto)",
                kind="flow",
                direction="out",
                tags={"cat": "opex", "vendor": "studio"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=_monthly(),
                        amount=Amount(constant=Decimal("-5000")),
                    )
                ],
                settlement=Settlement(
                    due=[DueTerm(share=Decimal("1"), offset="30d", withholding=Decimal("0.20"))]
                ),
            ),
            "hosting": Item(
                id="hosting",
                name="Hosting (weekly)",
                kind="flow",
                direction="out",
                tags={"cat": "opex"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=Recurrence(every=1, unit=Grain.WEEK),
                        amount=Amount(constant=Decimal("-200")),
                    )
                ],
            ),
            "equipment": Item(
                id="equipment",
                name="Equipment purchases (explicit schedule)",
                kind="flow",
                direction="out",
                tags={"cat": "capex"},
                flags=CASHFLOW,
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        end=date(2026, 7, 1),
                        recurrence=_monthly(),
                        amount=Amount(
                            schedule=[
                                (date(2026, 2, 15), Decimal("-8000")),
                                (date(2026, 5, 20), Decimal("-12000")),
                            ]
                        ),
                    )
                ],
            ),
            "grant": Item(
                id="grant",
                name="Grant accrual (never settles)",
                kind="flow",
                direction="in",
                tags={"cat": "other"},
                segments=[
                    Segment(
                        start=date(2026, 4, 1),
                        end=date(2026, 5, 1),
                        recurrence=_monthly(),
                        amount=Amount(constant=Decimal("50000")),
                        probability=Decimal("0.5"),
                    )
                ],
                settlement=Settlement(due=[]),
            ),
            # -- derived ---------------------------------------------------- #
            "revenue_total": Item(
                id="revenue_total",
                name="Total revenue accrued",
                kind="derived",
                tags={"cat": "derived"},
                formula='agg(tag="cat:revenue", measure="accrual")',
                settlement=Settlement(due=[]),
            ),
            "bonus": Item(
                id="bonus",
                name="Sales bonus",
                kind="derived",
                direction="out",
                tags={"cat": "payroll_var"},
                flags=CASHFLOW,
                formula='-agg(tag="cat:revenue", measure="accrual") * p.bonus_rate',
            ),
            "fee": Item(
                id="fee",
                name="Platform fee above threshold",
                kind="derived",
                direction="out",
                tags={"cat": "fees"},
                flags=CASHFLOW,
                formula=(
                    'where(agg(tag="cat:revenue", measure="accrual") > 10000, '
                    '-agg(tag="cat:revenue", measure="accrual") * p.fee_rate, 0)'
                ),
            ),
            "interest_income": Item(
                id="interest_income",
                name="Quarter-end deposit interest",
                kind="derived",
                direction="in",
                tags={"cat": "financial"},
                flags=CASHFLOW,
                formula=(
                    'where(t.is_quarter_end, '
                    'prev("cash", init=p.opening_balance) * p.deposit_rate, 0)'
                ),
            ),
            "cash": Item(
                id="cash",
                name="Cash balance",
                kind="stock",
                tags={"cat": "balance"},
                formula='prev("cash", init=p.opening_balance) + agg(tag="flag:cashflow")',
                agg_rule="last",
            ),
            "cum_revenue": Item(
                id="cum_revenue",
                name="Cumulative Acme revenue",
                kind="stock",
                tags={"cat": "balance"},
                formula=(
                    'cum("acme_impl", measure="accrual") '
                    '+ cum("acme_maint", measure="accrual")'
                ),
                agg_rule="last",
            ),
            "zero_guard": Item(
                id="zero_guard",
                name="Masked division probe",
                kind="derived",
                tags={"cat": "diag"},
                formula=(
                    'agg(tag="cat:revenue", measure="accrual") '
                    '/ it("revenue_total", measure="accrual")'
                ),
                settlement=Settlement(due=[]),
            ),
        },
    )
