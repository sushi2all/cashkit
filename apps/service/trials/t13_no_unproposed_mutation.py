"""T13 — mutation requires an applied proposal (SPEC §10, ADR-0029).

    T13 mutation-requires-applied-proposal — attempt every bypass path
    INCLUDING the §2.5 staleness paths (accept after save/discard/activate/
    another-accept must supersede, never apply blind)

This is the hard invariant of the whole product: **no path mutates a book
without a stored, user-accepted proposal.** The trial attacks it from every
side, deterministic and model-originated alike.

S1 wrote the deterministic half. S2 added ``POST /turns`` and re-ran it with
turn-originated proposals in scope: a card the model produced is subject to
exactly the same confirmation, expiry, ownership and staleness rules as a card
a button produced, because it goes through the same store. The model calls here
are scripted, so the trial asserts the invariant rather than the weather.
"""

from __future__ import annotations

import sqlalchemy as sa

from cashkit_service.db import proposals as proposals_table

GYM = {"op": "add_item", "id": "gym", "direction": "out", "amount": "-49.90", "start": "2026-04-01"}
BIKE = {"op": "add_item", "id": "bike", "direction": "out", "amount": "-31.00", "start": "2026-05-01"}


async def _items(client) -> set[str]:
    return {i["id"] for i in (await client.get("/book/state")).json()["items"]}


async def _propose(client, op):
    response = await client.post("/book/edits", json={"origin": "cell_edit", "ops": [op]})
    assert response.status_code == 201, response.text
    return response.json()["proposal"]


async def _propose_by_turn(client, script, op, text="change something", rounds=1):
    """The same card, produced by a turn instead of by a button.

    ``rounds`` covers an operation the engine refuses: the pipeline spends one
    repair round on the diagnostics, so the script needs an answer for it.
    """
    for _ in range(rounds):
        script.append(
            {"kind": "answer", "reply": "Here is what I understood.", "intents": [op]}
        )
    response = await client.post("/turns", json={"text": text})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "proposal", body
    return body["proposal"]


async def _status(database, proposal_id) -> str:
    async with database.connect() as conn:
        return (
            await conn.execute(
                sa.select(proposals_table.c.status).where(proposals_table.c.id == proposal_id)
            )
        ).scalar_one()


# --- 1. there is no unproposed write route -------------------------------- #


def test_the_only_write_routes_are_the_proposal_pipeline(app):
    """An inventory, not a spot check: every mutating route is enumerated.

    A future endpoint that writes without a proposal fails here, which is the
    point — the invariant must not depend on anyone remembering it.
    """
    from conftest import iter_routes

    writing = sorted(
        {
            route.path
            for route in iter_routes(app)
            if {"POST", "PUT", "PATCH", "DELETE"} & route.methods
        }
    )
    assert writing == [
        "/auth/link",          # issues a login token, touches no book
        "/auth/verify",        # opens a session, touches no book
        "/book/discard",       # reverts the overlay to HEAD; removes, never authors
        "/book/edits",         # produces a proposal — applies nothing
        "/book/save",          # commits changes already confirmed one card at a time
        "/book/scenarios",     # produces a proposal (D-MLP-14)
        "/book/scenarios/{scenario_id}/activate",  # app state, not book content
        "/books",              # creates the book itself
        "/me",                 # account deletion
        "/proposals/{proposal_id}",  # the one place a change is applied
        "/turns",              # produces a proposal — applies nothing (S2)
    ]


def test_no_production_code_offers_a_bypass():
    """No debug flag, admin path, or test shortcut in production code."""
    from pathlib import Path

    package = Path(__file__).resolve().parents[1] / "cashkit_service"
    banned = ("debug_apply", "force_apply", "skip_proposal", "admin_apply", "bypass")
    offenders = [
        f"{path.name}: {needle}"
        for path in package.rglob("*.py")
        for needle in banned
        if needle in path.read_text()
    ]
    assert offenders == [], offenders


# --- 2. an unconfirmed proposal changes nothing --------------------------- #


async def test_a_proposal_alone_never_mutates(seeded_client):
    before = await _items(seeded_client)
    await _propose(seeded_client, GYM)
    await _propose(seeded_client, BIKE)
    assert await _items(seeded_client) == before


async def test_a_discarded_proposal_never_mutates(seeded_client):
    before = await _items(seeded_client)
    card = await _propose(seeded_client, GYM)
    await seeded_client.post(f"/proposals/{card['id']}", json={"action": "discard"})
    assert await _items(seeded_client) == before


async def test_an_expired_proposal_never_mutates(seeded_client, clock):
    before = await _items(seeded_client)
    card = await _propose(seeded_client, GYM)
    clock.advance(minutes=16)
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert await _items(seeded_client) == before


async def test_a_proposal_cannot_be_applied_twice(seeded_client):
    card = await _propose(seeded_client, GYM)
    await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    again = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert again.status_code == 409


async def test_another_account_cannot_apply_your_proposal(seeded_client, client, mailer):
    card = await _propose(seeded_client, GYM)

    other = client
    await other.post("/auth/link", json={"email": "intruder@example.com"})
    link = mailer.last_for("intruder@example.com")
    token = (await other.post("/auth/verify", json={"token": link.token})).json()["token"]
    other.headers["Authorization"] = f"Bearer {token}"
    await other.post(
        "/books",
        json={"horizon_start": "2026-01-01", "horizon_end": "2027-01-01", "opening_balance": "1.00"},
    )

    response = await other.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 404
    assert "gym" not in await _items(seeded_client)


async def test_a_proposal_with_errors_is_refused_rather_than_half_applied(seeded_client):
    before = await _items(seeded_client)
    card = await _propose(seeded_client, {"op": "set_amount", "item": "nope", "amount": "-1.00"})
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "APPLY_REFUSED"
    assert await _items(seeded_client) == before


# --- 3. the §2.5 staleness paths ------------------------------------------ #


async def test_accept_after_another_accept_supersedes(seeded_client, database):
    first = await _propose(seeded_client, GYM)
    second = await _propose(seeded_client, BIKE)
    await seeded_client.post(f"/proposals/{first['id']}", json={"action": "accept"})

    assert await _status(database, second["id"]) == "superseded"
    response = await seeded_client.post(f"/proposals/{second['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert "bike" not in await _items(seeded_client)


async def test_accept_after_save_supersedes(seeded_client, database):
    card = await _propose(seeded_client, GYM)
    await seeded_client.post("/book/save", json={"message": "checkpoint"})

    assert await _status(database, card["id"]) == "superseded"
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert "gym" not in await _items(seeded_client)


async def test_accept_after_discard_supersedes(seeded_client, database):
    card = await _propose(seeded_client, GYM)
    await seeded_client.post("/book/discard")

    assert await _status(database, card["id"]) == "superseded"
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert "gym" not in await _items(seeded_client)


async def test_accept_after_scenario_activation_supersedes(seeded_client, database):
    card = await _propose(seeded_client, GYM)
    await seeded_client.post("/book/scenarios/downside/activate")

    assert await _status(database, card["id"]) == "superseded"
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409


async def test_a_stale_stamp_refreshes_and_still_needs_confirming(seeded_client, database):
    """The fingerprint moved without the card being superseded: refresh, never apply.

    The direct path to this is a stamp mismatch on a card that is still
    pending, which the trial produces by rewriting the stored fingerprint —
    simulating a book that moved by a route the service did not record.
    """
    card = await _propose(seeded_client, GYM)
    async with database.connect() as conn:
        await conn.execute(
            proposals_table.update()
            .where(proposals_table.c.id == card["id"])
            .values(overlay_fingerprint="a fingerprint from another book")
        )

    before = await _items(seeded_client)
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "refreshed", body
    assert body["proposal"]["id"] != card["id"]
    assert body["proposal"]["status"] == "pending"
    assert body["proposal"]["supersedes"] == card["id"]

    # Nothing was applied: the refreshed card still awaits confirmation.
    assert await _items(seeded_client) == before
    assert await _status(database, card["id"]) == "superseded"

    # Confirming the refreshed card is what applies it.
    applied = await seeded_client.post(
        f"/proposals/{body['proposal']['id']}", json={"action": "accept"}
    )
    assert applied.json()["kind"] == "applied"
    assert "gym" in await _items(seeded_client)


async def test_a_stale_card_is_never_applied_against_the_wrong_scenario(seeded_client):
    """A card dry-run on base must not land on a fork."""
    card = await _propose(seeded_client, GYM)
    await seeded_client.post("/book/scenarios/downside/activate")
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409

    downside = (await seeded_client.get("/book/state", params={"scenario": "downside"})).json()
    assert "gym" not in {i["id"] for i in downside["items"]}


# --- 4. the same rules, on cards a turn produced (S2) --------------------- #


async def test_a_turn_reaches_the_book_only_through_the_proposal_store(
    seeded_client, model_script, database
):
    """The card a turn produces is a row like any other, and is not applied."""
    before = await _items(seeded_client)
    card = await _propose_by_turn(seeded_client, model_script, GYM, "I joined a gym")

    assert card["origin"] == "turn"
    assert card["turn_id"] is not None
    assert card["status"] == "pending"
    assert await _items(seeded_client) == before
    assert await _status(database, card["id"]) == "pending"


async def test_a_turns_card_needs_confirming_like_any_other(seeded_client, model_script):
    before = await _items(seeded_client)
    card = await _propose_by_turn(seeded_client, model_script, GYM)
    assert await _items(seeded_client) == before

    await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert "gym" in await _items(seeded_client)


async def test_a_discarded_turn_card_never_mutates(seeded_client, model_script):
    before = await _items(seeded_client)
    card = await _propose_by_turn(seeded_client, model_script, GYM)
    await seeded_client.post(f"/proposals/{card['id']}", json={"action": "discard"})
    assert await _items(seeded_client) == before


async def test_an_expired_turn_card_never_mutates(seeded_client, model_script, clock):
    before = await _items(seeded_client)
    card = await _propose_by_turn(seeded_client, model_script, GYM)
    clock.advance(minutes=16)
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert await _items(seeded_client) == before


async def test_a_turn_card_cannot_be_applied_twice(seeded_client, model_script):
    card = await _propose_by_turn(seeded_client, model_script, GYM)
    await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    again = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert again.status_code == 409


async def test_another_account_cannot_apply_your_turn_card(
    seeded_client, model_script, client, mailer
):
    card = await _propose_by_turn(seeded_client, model_script, GYM)

    await client.post("/auth/link", json={"email": "intruder2@example.com"})
    token = (await client.post(
        "/auth/verify", json={"token": mailer.last_for("intruder2@example.com").token}
    )).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    await client.post("/books", json={"horizon_start": "2026-01-01",
                                      "horizon_end": "2027-01-01",
                                      "opening_balance": "1.00"})

    response = await client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 404
    assert "gym" not in await _items(seeded_client)


async def test_a_turn_card_goes_stale_the_same_way(seeded_client, model_script, database):
    """Save moves the ground; the card supersedes rather than applying blind."""
    card = await _propose_by_turn(seeded_client, model_script, GYM)
    await seeded_client.post("/book/save", json={"message": "checkpoint"})

    assert await _status(database, card["id"]) == "superseded"
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert "gym" not in await _items(seeded_client)


async def test_a_turn_card_and_a_button_card_supersede_each_other(
    seeded_client, model_script, database
):
    """One pipeline, one supersession rule, whatever produced the card."""
    from_button = await _propose(seeded_client, BIKE)
    from_turn = await _propose_by_turn(seeded_client, model_script, GYM)

    await seeded_client.post(f"/proposals/{from_turn['id']}", json={"action": "accept"})
    assert await _status(database, from_button["id"]) == "superseded"

    response = await seeded_client.post(f"/proposals/{from_button['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert "bike" not in await _items(seeded_client)


async def test_a_turn_card_with_errors_is_refused_rather_than_half_applied(
    seeded_client, model_script
):
    before = await _items(seeded_client)
    card = await _propose_by_turn(
        seeded_client, model_script,
        {"op": "set_amount", "item": "nope", "amount": "-1.00"},
        "change the thing that is not there",
        rounds=2,
    )
    response = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "APPLY_REFUSED"
    assert await _items(seeded_client) == before
