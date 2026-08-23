"""T07 — a messy human budget sheet (proto T07).

    Tried: a realistic family-budget table — month columns, POSITIVE expense
    figures, section and subtotal rows, a starting-balance corner cell, a
    13th-month salary, bimonthly utilities, one annual premium, a mid-year
    price rise. Upload, then check the numbers.

This is the actual "initialize from an existing budget" use case, and it is the
one proto trial where lite was one semantic slip away — it made the annual
premium open-ended and charged it seven times. flash passed. The failure class
is worth guarding precisely: a one-off authored as a recurring line is a wrong
number that looks entirely reasonable.

The assertions are on the **closing balance of every month**, not on the items,
because the construct is the model's to choose: a December bonus is equally
correct as a one-off event or as a windowed line, and a trial that insisted on
one of them would fail a right answer. The month-by-month balance is what the
user sees, and it is wrong the moment any construct is wrong.

The sheet is given as text. The xlsx pipeline is S5's (SPEC §7, T16); the model
behaviour is S2's (D-MLP-27).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, closings, make_book, state

pytestmark = pytest.mark.live_model

OPENING = Decimal("3200.00")

SHEET = """Starting balance | 3200
         | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec
INCOME
Salary   | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800
Bonus    |      |      |      |      |      |      |      |      |      |      |      | 2800
Total in | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 2800 | 5600
EXPENSES
Mortgage  | 1150 | 1150 | 1150 | 1150 | 1150 | 1150 | 1150 | 1150 | 1150 | 1150 | 1150 | 1150
Utilities |  240 |      |  240 |      |  240 |      |  240 |      |  240 |      |  240 |
Nursery   |  420 |  420 |  420 |  420 |  420 |  420 |  480 |  480 |  480 |  480 |  480 |  480
Insurance |      |      |      |      |      |  960 |      |      |      |      |      |
Total out | 1810 | 1570 | 1810 | 1570 | 1810 | 2530 | 1870 | 1630 | 1870 | 1630 | 1870 | 1630
"""

INSTRUCTION = (
    "This is my current budget spreadsheet. Recreate it as my book. The expense "
    "figures are written as positive numbers but they are money going out. Rows "
    "named \'Total in\' and \'Total out\' are computed results, not lines.\n\n" + SHEET
)

SALARY, BONUS = Decimal("2800"), Decimal("2800")
MORTGAGE, UTILITIES = Decimal("1150"), Decimal("240")
NURSERY_BEFORE, NURSERY_AFTER = Decimal("420"), Decimal("480")
INSURANCE = Decimal("960")

#: Each month's net, straight off the sheet the user pasted.
NET = [
    SALARY - MORTGAGE - UTILITIES - NURSERY_BEFORE,              # Jan
    SALARY - MORTGAGE - NURSERY_BEFORE,                          # Feb
    SALARY - MORTGAGE - UTILITIES - NURSERY_BEFORE,              # Mar
    SALARY - MORTGAGE - NURSERY_BEFORE,                          # Apr
    SALARY - MORTGAGE - UTILITIES - NURSERY_BEFORE,              # May
    SALARY - MORTGAGE - NURSERY_BEFORE - INSURANCE,              # Jun
    SALARY - MORTGAGE - UTILITIES - NURSERY_AFTER,               # Jul
    SALARY - MORTGAGE - NURSERY_AFTER,                           # Aug
    SALARY - MORTGAGE - UTILITIES - NURSERY_AFTER,               # Sep
    SALARY - MORTGAGE - NURSERY_AFTER,                           # Oct
    SALARY - MORTGAGE - UTILITIES - NURSERY_AFTER,               # Nov
    SALARY + BONUS - MORTGAGE - NURSERY_AFTER,                   # Dec
]


def expected_closings() -> list[Decimal]:
    balance, series = OPENING, []
    for net in NET:
        balance += net
        series.append(balance)
    return series


async def test_the_sheet_becomes_the_right_book(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    assert await closings(live_session) == expected_closings()


async def test_the_annual_premium_is_charged_once(live_session):
    """The proto's lite failure: an annual line made open-ended, paid 7 times.

    June is the only month that carries the premium, so June's step down is
    960 deeper than May's and July's are — and no other month is.
    """
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    series = await closings(live_session)
    steps = [series[0] - OPENING] + [b - a for a, b in zip(series, series[1:], strict=False)]
    assert steps == NET
    assert steps.count(NET[5]) == 1


async def test_the_bimonthly_line_lands_on_the_odd_months(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    series = await closings(live_session)
    steps = [series[0] - OPENING] + [b - a for a, b in zip(series, series[1:], strict=False)]
    # February pays no utilities, January does; the difference is exactly one bill.
    assert steps[0] - steps[1] == -UTILITIES
    assert steps[2] - steps[3] == -UTILITIES


async def test_the_thirteenth_month_arrives_once(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    december = OPENING + sum(NET, Decimal(0))     # 18000.00
    assert await closing(live_session, "2026-12") == december
    assert (await state(live_session))["summary"]["total_inflow"]["exact"] == str(
        (12 * SALARY + BONUS).quantize(Decimal("0.0001"))
    )
