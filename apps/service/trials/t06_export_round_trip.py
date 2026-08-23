"""T06 — round-tripping our own export back into a book (proto T06).

    Tried: build the book, export the 12-month budget, reset, feed the export
    back, compare closings and item cells.

Proto T06's real finding was that the round trip is **format-limited before it
is model-limited**: the first failure was the export itself, which carried no
opening balance, so no model could recover it. The export now writes an
``Opening balance | meta`` row, and this trial is the check that it stays
recoverable.

The xlsx *parsing* pipeline is S5's (SPEC §7, T16). What S2 owns is the
model-behaviour half: given a tabular budget, does the model rebuild the same
book? So the export is rendered as text and pasted into a turn, exactly as a
user would (D-MLP-27).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import (
    author, closing, make_book, new_session, sheet_text, state, total_by_direction,
)

pytestmark = pytest.mark.live_model

OPENING = Decimal("150.00")
SCHOLARSHIP, RENT, PHONE = Decimal("700"), Decimal("455"), Decimal("20")

BUILD = (
    "My scholarship pays me 700 a month all year from January. My rent is 455 "
    "a month all year from January, and my phone is 20 a month all year from "
    "January."
)


async def test_the_export_rebuilds_the_same_book(live_session, live_app, mailer):
    await make_book(live_session, str(OPENING))
    await author(live_session, BUILD)

    original = [c["exact"] for c in (await state(live_session))["closing"]]
    exported = await sheet_text(live_session)
    assert "Opening balance" in exported

    # A second account, so the rebuild starts from an empty book of its own.
    rebuilt = await new_session(live_app, mailer, "roundtrip@example.com")
    try:
        await make_book(rebuilt, str(OPENING))
        await author(
            rebuilt,
            "Here is my budget, exported from another tool. Recreate it. Rows "
            "named 'Closing balance' are computed results, not lines.\n\n" + exported,
        )
        assert [c["exact"] for c in (await state(rebuilt))["closing"]] == original
        assert await total_by_direction(rebuilt, "in") == 12 * SCHOLARSHIP
        assert await total_by_direction(rebuilt, "out") == -12 * (RENT + PHONE)
        assert await closing(rebuilt, "2026-12") == OPENING + 12 * (
            SCHOLARSHIP - RENT - PHONE
        )
    finally:
        await rebuilt.aclose()
