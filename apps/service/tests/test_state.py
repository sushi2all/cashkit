"""``GET /book/state`` — payload shape and the SPEC §3 envelope."""

from __future__ import annotations


async def test_state_carries_the_provenance_envelope(book_client):
    body = (await book_client.get("/book/state")).json()
    assert body["as_of"] == "2026-03-17"          # the frozen clock, host-filled
    assert body["scenario"] == "base"
    assert body["revision"]
    assert body["engine_version"] == "1"
    assert body["request_id"]


async def test_an_empty_base_book_is_not_stamped_what_if(book_client):
    body = (await book_client.get("/book/state")).json()
    assert body["dirty"] is False
    assert body["what_if"] == {"stamped": False, "reason": None, "scenario": None}


async def test_state_reports_the_book_as_authored(book_client):
    body = (await book_client.get("/book/state")).json()
    assert body["book"]["opening_balance"] == {"exact": "2500.0000", "display": "2500.00"}
    assert body["book"]["grain"] == "month"
    assert body["book"]["currency"] == "EUR"
    assert len(body["months"]) == 12
    assert body["months"][0] == "2026-01-01"


async def test_an_empty_book_holds_its_opening_balance_all_year(book_client):
    body = (await book_client.get("/book/state")).json()
    assert body["items"] == []
    assert {m["exact"] for m in body["closing"]} == {"2500.0000"}
    assert body["summary"]["closing_balance"]["exact"] == "2500.0000"


async def test_warnings_are_standing_and_structural(book_client):
    body = (await book_client.get("/book/state")).json()
    assert body["warnings"]["negative_months"] == []
    assert body["warnings"]["min_cash"] == {"exact": "2500.0000", "display": "2500.00"}
    assert body["warnings"]["min_cash_period"] == "2026-01-01"


async def test_an_unknown_scenario_is_404(book_client):
    response = await book_client.get("/book/state", params={"scenario": "nope"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NO_SCENARIO"


async def test_state_needs_a_book(auth_client):
    response = await auth_client.get("/book/state")
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "NO_BOOK"
