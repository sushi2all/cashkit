"""T02 — editing a line of an existing book (proto T02).

    Tried: turn 1 builds a book; turn 2 rewrites one line from a date.
    Checks: the changed cells, the untouched line, and the closing balance.

Proto T02's finding was that re-authoring by id is a good edit primitive for a
model. The MLP grammar sharpens it: M2 ``set_amount`` with ``from_date``
**splits** the segment rather than overwriting it, so the months before the
change keep the old amount. That is the property under test — an edit that
rewrote history would be a silent restatement of what the user used to pay.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, item_ids, item_month, make_book, total_by_direction

pytestmark = pytest.mark.live_model

OPENING = Decimal("150.00")
SCHOLARSHIP, RENT, NEW_RENT = Decimal("700"), Decimal("455"), Decimal("495")

BUILD = (
    "My scholarship pays me 700 a month all year from January, and my rent is "
    "455 a month all year from January."
)
EDIT = "From July my rent goes up to 495 a month."


async def test_the_edit_changes_the_future_and_keeps_the_past(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)
    before = await closing(live_session, "2026-12")
    assert before == OPENING + 12 * (SCHOLARSHIP - RENT)

    await author(live_session, EDIT)

    # No new line: the change is to the line that exists.
    assert len(await item_ids(live_session)) == 2

    rent = next(i for i in await item_ids(live_session) if "rent" in i.lower())
    assert await item_month(live_session, rent, "2026-06") == -RENT
    assert await item_month(live_session, rent, "2026-07") == -NEW_RENT
    assert await item_month(live_session, rent, "2026-12") == -NEW_RENT

    december = OPENING + 12 * SCHOLARSHIP - 6 * RENT - 6 * NEW_RENT   # 2850.00
    assert await closing(live_session, "2026-12") == december


async def test_the_other_line_is_untouched(live_session):
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)
    await author(live_session, EDIT)

    assert await total_by_direction(live_session, "in") == 12 * SCHOLARSHIP
