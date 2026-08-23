"""T11 — numeric Q&A, and the turn that must not write (proto T11).

    Tried: a book whose September closing is 1900, then two questions: afford
    1500 in September (yes, 400 left) and afford 2500 (no, 600 short).
    Result on lite: FAIL, dangerously — "yes" to both, no numbers, and two
    write operations on a question.

This is the trial that produced ADR-0029 and ADR-0028's model boundary. Two
things must hold at once, and they are independent:

* **the answer is right**, which needs the engine's own figures in front of the
  model — the proto had to add a results block before Q&A was safe at all;
* **the book does not move**, which is structural and holds even when the model
  misbehaves. The structural half is T14, which scripts a model that writes on
  every question; this trial is the live half.

The MLP improves on the proto here. R1 returns the closing balance for every
month, before and after the hypothetical, so the answer is quoted rather than
worked out: the model never subtracts 1500 from 1900.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from trials.live import author, closing, digits, make_book, state, turn

pytestmark = pytest.mark.live_model

OPENING, INCOME, RENT = Decimal("100.00"), Decimal("500"), Decimal("300")
#: 100 + 9 × 200.
SEPTEMBER = Decimal("1900.00")
AFFORDABLE, UNAFFORDABLE = Decimal("1500"), Decimal("2500")

BUILD = (
    "I earn 500 a month from January and my rent is 300 a month from January."
)


async def _book(client):
    await make_book(client, str(OPENING))
    await author(client, BUILD)
    assert await closing(client, "2026-09") == SEPTEMBER


async def test_the_affordable_purchase_is_answered_with_the_engines_figure(live_session):
    await _book(live_session)
    result = await turn(live_session, "can I afford a 1500 EUR laptop in September?")

    assert result["kind"] == "answer", result
    assert result["receipts"], "the answer must rest on a read the engine ran"
    # 1900 − 1500 = 400, and the model quotes 400 rather than working it out.
    assert "400" in digits(result["reply"]), result["reply"]


async def test_the_unaffordable_purchase_is_refused_with_the_engines_figure(live_session):
    await _book(live_session)
    result = await turn(live_session, "can I afford a 2500 EUR laptop in September?")

    assert result["kind"] == "answer", result
    assert "600" in digits(result["reply"]), result["reply"]


async def test_neither_question_moves_the_book(live_session):
    await _book(live_session)
    before = await state(live_session)

    for amount in (AFFORDABLE, UNAFFORDABLE):
        await turn(live_session, f"can I afford a {amount:.0f} EUR laptop in September?")

    after = await state(live_session)
    assert after["revision"] == before["revision"]
    assert after["items"] == before["items"]
    assert after["closing"] == before["closing"]
    assert await closing(live_session, "2026-09") == SEPTEMBER


async def test_a_hypothetical_answer_carries_the_what_if_stamp(live_session):
    """SPEC §2.4: a figure computed on a throwaway overlay is not base's."""
    await _book(live_session)
    result = await turn(live_session, "what happens if I spend 1500 in September?")

    assert result["what_if"]["stamped"] is True
    assert result["what_if"]["reason"] == "overlay"
