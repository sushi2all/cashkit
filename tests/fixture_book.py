"""Deterministic fixture book for golden-file tests.

The committed ``tests/fixtures/canonical_book.yaml`` is the canonical
serialization of :func:`build_fixture_book`. If the emitter's output ever
drifts, the golden test fails — phantom diffs across versions are a build
failure (PRD §10).
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
    TaxRegime,
    VatSpec,
    Watermark,
)


def build_fixture_book() -> Book:
    """Return a fixed Book exercising every serialization feature: schedules,
    escalation, settlement splits with withholding, VAT, tax regimes,
    watermark, params, unicode and quoting edge cases.
    """
    return Book(
        id="acme-cashflow",
        base_grain=Grain.DAY,
        calendar=CalendarSpec(
            fiscal_year_start_month=1,
            country="IT",
            holidays=[date(2026, 1, 1), date(2026, 1, 6), date(2026, 12, 25)],
            weekend={5, 6},
        ),
        horizon=PeriodRange(start=date(2026, 1, 1), end=date(2031, 1, 1)),
        opening_balance=Decimal("250000.00"),
        cutover=date(2026, 7, 1),
        ledger_watermark=Watermark(
            max_rowid=1042, row_count=1042, content_hash="9f2c4d6e8a0b1c3d"
        ),
        params={
            "vat_standard": Decimal("0.22"),
            "inflation": Decimal("0.031"),
            "acme_escalation": Decimal("0.05"),
        },
        items={
            "acme_impl": Item(
                id="acme_impl",
                name='Acme "Phase 1" implementation — fixed fee',
                kind="flow",
                direction="in",
                tags={"customer": "acme", "cat": "revenue"},
                flags={"committed"},
                currency="EUR",
                segments=[
                    Segment(
                        start=date(2026, 3, 1),
                        end=date(2027, 3, 1),
                        recurrence=Recurrence(
                            every=1,
                            unit=Grain.MONTH,
                            anchor="eom",
                            business_day_adjust="prev",
                        ),
                        amount=Amount(constant=Decimal("12000")),
                        escalation=Escalation(
                            rate="acme_escalation",
                            every_years=1,
                            anchor="segment_start",
                        ),
                        probability=Decimal("1"),
                    ),
                    Segment(
                        start=date(2027, 3, 1),
                        recurrence=Recurrence(
                            every=1, unit=Grain.MONTH, anchor="day_of_month", day=31
                        ),
                        amount=Amount(
                            schedule=[
                                (date(2027, 3, 31), Decimal("8000.0000")),
                                (date(2027, 4, 30), Decimal("6500.50")),
                            ]
                        ),
                        probability=Decimal("0.8"),
                    ),
                ],
                settlement=Settlement(
                    due=[
                        DueTerm(share=Decimal("0.3"), offset="0d"),
                        DueTerm(
                            share=Decimal("0.7"),
                            offset="90d",
                            basis="month_end",
                            adjust="next",
                            withholding=Decimal("0.2"),
                        ),
                    ]
                ),
                vat=VatSpec(
                    rate="vat_standard", treatment="standard", recoverable=Decimal(1)
                ),
                agg_rule="sum",
            ),
            "cash": Item(
                id="cash",
                name="Cash balance",
                kind="stock",
                formula='prev("cash", init=p.opening_balance) + agg(tag="cat:revenue")',
                agg_rule="last",
            ),
            "rent": Item(
                id="rent",
                name="Office rent\nviale Città 42",
                kind="flow",
                direction="out",
                tags={"cat": "opex", "vendor": "immobiliare_rossi"},
                segments=[
                    Segment(
                        start=date(2026, 1, 1),
                        recurrence=Recurrence(every=1, unit=Grain.MONTH),
                        amount=Amount(constant=Decimal("-1200.50")),
                    )
                ],
                settlement=Settlement(due=[]),
            ),
        },
        tax_regimes=[
            TaxRegime(
                id="iva",
                accumulates="",
                measure="accrual",
                periodicity="quarterly",
                payment_offset="16d",
                surcharge=Decimal("0.01"),
                credit_handling="carry",
                annual_adjustment_month=3,
            )
        ],
    )
