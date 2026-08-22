"""T18 — the record-actual discriminator (SPEC §10, §5-F5).

    T18 record-actual discriminator (context flag + date rule; future date →
    forecast; ambiguous → clarification)

The rule, verbatim (SPEC §5-F5): *an M5 intent maps to* ``status="actual"`` *if
and only if the turn arrived with* ``context: "actuals_record"`` *(set by the
client only on the Actuals record flow, §6-S7) AND the event date is ≤*
``as_of``. *A future-dated entry on that flow, or any M5 from any other
surface, stays* ``forecast``. *If the flow is* ``actuals_record`` *and the date
is ambiguous or missing, the turn returns* ``kind: clarification`` *— never a
guess.*

The model never chooses status. S1 tests the rule through ``POST /book/edits``
with the record-actual host op; S2 wires the turn path to the same function.
Zero model calls.
"""

from __future__ import annotations

import datetime as _dt

import pytest

from cashkit_service.ops.applier import discriminate_event_status

AS_OF = _dt.date(2026, 3, 17)  # the frozen clock
YESTERDAY = "2026-03-16"
TODAY = "2026-03-17"
TOMORROW = "2026-03-18"


async def _edit(client, op, **kw):
    return await client.post(
        "/book/edits", json={"origin": kw.pop("origin", "cell_edit"), "ops": [op], **kw}
    )


async def _record(client, op, **kw):
    response = await _edit(client, op, **kw)
    assert response.status_code == 201, response.text
    body = response.json()
    if body["kind"] != "proposal":
        return body, None
    accepted = await client.post(
        f"/proposals/{body['proposal']['id']}", json={"action": "accept"}
    )
    assert accepted.status_code == 200, accepted.text
    return body, accepted


async def _status_of(client, note: str) -> str:
    events = (await client.get("/book/events")).json()["events"]
    return next(e for e in events if e["note"] == note)["status"]


# --- the rule as a function ----------------------------------------------- #


@pytest.mark.parametrize(
    ("op", "context", "when", "expected"),
    [
        # On the flow, in the past or today: an actual.
        ({"op": "record_actual"}, None, YESTERDAY, "actual"),
        ({"op": "record_actual"}, None, TODAY, "actual"),
        ({"op": "add_event"}, "actuals_record", YESTERDAY, "actual"),
        ({"op": "add_event"}, "actuals_record", TODAY, "actual"),
        # On the flow but future-dated: still a forecast.
        ({"op": "record_actual"}, None, TOMORROW, "forecast"),
        ({"op": "add_event"}, "actuals_record", TOMORROW, "forecast"),
        # Off the flow, whatever the date: always a forecast.
        ({"op": "add_event"}, None, YESTERDAY, "forecast"),
        ({"op": "add_event"}, None, TODAY, "forecast"),
        ({"op": "add_event"}, None, TOMORROW, "forecast"),
        ({"op": "add_event"}, "onboarding", YESTERDAY, "forecast"),
    ],
)
def test_the_rule_is_exactly_the_spec(op, context, when, expected):
    decision = discriminate_event_status({**op, "date": when}, context=context, as_of=AS_OF)
    assert decision.status == expected
    assert decision.clarification is None


@pytest.mark.parametrize("op", [{"op": "record_actual"}, {"op": "add_event"}])
def test_a_missing_date_on_the_flow_is_a_clarification_never_a_guess(op):
    context = None if op["op"] == "record_actual" else "actuals_record"
    decision = discriminate_event_status({**op, "date": None}, context=context, as_of=AS_OF)
    assert decision.status is None
    assert decision.clarification
    # SPEC §5-F1 voice rule: succinct, says what is needed, no apology.
    assert len(decision.clarification.split(".")) <= 3
    assert "sorry" not in decision.clarification.lower()


def test_a_missing_date_off_the_flow_is_not_a_clarification():
    """Only the record-actual flow needs the date to decide; a plan entry does not."""
    decision = discriminate_event_status({"op": "add_event", "date": None}, context=None, as_of=AS_OF)
    assert decision.status == "forecast"


# --- the rule through the API --------------------------------------------- #


async def test_a_past_dated_record_actual_becomes_an_actual(seeded_client):
    await _record(
        seeded_client,
        {"op": "record_actual", "date": YESTERDAY, "amount": "-42.10", "direction": "out",
         "note": "coffee beans"},
    )
    assert await _status_of(seeded_client, "coffee beans") == "actual"


async def test_a_record_actual_dated_today_becomes_an_actual(seeded_client):
    await _record(
        seeded_client,
        {"op": "record_actual", "date": TODAY, "amount": "-9.99", "direction": "out",
         "note": "today's bus fare"},
    )
    assert await _status_of(seeded_client, "today's bus fare") == "actual"


async def test_a_future_dated_record_actual_stays_a_forecast(seeded_client):
    await _record(
        seeded_client,
        {"op": "record_actual", "date": TOMORROW, "amount": "-60.00", "direction": "out",
         "note": "tomorrow's ticket"},
    )
    assert await _status_of(seeded_client, "tomorrow's ticket") == "forecast"


async def test_an_add_event_off_the_flow_stays_a_forecast(seeded_client):
    """The same date, the same amount — only the surface differs."""
    await _record(
        seeded_client,
        {"op": "add_event", "date": YESTERDAY, "amount": "-42.10", "direction": "out",
         "note": "same day, planning surface"},
    )
    assert await _status_of(seeded_client, "same day, planning surface") == "forecast"


async def test_the_context_flag_carries_the_flag_on_add_event(seeded_client):
    await _record(
        seeded_client,
        {"op": "add_event", "date": YESTERDAY, "amount": "-15.00", "direction": "out",
         "note": "via the actuals flow"},
        context="actuals_record",
    )
    assert await _status_of(seeded_client, "via the actuals flow") == "actual"


async def test_a_dateless_record_actual_asks_and_stores_nothing(seeded_client, database):
    import sqlalchemy as sa

    from cashkit_service.db import proposals as proposals_table

    before = (await seeded_client.get("/book/events")).json()["events"]
    response = await _edit(
        seeded_client,
        {"op": "record_actual", "amount": "-30.00", "direction": "out", "note": "no date given"},
    )
    body = response.json()
    assert body["kind"] == "clarification"
    assert body["clarification"]
    assert body["proposal"] is None

    # A question is not a pending change: nothing was stored to confirm.
    async with database.connect() as conn:
        count = (
            await conn.execute(sa.select(sa.func.count()).select_from(proposals_table))
        ).scalar_one()
    assert count == 0
    after = (await seeded_client.get("/book/events")).json()["events"]
    assert [e["id"] for e in after] == [e["id"] for e in before]


async def test_recording_an_actual_still_needs_a_proposal(seeded_client):
    """The record-actual channel is a host op, not a shortcut around ADR-0029."""
    before = (await seeded_client.get("/book/events")).json()["events"]
    response = await _edit(
        seeded_client,
        {"op": "record_actual", "date": YESTERDAY, "amount": "-5.00", "direction": "out",
         "note": "unconfirmed"},
    )
    assert response.json()["proposal"]["status"] == "pending"
    after = (await seeded_client.get("/book/events")).json()["events"]
    assert [e["id"] for e in after] == [e["id"] for e in before]


async def test_status_is_not_a_slot_a_caller_can_set(seeded_client):
    """No surface lets anyone declare that something happened."""
    response = await _edit(
        seeded_client,
        {"op": "add_event", "date": TOMORROW, "amount": "-5.00", "direction": "out",
         "status": "actual"},
    )
    assert response.status_code == 422, "the grammar has no status slot"


async def test_a_recorded_actual_shows_up_in_reconcile(seeded_client):
    before = (await seeded_client.get("/book/reconcile")).json()["reconciliation"]
    await _record(
        seeded_client,
        {"op": "record_actual", "date": YESTERDAY, "amount": "-100.00", "direction": "out",
         "item": "rent", "note": "part payment"},
    )
    after = (await seeded_client.get("/book/reconcile")).json()["reconciliation"]
    assert after["actual_events"] == before["actual_events"] + 1
