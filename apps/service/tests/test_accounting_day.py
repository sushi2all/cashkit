"""The book's accounting day (migration 0003, SPEC §6-S15).

The engine has no "March": a segment starts on a date, and the day sets the
recurrence phase, the escalation anniversary and — through settlement — which
month the cash lands in. Before this, the assistant picked a day whenever the
user named none, and picked it silently.

Now the model may write the month alone and the host fills the day from the
book. These tests hold the two halves of that: the expansion itself, and the
setting that feeds it.
"""

from __future__ import annotations

import pytest

from cashkit_service.agent.guard import guard
from cashkit_service.ops.months import expand_months, is_month, parse_accounting_day

RENT_NO_DAY = {
    "op": "add_item", "id": "rent", "direction": "out", "amount": "-950.00",
    "recurrence": "1m", "start": "2026-04",
}


# --- the expansion ---------------------------------------------------------- #


def test_a_month_is_recognised_and_a_date_is_not():
    assert is_month("2026-04")
    assert not is_month("2026-04-01")
    assert not is_month("2026-13")
    assert not is_month(None)
    assert not is_month(4)


def test_a_month_takes_the_book_s_day():
    assert expand_months(RENT_NO_DAY, 27)["start"] == "2026-04-27"


def test_a_full_date_is_never_touched():
    """A day the user actually said outranks the book's default."""
    stated = dict(RENT_NO_DAY, start="2026-04-05")
    assert expand_months(stated, 27)["start"] == "2026-04-05"


def test_an_exclusive_end_takes_the_first_and_not_the_default():
    """`end` is a boundary, not an occurrence: 2026-07 means "through June"."""
    bounded = dict(RENT_NO_DAY, end="2026-07")
    expanded = expand_months(bounded, 27)
    assert expanded["end"] == "2026-07-01"
    assert expanded["start"] == "2026-04-27"


def test_the_day_can_never_leave_the_1_to_28_window():
    """A stored day past 28 would mean a different day in February."""
    assert expand_months(RENT_NO_DAY, 31)["start"] == "2026-04-28"
    assert expand_months(RENT_NO_DAY, 0)["start"] == "2026-04-01"


def test_the_operation_is_returned_unchanged_when_nothing_is_a_month():
    operation = dict(RENT_NO_DAY, start="2026-04-05")
    assert expand_months(operation, 27) == operation


# --- end of month ----------------------------------------------------------- #


def test_end_of_month_dates_the_first_occurrence_on_that_month_s_last_day():
    assert expand_months(RENT_NO_DAY, "eom")["start"] == "2026-04-30"
    assert expand_months(dict(RENT_NO_DAY, start="2026-02"), "eom")["start"] == "2026-02-28"
    assert expand_months(dict(RENT_NO_DAY, start="2028-02"), "eom")["start"] == "2028-02-29"
    assert expand_months(dict(RENT_NO_DAY, start="2026-01"), "eom")["start"] == "2026-01-31"


def test_end_of_month_stamps_the_engine_s_own_anchor():
    """A date alone cannot mean "month end": 30 April steps to 30 May, not 31."""
    assert expand_months(RENT_NO_DAY, "eom")["anchor"] == "eom"


def test_a_fixed_day_stamps_no_anchor():
    assert "anchor" not in expand_months(RENT_NO_DAY, 27)


def test_a_start_the_user_named_keeps_its_own_meaning_under_eom():
    """"From 31 January" is theirs; the host does not turn it into a rule."""
    stated = dict(RENT_NO_DAY, start="2026-01-31")
    expanded = expand_months(stated, "eom")
    assert expanded["start"] == "2026-01-31"
    assert "anchor" not in expanded


def test_an_exclusive_end_is_still_the_first_under_eom():
    bounded = dict(RENT_NO_DAY, end="2026-07")
    assert expand_months(bounded, "eom")["end"] == "2026-07-01"


# --- the other date slots ---------------------------------------------------- #


def test_a_split_takes_the_first_so_the_month_is_not_paid_twice():
    """Rent on the 5th, day 27: a split on the 27th would pay June at both amounts."""
    split = {"op": "set_amount", "id": "rent", "amount": "-1000.00", "from_date": "2026-06"}
    assert expand_months(split, 27)["from_date"] == "2026-06-01"


def test_a_cutover_takes_the_first_so_nothing_in_its_month_disappears():
    assert expand_months({"op": "set_cutover", "date": "2026-06"}, 27)["date"] == "2026-06-01"


def test_a_record_with_no_day_is_refused_and_never_dated():
    """An event is something that happens: the host does not invent its day."""
    turn = guard([{"op": "add_event", "id": "bonus", "amount": "500.00", "date": "2026-06"}], 27)
    assert not turn.mutations
    assert "no day" in turn.diagnostics[0].message


@pytest.mark.parametrize("recurrence", ["1w", "2w", "1d"])
def test_a_weekly_line_never_gets_the_month_end_anchor(recurrence):
    """Weekly under eom would pay four times on each month end."""
    assert "anchor" not in expand_months(dict(RENT_NO_DAY, recurrence=recurrence), "eom")


@pytest.mark.parametrize("recurrence", ["1m", "3m", "1y"])
def test_monthly_and_longer_lines_keep_the_anchor(recurrence):
    assert expand_months(dict(RENT_NO_DAY, recurrence=recurrence), "eom")["anchor"] == "eom"


def test_an_anchor_the_model_sends_is_dropped():
    """The card would show the 5th while the engine paid on month ends."""
    sent = dict(RENT_NO_DAY, start="2026-04-05", anchor="eom")
    turn = guard([sent], "eom")
    assert turn.mutations[0].get("anchor") is None
    assert turn.mutations[0]["start"] == "2026-04-05"


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("1", 1), ("27", 27), ("eom", "eom"), ("EOM", "eom"), ("nonsense", 1), (None, 1), ("31", 28)],
)
def test_the_stored_setting_is_read_back_as_a_day_or_as_eom(stored, expected):
    assert parse_accounting_day(stored) == expected


# --- the setting ------------------------------------------------------------ #


async def test_a_new_book_counts_from_the_first(book_client):
    state = (await book_client.get("/book/state")).json()
    assert state["book"]["accounting_day"] == 1


async def test_the_day_is_set_and_read_back(book_client):
    response = await book_client.post("/book/preferences", json={"accounting_day": 27})
    assert response.status_code == 200, response.text
    assert response.json()["accounting_day"] == 27

    state = (await book_client.get("/book/state")).json()
    assert state["book"]["accounting_day"] == 27


@pytest.mark.parametrize("day", [0, 29, 31, -1, "last", "31"])
async def test_a_day_outside_the_window_is_refused(book_client, day):
    response = await book_client.post("/book/preferences", json={"accounting_day": day})
    assert response.status_code == 422


async def test_end_of_month_is_set_and_read_back(book_client):
    response = await book_client.post("/book/preferences", json={"accounting_day": "eom"})
    assert response.status_code == 200, response.text
    assert response.json()["accounting_day"] == "eom"

    state = (await book_client.get("/book/state")).json()
    assert state["book"]["accounting_day"] == "eom"


# --- the two halves together ------------------------------------------------ #


async def test_a_turn_that_names_no_day_lands_on_the_book_s_day(book_client, model_script):
    await book_client.post("/book/preferences", json={"accounting_day": 27})
    model_script.append({
        "kind": "answer",
        "reply": "Rent, monthly from April.",
        "intents": [RENT_NO_DAY],
    })

    response = await book_client.post("/turns", json={"text": "rent is 950 a month from April"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "proposal", body
    operations = body["proposal"]["operations"]
    assert operations[0]["start"] == "2026-04-27"


async def test_the_day_the_user_says_wins_over_the_setting(book_client, model_script):
    await book_client.post("/book/preferences", json={"accounting_day": 27})
    model_script.append({
        "kind": "answer",
        "reply": "Rent, monthly from 5 April.",
        "intents": [dict(RENT_NO_DAY, start="2026-04-05")],
    })

    response = await book_client.post("/turns", json={"text": "rent is 950 on the 5th"})
    body = response.json()
    assert body["proposal"]["operations"][0]["start"] == "2026-04-05"


async def test_the_model_reads_the_day_out_of_the_snapshot(book_client, model_script, transport):
    """It has to: its reply says which date the card will carry."""
    await book_client.post("/book/preferences", json={"accounting_day": 15})
    model_script.append({"kind": "answer", "reply": "ok", "intents": []})
    await book_client.post("/turns", json={"text": "what do I pay every month?"})

    sent = transport.calls[0]["messages"]
    assert any("accounting_day" in message["content"] and "15" in message["content"]
               for message in sent)
    snapshot = next(m["content"] for m in sent if "accounting_day" in m["content"])
    assert '"accounting_day":15' in snapshot.replace(", ", ",").replace(": ", ":")


async def test_a_turn_under_end_of_month_lands_on_the_last_day_and_stays_there(
    book_client, model_script
):
    """The engine's anchor, not a clamped day: every month ends where it ends."""
    await book_client.post("/book/preferences", json={"accounting_day": "eom"})
    model_script.append({
        "kind": "answer",
        "reply": "Rent, monthly from April.",
        "intents": [RENT_NO_DAY],
    })

    response = await book_client.post("/turns", json={"text": "rent is 950 a month from April"})
    body = response.json()
    operation = body["proposal"]["operations"][0]
    assert operation["start"] == "2026-04-30"
    assert operation["anchor"] == "eom"

    applied = await book_client.post(
        f"/proposals/{body['proposal']['id']}", json={"action": "accept"}
    )
    assert applied.status_code == 200, applied.text


def test_the_anchor_is_what_keeps_a_line_on_month_ends():
    """The engine's own behaviour, which is why the anchor exists at all.

    A fixed day cannot express this: 30 gives 30 May, and 31 gives 30 April and
    then stays on the 30th for every month after it.
    """
    import datetime as _dt

    from cashkit.engine.calendars import occurrence_dates
    from cashkit.model import Grain, Recurrence

    monthly = dict(
        segment_start=_dt.date(2026, 4, 30),
        segment_end=None,
        horizon_start=_dt.date(2026, 1, 1),
        horizon_end=_dt.date(2026, 8, 1),
    )
    eom = occurrence_dates(Recurrence(every=1, unit=Grain.MONTH, anchor="eom"), **monthly)
    assert [d.isoformat() for d in eom] == ["2026-04-30", "2026-05-31", "2026-06-30", "2026-07-31"]

    fixed = occurrence_dates(Recurrence(every=1, unit=Grain.MONTH), **monthly)
    assert [d.isoformat() for d in fixed] == ["2026-04-30", "2026-05-30", "2026-06-30", "2026-07-30"]
