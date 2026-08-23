"""T08 — payment terms: an invoice paid net 45 (proto T08).

    Tried: a monthly invoice paid 45 days later plus an immediate monthly
    cost. Checks separate accrual from cash, and December's invoice must
    settle outside the horizon.

Accrual against cash is the CashKit feature a spreadsheet cannot express, and
proto T08 is where the JSON transport hardening was earned: four runs to green,
every fix on the harness side rather than the model's.

The proto's 50/50 split half is not in the v0 intent grammar — M1 carries one
settlement term, not a schedule of them — so this trial covers the net-N half.
The split stays a host-side or SDK concern (recorded in the handoff).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, item_month, make_book, state

pytestmark = pytest.mark.live_model

INVOICE, SUBSCRIPTION = Decimal("6000"), Decimal("300")
INSTRUCTION = (
    "I invoice 6000 a month starting in January and my client pays 45 days "
    "later. I also pay 300 a month for subscriptions from January, straight "
    "away."
)


def _invoice_line(body: dict) -> dict:
    return next(
        series for series in body["items"]
        if any(Decimal(c["exact"]) == INVOICE for c in series["accrual"])
    )


async def test_the_invoice_accrues_in_january_and_pays_in_february(live_session):
    await make_book(live_session, "0.00")
    await author(live_session, INSTRUCTION)

    body = await state(live_session)
    months = [m[:7] for m in body["months"]]
    invoice = _invoice_line(body)

    assert Decimal(invoice["accrual"][months.index("2026-01")]["exact"]) == INVOICE
    # 1 January plus 45 days is 15 February, so January's cash column is empty.
    assert Decimal(invoice["cash"][months.index("2026-01")]["exact"]) == 0
    assert Decimal(invoice["cash"][months.index("2026-02")]["exact"]) == INVOICE


async def test_the_december_invoice_settles_outside_the_horizon(live_session):
    await make_book(live_session, "0.00")
    await author(live_session, INSTRUCTION)

    # January to November settle inside the year; December's lands in 2027.
    december = 11 * INVOICE - 12 * SUBSCRIPTION      # 62400.00
    assert await closing(live_session, "2026-12") == december


async def test_january_is_only_the_subscription(live_session):
    await make_book(live_session, "0.00")
    await author(live_session, INSTRUCTION)
    assert await closing(live_session, "2026-01") == -SUBSCRIPTION
