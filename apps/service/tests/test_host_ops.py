"""The five host ops (SPEC §2.5, D-MLP-03)."""

from __future__ import annotations

import pytest


async def propose(client, op, **kw):
    response = await client.post(
        "/book/edits", json={"origin": kw.pop("origin", "settings"), "ops": [op], **kw}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def apply(client, op, **kw):
    body = await propose(client, op, **kw)
    assert body["kind"] == "proposal", body
    response = await client.post(f"/proposals/{body['proposal']['id']}", json={"action": "accept"})
    assert response.status_code == 200, response.text
    return response.json()


async def test_set_horizon(seeded_client):
    await apply(seeded_client, {"op": "set_horizon", "start": "2026-01-01", "end": "2026-07-01"})
    state = (await seeded_client.get("/book/state")).json()
    assert state["book"]["horizon_end"] == "2026-07-01"
    assert len(state["months"]) == 6


async def test_a_backwards_horizon_is_refused_with_a_reason(seeded_client):
    body = await propose(
        seeded_client, {"op": "set_horizon", "start": "2026-07-01", "end": "2026-01-01"}
    )
    diagnostics = body["proposal"]["diagnostics"]
    assert diagnostics and "end after it starts" in diagnostics[0]["message"]


async def test_set_opening_balance(seeded_client):
    await apply(seeded_client, {"op": "set_opening_balance", "amount": "5000.00"})
    state = (await seeded_client.get("/book/state")).json()
    assert state["book"]["opening_balance"] == {"exact": "5000.0000", "display": "5000.00"}


async def test_remove_event_takes_a_forecast_out(seeded_client):
    before = (await seeded_client.get("/book/events")).json()["events"]
    forecast = next(e for e in before if e["status"] == "forecast")
    await apply(seeded_client, {"op": "remove_event", "event": forecast["id"], "note": "cancelled"})

    after = (await seeded_client.get("/book/events")).json()["events"]
    assert forecast["id"] not in {e["id"] for e in after}


async def test_remove_event_is_refused_on_an_actual(seeded_client):
    """SPEC §2.5: refused on actuals — corrections only."""
    events = (await seeded_client.get("/book/events")).json()["events"]
    actual = next(e for e in events if e["status"] == "actual")

    body = await propose(seeded_client, {"op": "remove_event", "event": actual["id"]})
    diagnostics = body["proposal"]["diagnostics"]
    assert diagnostics
    assert "recorded actual" in diagnostics[0]["message"]
    # The refusal names the path the user actually wants, and does not advise.
    assert "correction" in diagnostics[0]["suggested_fix"]

    after = (await seeded_client.get("/book/events")).json()["events"]
    assert actual["id"] in {e["id"] for e in after}


async def test_edit_schedule_date_adds_a_date(seeded_client):
    await apply(
        seeded_client,
        {"op": "edit_schedule_date", "item": "insurance", "action": "add",
         "date": "2026-11-01", "amount": "-207.77"},
    )
    trace = (await seeded_client.get(
        "/book/trace", params={"item": "insurance", "period": "2026-11-01"}
    )).json()
    assert trace["trace"]["value"]["exact"] == "-207.7700"


async def test_edit_schedule_date_removes_a_date(seeded_client):
    await apply(
        seeded_client,
        {"op": "edit_schedule_date", "item": "insurance", "action": "remove", "date": "2026-08-01"},
    )
    trace = (await seeded_client.get(
        "/book/trace", params={"item": "insurance", "period": "2026-08-01"}
    )).json()
    assert trace["trace"]["value"]["exact"] == "0.0000"


async def test_edit_schedule_date_is_refused_on_a_rule_backed_item(seeded_client):
    body = await propose(
        seeded_client,
        {"op": "edit_schedule_date", "item": "rent", "action": "remove", "date": "2026-08-01"},
    )
    diagnostics = body["proposal"]["diagnostics"]
    assert diagnostics and "no explicit dates" in diagnostics[0]["message"]


async def test_removing_the_last_date_is_refused(seeded_client):
    await apply(
        seeded_client,
        {"op": "edit_schedule_date", "item": "insurance", "action": "remove", "date": "2026-02-01"},
    )
    body = await propose(
        seeded_client,
        {"op": "edit_schedule_date", "item": "insurance", "action": "remove", "date": "2026-08-01"},
    )
    diagnostics = body["proposal"]["diagnostics"]
    assert diagnostics and "no dates at all" in diagnostics[0]["message"]


async def test_host_ops_are_enumerated_and_typed(seeded_client):
    """An operation outside the grammar is refused by the schema, not guessed."""
    response = await seeded_client.post(
        "/book/edits",
        json={"origin": "settings", "ops": [{"op": "delete_everything"}]},
    )
    assert response.status_code == 422


def test_the_host_op_set_is_exactly_the_five_of_the_spec():
    from cashkit_service.ops.schema import HOST_OPS

    assert HOST_OPS == {
        "set_horizon", "set_opening_balance", "remove_event",
        "edit_schedule_date", "record_actual",
    }
