"""T10 — the whole user path, end to end (proto T10).

    Tried: drive the page — chat a budget, watch the table render, switch the
    model, upload a workbook.

Proto T10 drove a browser because the proto had a page. At S2 there is no
interface yet: the browser E2E is S3's gate (Playwright web, Maestro iOS). What
exists at S2 is the path the interface will drive, so this trial walks it —
sign in, create the book, say what changed, confirm the card, read the
forecast, tap a number for the trace, save.

It is the trial that would catch a step that works alone and not in sequence.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import apply, closing, make_book, state, turn

pytestmark = pytest.mark.live_model

OPENING = Decimal("2000.00")
SALARY, RENT = Decimal("2400"), Decimal("900")


async def test_a_user_can_go_from_nothing_to_a_saved_forecast(live_session):
    # 1. a book.
    await make_book(live_session, str(OPENING))
    assert (await state(live_session))["dirty"] is False

    # 2. say what changed; get a card, not a change.
    result = await turn(
        live_session,
        "I earn 2400 a month from January and my rent is 900 a month from January.",
    )
    assert result["kind"] == "proposal"
    assert result["what_if"] == {"stamped": True, "reason": "pending", "scenario": "base"}
    before = await state(live_session)
    assert before["items"] == []

    # 3. confirm it. Now the book has changed, and it is uncommitted.
    applied = await apply(live_session, result)
    assert applied["kind"] == "applied"
    body = await state(live_session)
    assert len(body["items"]) == 2
    assert body["dirty"] is True
    assert await closing(live_session, "2026-12") == OPENING + 12 * (SALARY - RENT)

    # 4. the forecast the interface renders.
    forecast = (await live_session.get("/book/forecast")).json()
    assert len(forecast["rows"]) == 12
    assert Decimal(forecast["rows"][0]["closing"]["exact"]) == OPENING + SALARY - RENT

    # 5. tap a number: the trace explains it.
    item = body["items"][0]["id"]
    trace = await live_session.get(
        "/book/trace", params={"item": item, "period": "2026-05-01"}
    )
    assert trace.status_code == 200, trace.text
    assert trace.json()["trace"]["steps"]

    # 6. ask a question about it, and get an answer with no change.
    revision_before = (await state(live_session))["revision"]
    answer = await turn(live_session, "what is the lowest my balance gets this year?")
    assert answer["kind"] == "answer", answer
    assert answer["reply"]
    assert (await state(live_session))["revision"] == revision_before
    assert (await state(live_session))["dirty"] is True

    # 7. save. The revision moves, and the book is clean.
    saved = await live_session.post("/book/save", json={"message": "first budget"})
    assert saved.status_code == 200, saved.text
    after = await state(live_session)
    assert after["dirty"] is False
    assert after["revision"] != revision_before

    # 8. the history the Settings screen lists.
    history = (await live_session.get("/book/history")).json()
    assert history["revisions"][0]["message"] == "first budget"
