"""T09 — quarterly and weekly recurrences (proto T09).

    Tried: one instruction with a 3-monthly premium and a weekly wage.
    Checks include the four-Monday January and the 52-week year.

Proto T09 is where the bracket-stack repair was earned: the model dropped one
closing brace in an otherwise perfect object and the retry reproduced it byte
for byte. With the transport hardened, the operation quality was never the
problem.

The engine counts the occurrences, and it counts them in the calendar rather
than by dividing: January 2026 has four Mondays from the 5th, and the year has
fifty-two of them.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, item_month, make_book, state, total_by_direction

pytestmark = pytest.mark.live_model

PREMIUM, WAGE = Decimal("300"), Decimal("120")
#: Mondays from 5 January 2026 to the end of the year, counted in the calendar.
MONDAYS_IN_JANUARY, MONDAYS_IN_2026 = 4, 52
#: March, June, September, December.
QUARTERS = 4

INSTRUCTION = (
    "I pay a 300 insurance premium every 3 months starting in March, and I "
    "earn 120 every week starting Monday 5 January."
)


async def test_the_quarterly_premium_lands_four_times(live_session):
    await make_book(live_session, "0.00")
    await author(live_session, INSTRUCTION)

    body = await state(live_session)
    months = [m[:7] for m in body["months"]]
    charged = sorted(
        month
        for series in body["items"]
        for month, cell in zip(months, series["cash"], strict=True)
        if Decimal(cell["exact"]) == -PREMIUM
    )
    assert charged == ["2026-03", "2026-06", "2026-09", "2026-12"]


async def test_the_weekly_wage_is_counted_in_the_calendar(live_session):
    await make_book(live_session, "0.00")
    await author(live_session, INSTRUCTION)

    assert await total_by_direction(live_session, "in") == MONDAYS_IN_2026 * WAGE
    assert await total_by_direction(live_session, "in", month="2026-01") == (
        MONDAYS_IN_JANUARY * WAGE
    )


async def test_the_year_closes_where_the_calendar_puts_it(live_session):
    await make_book(live_session, "0.00")
    await author(live_session, INSTRUCTION)

    december = MONDAYS_IN_2026 * WAGE - QUARTERS * PREMIUM     # 5040.00
    assert await closing(live_session, "2026-12") == december
