"""T01 — a whole base budget from one instruction (proto T01).

    Tried: one chat turn with the scenario-01 base facts: income lines,
    expense lines, two windowed lines. Checks: item cells + closing Jan +
    closing Dec, computed independently.

The baseline translation task: flat lines and date windows, no formulas. The
window is where models slip — an end date is exclusive, and an off-by-one there
pays a line one month too long (proto T06 caught lite doing exactly that).

Every expected figure below is arithmetic over the sentence the user said, in
Decimal, so the assertion is an independent check rather than a copy of the
engine's answer.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, item_ids, make_book, state, total_by_direction

pytestmark = pytest.mark.live_model

OPENING = Decimal("150.00")
INSTRUCTION = (
    "I get 700 a month from my scholarship starting in January, and it stops "
    "after June. My parents send me 250 a month all year. My rent is 455 a "
    "month, my phone is 20 a month, and groceries are 260 a month, all from "
    "January. I also put 100 a month into savings from January, but I stop "
    "saving after June."
)

SCHOLARSHIP, PARENTS = Decimal("700"), Decimal("250")
RENT, PHONE, GROCERIES, SAVINGS = Decimal("455"), Decimal("20"), Decimal("260"), Decimal("100")

#: What each month costs, and earns, on the user's own words.
ALL_YEAR_OUT = RENT + PHONE + GROCERIES          # 735
FIRST_HALF_OUT = ALL_YEAR_OUT + SAVINGS          # 835
FIRST_HALF_IN = SCHOLARSHIP + PARENTS            # 950
SECOND_HALF_IN = PARENTS                         # 250


async def test_the_budget_lands_month_by_month(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    # Six lines authored, none invented.
    assert len(await item_ids(live_session)) == 6

    january = OPENING + FIRST_HALF_IN - FIRST_HALF_OUT           # 265.00
    assert await closing(live_session, "2026-01") == january

    june = OPENING + 6 * (FIRST_HALF_IN - FIRST_HALF_OUT)        # 840.00
    assert await closing(live_session, "2026-06") == june

    # July is the test of the exclusive end: the scholarship and the savings
    # both stop after June, so neither may appear.
    july = june + SECOND_HALF_IN - ALL_YEAR_OUT                  # 355.00
    assert await closing(live_session, "2026-07") == july

    december = june + 6 * (SECOND_HALF_IN - ALL_YEAR_OUT)        # -2070.00
    assert await closing(live_session, "2026-12") == december


async def test_the_windowed_lines_stop_where_the_user_said(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    # Six months of scholarship and six of savings, not seven and not twelve.
    assert await total_by_direction(live_session, "in") == 6 * SCHOLARSHIP + 12 * PARENTS
    assert await total_by_direction(live_session, "out") == -(
        12 * ALL_YEAR_OUT + 6 * SAVINGS
    )
    assert await total_by_direction(live_session, "in", month="2026-07") == PARENTS


async def test_the_book_carries_its_provenance(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, INSTRUCTION)

    body = await state(live_session)
    assert body["as_of"] == "2026-03-17"
    assert body["engine_version"]
    assert body["revision"]
