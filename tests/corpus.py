"""The Phase 3 dual-engine corpus: books both engines must agree on exactly.

The gate is byte-identical output between ``cashkit.reference`` (naive Decimal)
and ``cashkit.engine`` (vectorized int64) over a corpus of at least 50 books
covering multi-segment items, every ``Recurrence`` anchor, business-day
adjustment, every ``DueTerm`` shape including ``remainder`` clamping,
``prev(n>1)``, feedback loops, ``agg()`` selectors, empty-``due`` accrual-only
items, ``probability < 1``, withholding and mixed-sign amounts.

Three layers, all deterministic:

* **focus books** — one modelling idea each, including the books that are
  deliberately *broken*, because the two engines must agree on which
  diagnostics a bad book produces, not only on its numbers;
* **sweeps** — the cross-products where a bug would hide in one cell: anchor x
  business-day adjustment, settlement basis x adjustment, recurrence unit, base
  grain;
* **seeded random books** — a fixed-seed generator over the same pools, to
  reach combinations no one thought to enumerate.

:func:`coverage_of` re-derives what the corpus actually exercises from the books
themselves, so the coverage assertion in ``tests/test_dual_engine.py`` cannot
rot into checking a stale list.
"""

from __future__ import annotations

import random
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

__all__ = ["build_corpus", "coverage_of", "REQUIRED_COVERAGE"]

START = date(2026, 1, 1)
END = date(2026, 7, 1)

#: Italian public holidays inside the standard corpus horizon, plus enough of
#: 2027-2029 that the long-horizon books roll over real dates.
HOLIDAYS = [
    date(2026, 1, 1), date(2026, 1, 6), date(2026, 4, 6), date(2026, 4, 25),
    date(2026, 5, 1), date(2026, 6, 2), date(2026, 8, 15), date(2026, 11, 1),
    date(2026, 12, 8), date(2026, 12, 25), date(2026, 12, 26),
    date(2027, 1, 1), date(2027, 1, 6), date(2027, 3, 29), date(2027, 4, 25),
    date(2028, 1, 1), date(2028, 1, 6), date(2028, 4, 17), date(2029, 1, 1),
]

CALENDAR = CalendarSpec(
    fiscal_year_start_month=1, country="IT", holidays=HOLIDAYS, weekend={5, 6}
)
#: A fiscal year starting in July, so `t.is_quarter_end` cannot be right by
#: accident on the calendar quarters.
FISCAL_CALENDAR = CalendarSpec(
    fiscal_year_start_month=7, country="IT", holidays=HOLIDAYS, weekend={5, 6}
)

PARAMS = {
    "inflation": Decimal("0.031"),
    "esc_high": Decimal("0.0725"),
    "fee_rate": Decimal("0.0275"),
    "bonus_rate": Decimal("0.1"),
    "interest_rate": Decimal("0.00137"),
    "threshold": Decimal("10000"),
    "third": Decimal("0.3333"),
}


def _rec(
    unit: Grain = Grain.MONTH,
    *,
    every: int = 1,
    anchor: str = "period_start",
    day: int | None = None,
    adjust: str = "none",
) -> Recurrence:
    return Recurrence(
        every=every, unit=unit, anchor=anchor, day=day, business_day_adjust=adjust
    )


def _seg(
    amount: str | Amount,
    *,
    start: date = START,
    end: date | None = None,
    recurrence: Recurrence | None = None,
    escalation: Escalation | None = None,
    probability: str = "1",
) -> Segment:
    return Segment(
        start=start,
        end=end,
        recurrence=recurrence or _rec(),
        amount=Amount(constant=Decimal(amount)) if isinstance(amount, str) else amount,
        escalation=escalation,
        probability=Decimal(probability),
    )


def _flow(
    item_id: str,
    segments: list[Segment],
    *,
    settlement: Settlement | None = None,
    tags: dict[str, str] | None = None,
    flags: set[str] | None = None,
    currency: str = "EUR",
) -> Item:
    return Item(
        id=item_id,
        name=item_id.replace("_", " ").title(),
        kind="flow",
        tags=tags if tags is not None else {"cat": "revenue"},
        flags=flags if flags is not None else {"cashflow"},
        currency=currency,
        segments=segments,
        settlement=settlement,
    )


def _derived(
    item_id: str,
    formula: str,
    *,
    kind: str = "derived",
    settlement: Settlement | None = None,
    tags: dict[str, str] | None = None,
    flags: set[str] | None = None,
) -> Item:
    return Item(
        id=item_id,
        name=item_id.replace("_", " ").title(),
        kind=kind,
        tags=tags if tags is not None else {"cat": "derived"},
        flags=flags if flags is not None else set(),
        formula=formula,
        settlement=settlement,
        agg_rule="last" if kind == "stock" else "sum",
    )


def _book(
    book_id: str,
    items: list[Item],
    *,
    start: date = START,
    end: date = END,
    grain: Grain = Grain.DAY,
    cutover: date | None = None,
    calendar: CalendarSpec | None = None,
    opening: str = "125000",
) -> Book:
    return Book(
        id=book_id,
        base_grain=grain,
        calendar=calendar or CALENDAR,
        horizon=PeriodRange(start=start, end=end),
        opening_balance=Decimal(opening),
        cutover=cutover or start,
        params=dict(PARAMS),
        items={item.id: item for item in items},
    )


#: The canonical cash fold: a stock reading its own previous level plus every
#: item flagged for cash. Appears in most corpus books so the non-trivial SCC
#: path is exercised everywhere, not only in the feedback focus book.
CASH = _derived(
    "cash",
    'prev("cash", init=p.opening_balance) + agg(tag="flag:cashflow")',
    kind="stock",
    tags={"cat": "balance"},
)


# --------------------------------------------------------------------------- #
# Focus books
# --------------------------------------------------------------------------- #


def _focus_books() -> list[tuple[str, Book]]:
    books: list[tuple[str, Book]] = []

    books.append((
        "multi-segment with escalation on both anchors",
        _book(
            "multi-segment",
            [
                _flow(
                    "phased",
                    [
                        _seg("4000", end=date(2026, 3, 1), recurrence=_rec(anchor="eom")),
                        _seg(
                            "6000",
                            start=date(2026, 3, 1),
                            end=date(2026, 5, 1),
                            recurrence=_rec(anchor="day_of_month", day=15),
                            escalation=Escalation(
                                rate="inflation", every_years=1, anchor="segment_start"
                            ),
                        ),
                        _seg(
                            "7500",
                            start=date(2026, 5, 1),
                            recurrence=_rec(anchor="period_end", adjust="prev"),
                            escalation=Escalation(
                                rate=Decimal("0.02"),
                                every_years=1,
                                anchor="calendar_year",
                            ),
                        ),
                    ],
                    settlement=Settlement.split(
                        [(Decimal("0.25"), "0d"), (Decimal("0.75"), "45d")]
                    ),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "escalation over a five-year horizon, both anchors",
        _book(
            "long-escalation",
            [
                _flow(
                    "rent",
                    [
                        _seg(
                            "-3300",
                            start=date(2023, 7, 1),
                            recurrence=_rec(anchor="day_of_month", day=1, adjust="next"),
                            escalation=Escalation(
                                rate="esc_high", every_years=1, anchor="segment_start"
                            ),
                        )
                    ],
                    tags={"cat": "opex"},
                ),
                _flow(
                    "licence",
                    [
                        _seg(
                            "-900",
                            start=date(2024, 2, 29),
                            recurrence=_rec(Grain.QUARTER, anchor="eom"),
                            escalation=Escalation(
                                rate="inflation", every_years=2, anchor="calendar_year"
                            ),
                        )
                    ],
                    tags={"cat": "opex"},
                    settlement=Settlement.net(90),
                ),
                CASH,
            ],
            start=date(2026, 1, 1),
            end=date(2031, 1, 1),
            grain=Grain.MONTH,
        ),
    ))

    books.append((
        "prev(n>1) chain and a two-item feedback loop",
        _book(
            "feedback",
            [
                _flow("sales", [_seg("9000", recurrence=_rec(anchor="eom"))]),
                _derived(
                    "trailing",
                    'prev("sales", n=3, init=0) + prev("sales", n=7, init=250)',
                    tags={"cat": "derived"},
                ),
                _derived(
                    "overdraft",
                    'where(prev("cash", init=p.opening_balance) < 0, '
                    'prev("cash", init=p.opening_balance) * p.interest_rate, 0)',
                    flags={"cashflow"},
                    tags={"cat": "financial"},
                ),
                _derived(
                    "reserve",
                    'prev("reserve", n=2, init=1000) * 1 + it("overdraft") / 2',
                    kind="stock",
                    tags={"cat": "balance"},
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "remainder clamped by an oversized deposit (CK-W001)",
        _book(
            "clamped-remainder",
            [
                _flow(
                    "small_job",
                    [_seg("2500", end=date(2026, 4, 1))],
                    settlement=Settlement(
                        due=[
                            DueTerm(amount=Decimal("4000"), offset="0d"),
                            DueTerm(remainder=True, offset="30d"),
                        ]
                    ),
                ),
                _flow(
                    "big_job",
                    [_seg("20000", start=date(2026, 4, 1))],
                    settlement=Settlement(
                        due=[
                            DueTerm(amount=Decimal("4000"), offset="0d"),
                            DueTerm(remainder=True, offset="30d"),
                        ]
                    ),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "mixed-sign credit notes through fixed and share settlements (CK-W002)",
        _book(
            "credit-notes",
            [
                _flow(
                    "invoices",
                    [_seg("11000", recurrence=_rec(anchor="day_of_month", day=10))],
                    settlement=Settlement.split(
                        [(Decimal("0.5"), "0d"), (Decimal("0.5"), "60d")]
                    ),
                ),
                _flow(
                    "credit_fixed",
                    [
                        _seg(
                            "-3700",
                            start=date(2026, 2, 1),
                            recurrence=_rec(anchor="day_of_month", day=20),
                        )
                    ],
                    settlement=Settlement(
                        due=[
                            DueTerm(amount=Decimal("500"), offset="0d"),
                            DueTerm(remainder=True, offset="15d"),
                        ]
                    ),
                ),
                _flow(
                    "credit_shares",
                    [_seg("-1234.5678", recurrence=_rec(Grain.WEEK))],
                    settlement=Settlement.split(
                        [(Decimal("0.3333"), "0d"), (Decimal("0.6667"), "7d")]
                    ),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "withholding on every leg, plus an accrual-only item",
        _book(
            "withholding",
            [
                _flow(
                    "consultant",
                    [_seg("-5000")],
                    tags={"cat": "opex"},
                    settlement=Settlement(
                        due=[
                            DueTerm(
                                share=Decimal("0.4"),
                                offset="0d",
                                withholding=Decimal("0.20"),
                            ),
                            DueTerm(
                                share=Decimal("0.6"),
                                offset="30d",
                                withholding=Decimal("0.045"),
                            ),
                        ]
                    ),
                ),
                _flow(
                    "agent",
                    [_seg("-1750.25", recurrence=_rec(Grain.WEEK))],
                    tags={"cat": "opex"},
                    settlement=Settlement(
                        due=[
                            DueTerm(
                                amount=Decimal("300"),
                                offset="0d",
                                withholding=Decimal("1"),
                            ),
                            DueTerm(
                                remainder=True, offset="14d", withholding=Decimal("0.23")
                            ),
                        ]
                    ),
                ),
                _flow(
                    "grant_accrual",
                    [_seg("40000", start=date(2026, 3, 1), end=date(2026, 4, 1))],
                    tags={"cat": "other"},
                    flags=set(),
                    settlement=Settlement(due=[]),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "probability weighting across the rounding boundary",
        _book(
            "probability",
            [
                _flow(
                    "pipeline_a",
                    [_seg("8333.3333", probability="0.65")],
                    flags={"cashflow", "pipeline"},
                    settlement=Settlement.net(30),
                ),
                _flow(
                    "pipeline_b",
                    [
                        _seg(
                            "12345.6789",
                            recurrence=_rec(Grain.WEEK),
                            probability="0.005",
                        )
                    ],
                    flags={"cashflow", "pipeline"},
                ),
                _flow(
                    "pipeline_c",
                    [
                        _seg(
                            "-999.9999",
                            recurrence=_rec(anchor="eom"),
                            probability="0.5",
                            escalation=Escalation(rate="esc_high", every_years=1),
                        )
                    ],
                    flags={"cashflow", "pipeline"},
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "agg selectors: tag equality, flag membership, ANDed terms",
        _book(
            "selectors",
            [
                _flow("acme_a", [_seg("5000")], tags={"cat": "revenue", "cust": "acme"}),
                _flow("acme_b", [_seg("2500")], tags={"cat": "revenue", "cust": "acme"},
                      flags={"cashflow", "committed"}),
                _flow("beta_a", [_seg("-1500")], tags={"cat": "opex", "cust": "beta"}),
                _derived("acme_total", 'agg(tag="cust:acme", measure="accrual")'),
                _derived("committed_cash", 'agg(tag="flag:committed")'),
                _derived(
                    "acme_committed",
                    'agg(tag="cust:acme flag:committed", measure="accrual")',
                ),
                _derived("running", 'cum("acme_a", measure="accrual") - cum("beta_a")'),
                CASH,
            ],
        ),
    ))

    books.append((
        "every numeric builtin as a column operation",
        _book(
            "builtins",
            [
                _flow("base", [_seg("3333.3333", recurrence=_rec(Grain.WEEK))]),
                _flow("other", [_seg("-777.7777", recurrence=_rec(anchor="eom"))]),
                _derived("b_min", 'min(it("base", measure="accrual"), it("other", measure="accrual"), 0)'),
                _derived("b_max", 'max(it("base", measure="accrual"), p.threshold)'),
                _derived("b_clip", 'clip(it("base", measure="accrual"), -1000, 2500)'),
                _derived("b_round0", 'round_(it("base", measure="accrual"))'),
                _derived("b_round2", 'round_(it("base", measure="accrual") * p.third, ndigits=2)'),
                _derived("b_abs", 'abs_(it("other", measure="accrual"))'),
                _derived(
                    "b_logic",
                    'where(t.is_business_day and not t.is_quarter_end, '
                    'it("base", measure="accrual"), '
                    'where(t.month == 3 or t.index > 100, -1, 0))',
                ),
                _derived(
                    "b_div",
                    'it("base", measure="accrual") / it("other", measure="accrual")',
                ),
                CASH,
            ],
            calendar=FISCAL_CALENDAR,
        ),
    ))

    books.append((
        "masked division by zero in selected and unselected branches (CK-W005)",
        _book(
            "zero-division",
            [
                _flow("numerator", [_seg("1000", recurrence=_rec(Grain.WEEK))]),
                _flow(
                    "sometimes_zero",
                    [_seg("250", start=date(2026, 4, 1), recurrence=_rec(Grain.WEEK))],
                ),
                _derived(
                    "selected_div",
                    'it("numerator", measure="accrual") '
                    '/ it("sometimes_zero", measure="accrual")',
                ),
                _derived(
                    "guarded_div",
                    'where(it("sometimes_zero", measure="accrual") == 0, 0, '
                    'it("numerator", measure="accrual") '
                    '/ it("sometimes_zero", measure="accrual"))',
                ),
                _derived("rate_div", "1 / 0 + p.fee_rate"),
                CASH,
            ],
        ),
    ))

    books.append((
        "explicit schedules, one dated outside the horizon",
        _book(
            "schedules",
            [
                _flow(
                    "capex",
                    [
                        _seg(
                            Amount(
                                schedule=[
                                    (date(2025, 12, 31), Decimal("-5000")),
                                    (date(2026, 2, 14), Decimal("-8250.5")),
                                    (date(2026, 5, 30), Decimal("-12000")),
                                    (date(2026, 9, 1), Decimal("-4000")),
                                ]
                            ),
                            end=date(2026, 3, 1),
                        )
                    ],
                    tags={"cat": "capex"},
                    settlement=Settlement.split(
                        [(Decimal("0.5"), "0d"), (Decimal("0.5"), "30d")]
                    ),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "cutover mid-horizon suppresses generation before it (ADR-0004)",
        _book(
            "cutover",
            [
                _flow(
                    "recurring",
                    [_seg("3000", recurrence=_rec(Grain.WEEK))],
                    settlement=Settlement.net(60),
                ),
                _flow("monthly", [_seg("-1200", recurrence=_rec(anchor="eom"))]),
                CASH,
            ],
            cutover=date(2026, 3, 15),
        ),
    ))

    books.append((
        "settlement legs landing outside the horizon on both sides",
        _book(
            "horizon-edges",
            [
                _flow(
                    "late",
                    [_seg("6000", recurrence=_rec(anchor="eom"))],
                    settlement=Settlement.net(200),
                ),
                _flow(
                    "rolled_back",
                    [
                        _seg(
                            "4000",
                            recurrence=_rec(anchor="period_start"),
                        )
                    ],
                    settlement=Settlement(
                        due=[DueTerm(share=Decimal(1), offset="0d", adjust="prev")]
                    ),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "a stock read by both measures, and a derived item that settles",
        _book(
            "stocks",
            [
                _flow("inflow", [_seg("2000", recurrence=_rec(Grain.WEEK))]),
                _derived(
                    "level",
                    'prev("level", init=5000) + it("inflow", measure="accrual")',
                    kind="stock",
                    tags={"cat": "balance"},
                ),
                _derived(
                    "levy",
                    '-it("level", measure="accrual") * p.fee_rate',
                    flags={"cashflow"},
                    settlement=Settlement(
                        due=[
                            DueTerm(share=Decimal("0.5"), offset="0d", basis="month_end"),
                            DueTerm(
                                share=Decimal("0.5"),
                                offset="1m",
                                basis="period_end",
                                withholding=Decimal("0.1"),
                            ),
                        ]
                    ),
                ),
                _derived("levy_cash_echo", 'it("levy", measure="cash")'),
                _derived("level_cash_echo", 'it("level", measure="cash")'),
                CASH,
            ],
        ),
    ))

    books.append((
        "mixed currencies that never meet in one aggregate",
        _book(
            "currencies",
            [
                _flow("eur_sales", [_seg("5000")], currency="EUR",
                      tags={"cat": "revenue", "ccy": "eur"}),
                _flow("usd_sales", [_seg("6000")], currency="USD",
                      tags={"cat": "revenue", "ccy": "usd"}, flags=set()),
                _derived("eur_total", 'agg(tag="ccy:eur", measure="accrual")'),
                CASH,
            ],
        ),
    ))

    # -- deliberately broken books ------------------------------------------ #

    books.append((
        "broken: unknown item id and an empty selector (CK-E001)",
        _book(
            "broken-e001",
            [
                _flow("real", [_seg("1000")]),
                _derived("ghost_ref", 'it("does_not_exist") + 1'),
                _derived("empty_agg", 'agg(tag="cat:nothing_here")'),
                CASH,
            ],
        ),
    ))

    books.append((
        "broken: cycle without prev(), and self-selecting agg (CK-E002)",
        _book(
            "broken-e002",
            [
                _flow("seed", [_seg("1000")]),
                _derived("loop_a", 'it("loop_b") + it("seed", measure="accrual")'),
                _derived("loop_b", 'it("loop_a") * 2'),
                _derived("self_agg", 'agg(tag="cat:derived", measure="accrual")'),
                CASH,
            ],
        ),
    ))

    books.append((
        "broken: rejected formulas and kind/segment mismatches (CK-E003)",
        _book(
            "broken-e003",
            [
                _flow("ok", [_seg("1000")]),
                _derived("attribute_access", '__import__("os").system("echo")'),
                _derived("bad_syntax", "1 +"),
                _derived("if_expression", "1 if t.index else 2"),
                Item(
                    id="flow_with_formula",
                    name="Flow with formula",
                    kind="flow",
                    formula='it("ok")',
                    segments=[_seg("500")],
                    tags={"cat": "revenue"},
                ),
                Item(
                    id="derived_with_segments",
                    name="Derived with segments",
                    kind="derived",
                    formula='it("ok")',
                    segments=[_seg("500")],
                    tags={"cat": "derived"},
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "broken: settlement structure (CK-E004, CK-E005)",
        _book(
            "broken-settlement",
            [
                _flow(
                    "shares_short",
                    [_seg("1000")],
                    settlement=Settlement.split(
                        [(Decimal("0.3"), "0d"), (Decimal("0.6"), "30d")]
                    ),
                ),
                _flow(
                    "mixed_terms",
                    [_seg("2000")],
                    settlement=Settlement(
                        due=[
                            DueTerm(share=Decimal("0.5"), offset="0d"),
                            DueTerm(amount=Decimal("100"), offset="0d"),
                        ]
                    ),
                ),
                _flow(
                    "two_remainders",
                    [_seg("3000")],
                    settlement=Settlement(
                        due=[
                            DueTerm(remainder=True, offset="0d"),
                            DueTerm(remainder=True, offset="30d"),
                        ]
                    ),
                ),
                _flow(
                    "no_remainder",
                    [_seg("4000")],
                    settlement=Settlement(
                        due=[DueTerm(amount=Decimal("100"), offset="0d")]
                    ),
                ),
                CASH,
            ],
        ),
    ))

    books.append((
        "broken: unknown params in a formula and in escalation (CK-E008)",
        _book(
            "broken-e008",
            [
                _flow(
                    "escalating",
                    [
                        _seg(
                            "1000",
                            escalation=Escalation(rate="no_such_rate", every_years=1),
                        )
                    ],
                ),
                _derived("uses_missing", "p.also_missing * 2"),
                _derived("prev_init_missing", 'prev("cash", init=p.nope)'),
                CASH,
            ],
        ),
    ))

    books.append((
        "broken: cross-currency aggregate (CK-E020)",
        _book(
            "broken-e020",
            [
                _flow("eur_line", [_seg("1000")], currency="EUR",
                      tags={"cat": "revenue"}),
                _flow("usd_line", [_seg("1000")], currency="USD",
                      tags={"cat": "revenue"}, flags=set()),
                _derived("mixed_total", 'agg(tag="cat:revenue", measure="accrual")'),
                CASH,
            ],
        ),
    ))

    return books


# --------------------------------------------------------------------------- #
# Sweeps
# --------------------------------------------------------------------------- #

ANCHORS: tuple[tuple[str, int | None], ...] = (
    ("period_start", None),
    ("period_end", None),
    ("eom", None),
    ("day_of_month", 15),
    ("day_of_month", 31),
)
ADJUSTS = ("none", "prev", "next")
BASES = ("accrual", "period_end", "month_end")
UNITS = (Grain.DAY, Grain.WEEK, Grain.MONTH, Grain.QUARTER, Grain.YEAR)


def _anchor_sweep() -> list[tuple[str, Book]]:
    """Every recurrence anchor against every business-day adjustment."""
    books: list[tuple[str, Book]] = []
    for anchor, day in ANCHORS:
        for adjust in ADJUSTS:
            label = f"{anchor}{f'-{day}' if day else ''}-{adjust}"
            books.append((
                f"anchor sweep: {label}",
                _book(
                    f"anchor-{label}",
                    [
                        _flow(
                            "swept",
                            [
                                _seg(
                                    "1234.5678",
                                    end=date(2026, 4, 1),
                                    recurrence=_rec(
                                        anchor=anchor, day=day, adjust=adjust
                                    ),
                                ),
                                _seg(
                                    "-2345.6789",
                                    start=date(2026, 4, 1),
                                    recurrence=_rec(
                                        Grain.WEEK, every=2, anchor=anchor
                                        if anchor in ("period_start", "period_end")
                                        else "period_start",
                                        adjust=adjust,
                                    ),
                                    probability="0.75",
                                ),
                            ],
                            settlement=Settlement.split(
                                [(Decimal("0.35"), "0d"), (Decimal("0.65"), "30d")]
                            ),
                        ),
                        CASH,
                    ],
                ),
            ))
    return books


def _settlement_sweep() -> list[tuple[str, Book]]:
    """Every ``DueTerm`` basis against every leg adjustment, share and fixed."""
    books: list[tuple[str, Book]] = []
    for basis in BASES:
        for adjust in ADJUSTS:
            label = f"{basis}-{adjust}"
            books.append((
                f"settlement sweep: {label}",
                _book(
                    f"settle-{label}",
                    [
                        _flow(
                            "share_split",
                            [_seg("9999.9999", recurrence=_rec(anchor="day_of_month", day=28))],
                            settlement=Settlement(
                                due=[
                                    DueTerm(
                                        share=Decimal("0.15"),
                                        offset="0d",
                                        basis=basis,
                                        adjust=adjust,
                                    ),
                                    DueTerm(
                                        share=Decimal("0.35"),
                                        offset="2m",
                                        basis=basis,
                                        adjust=adjust,
                                        withholding=Decimal("0.04"),
                                    ),
                                    DueTerm(
                                        share=Decimal("0.5"),
                                        offset="1w",
                                        basis=basis,
                                        adjust=adjust,
                                    ),
                                ]
                            ),
                        ),
                        _flow(
                            "fixed_split",
                            [_seg("7500", recurrence=_rec(Grain.MONTH, anchor="eom"))],
                            settlement=Settlement(
                                due=[
                                    DueTerm(
                                        amount=Decimal("2500"),
                                        offset="0d",
                                        basis=basis,
                                        adjust=adjust,
                                    ),
                                    DueTerm(
                                        remainder=True,
                                        offset="1y",
                                        basis=basis,
                                        adjust=adjust,
                                        withholding=Decimal("0.2"),
                                    ),
                                ]
                            ),
                        ),
                        _flow(
                            "clamped",
                            [_seg("100", recurrence=_rec(Grain.WEEK))],
                            settlement=Settlement(
                                due=[
                                    DueTerm(
                                        amount=Decimal("400"),
                                        offset="0d",
                                        basis=basis,
                                        adjust=adjust,
                                    ),
                                    DueTerm(remainder=True, offset="0d", basis=basis),
                                ]
                            ),
                        ),
                        CASH,
                    ],
                ),
            ))
    return books


def _grain_sweep() -> list[tuple[str, Book]]:
    """Every recurrence unit, and every base grain (DECISIONS D-P2-17)."""
    books: list[tuple[str, Book]] = []
    for unit in UNITS:
        books.append((
            f"recurrence unit: {unit.value}",
            _book(
                f"unit-{unit.value}",
                [
                    _flow(
                        "cadence",
                        [
                            _seg(
                                "1500.5",
                                start=date(2025, 11, 17),
                                recurrence=_rec(unit, every=2, adjust="next"),
                                escalation=Escalation(
                                    rate="inflation", every_years=1
                                ),
                            )
                        ],
                        settlement=Settlement.net(45),
                    ),
                    CASH,
                ],
                start=date(2026, 1, 1),
                end=date(2029, 1, 1),
                grain=Grain.MONTH,
            ),
        ))
    for grain in UNITS:
        books.append((
            f"base grain: {grain.value}",
            _book(
                f"grain-{grain.value}",
                [
                    _flow(
                        "daily",
                        [_seg("-42.4242", recurrence=_rec(Grain.DAY, every=3))],
                        settlement=Settlement.net(10),
                    ),
                    _flow(
                        "monthly",
                        [
                            _seg(
                                "8000",
                                recurrence=_rec(anchor="day_of_month", day=15),
                                probability="0.9",
                            )
                        ],
                        settlement=Settlement(
                            due=[
                                DueTerm(share=Decimal(1), offset="0d", basis="period_end")
                            ]
                        ),
                    ),
                    _derived("quarter_bonus", "where(t.is_quarter_end, p.threshold, 0)"),
                    CASH,
                ],
                start=date(2026, 1, 1),
                end=date(2028, 1, 1),
                grain=grain,
                calendar=FISCAL_CALENDAR,
            ),
        ))
    return books


# --------------------------------------------------------------------------- #
# Seeded random books
# --------------------------------------------------------------------------- #

_AMOUNTS = (
    "1000", "-1000", "3333.3333", "-7777.7777", "0.0001", "-0.0005",
    "12345.6789", "-99999.9999", "0", "250.125",
)
_PROBABILITIES = ("1", "0.5", "0.85", "0.005", "0.3333")
_OFFSETS = ("0d", "7d", "30d", "45d", "2w", "1m", "3m", "1y")
_FORMULAS = (
    'agg(tag="cat:revenue", measure="accrual") * p.bonus_rate',
    'where(agg(tag="cat:revenue", measure="accrual") > p.threshold, '
    '-agg(tag="cat:revenue", measure="accrual") * p.fee_rate, 0)',
    'cum("gen_0", measure="accrual") / 3',
    'min(it("gen_0", measure="accrual"), it("gen_1", measure="accrual"))',
    'clip(prev("cash", init=p.opening_balance) * p.interest_rate, -500, 500)',
    'abs_(it("gen_1")) - round_(it("gen_0", measure="accrual"), ndigits=1)',
    'prev("gen_0", n=4, init=p.threshold) + t.index',
)


def _random_books(count: int, seed: int = 20260801) -> list[tuple[str, Book]]:
    rng = random.Random(seed)
    books: list[tuple[str, Book]] = []
    for index in range(count):
        items: list[Item] = []
        for gen in range(rng.randint(2, 4)):
            anchor, day = rng.choice(ANCHORS)
            segments = [
                _seg(
                    rng.choice(_AMOUNTS),
                    start=date(2025, rng.randint(1, 12), rng.randint(1, 28)),
                    end=date(2026, 4, 1) if gen % 2 else None,
                    recurrence=_rec(
                        rng.choice(UNITS),
                        every=rng.randint(1, 3),
                        anchor=anchor,
                        day=day,
                        adjust=rng.choice(ADJUSTS),
                    ),
                    escalation=(
                        Escalation(
                            rate=rng.choice(["inflation", "esc_high", Decimal("0.015")]),
                            every_years=rng.randint(1, 3),
                            anchor=rng.choice(["segment_start", "calendar_year"]),
                        )
                        if rng.random() < 0.5
                        else None
                    ),
                    probability=rng.choice(_PROBABILITIES),
                )
            ]
            if rng.random() < 0.4:
                anchor2, day2 = rng.choice(ANCHORS)
                segments.append(
                    _seg(
                        rng.choice(_AMOUNTS),
                        start=date(2026, 4, 1),
                        recurrence=_rec(
                            rng.choice(UNITS),
                            anchor=anchor2,
                            day=day2,
                            adjust=rng.choice(ADJUSTS),
                        ),
                    )
                )
            items.append(
                _flow(
                    f"gen_{gen}",
                    segments,
                    tags={"cat": "revenue" if gen % 2 == 0 else "opex"},
                    settlement=_random_settlement(rng),
                )
            )
        for derived in range(rng.randint(1, 3)):
            items.append(
                _derived(
                    f"der_{derived}",
                    rng.choice(_FORMULAS),
                    flags={"cashflow"} if rng.random() < 0.5 else set(),
                )
            )
        items.append(CASH)
        books.append((
            f"random book {index}",
            _book(
                f"random-{index}",
                items,
                cutover=date(2026, 1, 1) if index % 3 else date(2026, 2, 10),
                calendar=FISCAL_CALENDAR if index % 4 == 0 else CALENDAR,
            ),
        ))
    return books


def _random_settlement(rng: random.Random) -> Settlement | None:
    roll = rng.random()
    if roll < 0.12:
        return None
    if roll < 0.22:
        return Settlement(due=[])
    if roll < 0.6:
        first = rng.choice([Decimal("0.25"), Decimal("0.4"), Decimal("0.3333")])
        return Settlement(
            due=[
                DueTerm(
                    share=first,
                    offset=rng.choice(_OFFSETS),
                    basis=rng.choice(BASES),
                    adjust=rng.choice(ADJUSTS),
                    withholding=rng.choice([Decimal(0), Decimal("0.2"), Decimal("0.04")]),
                ),
                DueTerm(
                    share=Decimal(1) - first,
                    offset=rng.choice(_OFFSETS),
                    basis=rng.choice(BASES),
                    adjust=rng.choice(ADJUSTS),
                ),
            ]
        )
    return Settlement(
        due=[
            DueTerm(
                amount=Decimal(rng.choice(["500", "2500", "12000"])),
                offset=rng.choice(_OFFSETS),
                basis=rng.choice(BASES),
                adjust=rng.choice(ADJUSTS),
            ),
            DueTerm(
                remainder=True,
                offset=rng.choice(_OFFSETS),
                basis=rng.choice(BASES),
                adjust=rng.choice(ADJUSTS),
                withholding=rng.choice([Decimal(0), Decimal("0.23")]),
            ),
        ]
    )


def build_corpus() -> list[tuple[str, Book]]:
    """Return the whole dual-engine corpus as ``(description, book)`` pairs.

    Deterministic: the random layer is seeded, so a failure names a book that
    can be rebuilt exactly. Produces no diagnostics itself.
    """
    return (
        _focus_books()
        + _anchor_sweep()
        + _settlement_sweep()
        + _grain_sweep()
        + _random_books(14)
    )


# --------------------------------------------------------------------------- #
# Coverage, derived from the corpus rather than asserted about it
# --------------------------------------------------------------------------- #

#: What the Phase 3 gate requires the corpus to exercise.
REQUIRED_COVERAGE = frozenset(
    {
        "multi_segment",
        "anchor:period_start",
        "anchor:period_end",
        "anchor:day_of_month",
        "anchor:eom",
        "adjust:none",
        "adjust:prev",
        "adjust:next",
        "unit:day",
        "unit:week",
        "unit:month",
        "unit:quarter",
        "unit:year",
        "grain:day",
        "grain:week",
        "grain:month",
        "grain:quarter",
        "grain:year",
        "due:share_split",
        "due:fixed_remainder",
        "due:remainder_clamped",
        "due:empty",
        "due:absent",
        "basis:accrual",
        "basis:period_end",
        "basis:month_end",
        "leg_adjust:none",
        "leg_adjust:prev",
        "leg_adjust:next",
        "withholding",
        "probability_lt_1",
        "mixed_sign",
        "escalation:segment_start",
        "escalation:calendar_year",
        "explicit_schedule",
        "prev_lag_gt_1",
        "feedback_loop",
        "agg_tag_selector",
        "agg_flag_selector",
        "cum",
        "stock",
        "cutover_mid_horizon",
        "broken_item",
    }
)


def coverage_of(corpus: list[tuple[str, Book]]) -> set[str]:
    """Re-derive which gate features the corpus actually exercises.

    Reads the books and their compiled graphs, so the coverage claim tracks the
    corpus instead of restating it. Returns a set of feature labels; produces no
    diagnostics.
    """
    from cashkit.engine.expand import FIXED, IMMEDIATE, NEVER, classify_settlement
    from cashkit.engine.graph import compile_book

    found: set[str] = set()
    for _, book in corpus:
        found.add(f"grain:{book.base_grain.value}")
        if book.cutover > book.horizon.start:
            found.add("cutover_mid_horizon")
        compiled = compile_book(book)
        if any(entry.broken for entry in compiled.items.values()):
            found.add("broken_item")
        if any(not component.trivial for component in compiled.components):
            found.add("feedback_loop")
        for item in book.items.values():
            if len(item.segments) >= 2:
                found.add("multi_segment")
            for segment in item.segments:
                found.add(f"anchor:{segment.recurrence.anchor}")
                found.add(f"adjust:{segment.recurrence.business_day_adjust}")
                found.add(f"unit:{segment.recurrence.unit.value}")
                if segment.probability != Decimal(1):
                    found.add("probability_lt_1")
                if segment.escalation is not None:
                    found.add(f"escalation:{segment.escalation.anchor}")
                if segment.amount.schedule is not None:
                    found.add("explicit_schedule")
                    if any(value < 0 for _, value in segment.amount.schedule):
                        found.add("mixed_sign")
                elif segment.amount.constant is not None and segment.amount.constant < 0:
                    found.add("mixed_sign")
            kind, _ = classify_settlement(item)
            if kind == IMMEDIATE:
                found.add("due:absent")
            elif kind == NEVER:
                found.add("due:empty")
            if item.settlement is not None:
                for term in item.settlement.due:
                    found.add(f"basis:{term.basis}")
                    found.add(f"leg_adjust:{term.adjust}")
                    if term.withholding != Decimal(0):
                        found.add("withholding")
                    if term.share is not None:
                        found.add("due:share_split")
                if kind == FIXED:
                    found.add("due:fixed_remainder")
        found |= _formula_coverage(book, compiled)
    found |= _clamping_coverage(corpus)
    return found


def _formula_coverage(book: Book, compiled) -> set[str]:
    from cashkit.engine.formula import Agg, Cum, Prev, iter_refs

    found: set[str] = set()
    for item in book.items.values():
        if item.kind == "stock":
            found.add("stock")
    for entry in compiled.items.values():
        if entry.expr is None:
            continue
        for ref in iter_refs(entry.expr):
            if isinstance(ref, Prev) and ref.lag > 1:
                found.add("prev_lag_gt_1")
            if isinstance(ref, Cum):
                found.add("cum")
            if isinstance(ref, Agg):
                if ref.selector.flags:
                    found.add("agg_flag_selector")
                if ref.selector.tags:
                    found.add("agg_tag_selector")
    return found


def _clamping_coverage(corpus: list[tuple[str, Book]]) -> set[str]:
    """``CK-W001`` is only truly covered if some book actually clamps."""
    import cashkit.engine as engine

    for _, book in corpus:
        if any(
            diagnostic.code == "CK-W001" for diagnostic in engine.run(book).diagnostics
        ):
            return {"due:remainder_clamped"}
    return set()
