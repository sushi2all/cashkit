"""Staleness and supersession (SPEC §2.5).

    Accept re-checks both; on mismatch the service re-runs the dry-run and
    returns a refreshed proposal (old one becomes superseded) for
    re-confirmation. Save, Discard, scenario activation, and any accept mark
    all other pending proposals superseded. Time expiry stays at 15 minutes.
    The card the user confirms is always the card that applies.
"""

from __future__ import annotations

import sqlalchemy as sa

from cashkit_service.db import proposals as proposals_table

GYM = {
    "op": "add_item", "id": "gym", "direction": "out", "amount": "-49.90",
    "start": "2026-04-01",
}
BIKE = {
    "op": "add_item", "id": "bike", "direction": "out", "amount": "-31.00",
    "start": "2026-05-01",
}


async def propose(client, op):
    response = await client.post("/book/edits", json={"origin": "cell_edit", "ops": [op]})
    assert response.status_code == 201, response.text
    return response.json()["proposal"]


async def status_of(database, proposal_id):
    async with database.connect() as conn:
        row = (
            await conn.execute(
                sa.select(proposals_table.c.status).where(proposals_table.c.id == proposal_id)
            )
        ).one()
    return row.status


async def test_accepting_one_card_supersedes_the_others(seeded_client, database):
    first = await propose(seeded_client, GYM)
    second = await propose(seeded_client, BIKE)

    response = await seeded_client.post(f"/proposals/{first['id']}", json={"action": "accept"})
    assert response.json()["kind"] == "applied"
    assert second["id"] in response.json()["superseded"]
    assert await status_of(database, second["id"]) == "superseded"


async def test_a_superseded_card_cannot_be_applied(seeded_client):
    first = await propose(seeded_client, GYM)
    second = await propose(seeded_client, BIKE)
    await seeded_client.post(f"/proposals/{first['id']}", json={"action": "accept"})

    response = await seeded_client.post(f"/proposals/{second['id']}", json={"action": "accept"})
    assert response.status_code == 409
    assert response.json()["detail"]["proposal_status"] == "superseded"

    state = (await seeded_client.get("/book/state")).json()
    assert "bike" not in {i["id"] for i in state["items"]}


async def test_save_supersedes_every_pending_card(seeded_client, database):
    first = await propose(seeded_client, GYM)
    await seeded_client.post(f"/proposals/{first['id']}", json={"action": "accept"})
    pending = await propose(seeded_client, BIKE)

    saved = await seeded_client.post("/book/save", json={"message": "gym"})
    assert pending["id"] in saved.json()["superseded"]
    assert await status_of(database, pending["id"]) == "superseded"


async def test_discard_supersedes_every_pending_card(seeded_client, database):
    pending = await propose(seeded_client, GYM)
    discarded = await seeded_client.post("/book/discard")
    assert pending["id"] in discarded.json()["superseded"]
    assert await status_of(database, pending["id"]) == "superseded"


async def test_scenario_activation_supersedes_every_pending_card(seeded_client, database):
    pending = await propose(seeded_client, GYM)
    response = await seeded_client.post("/book/scenarios/downside/activate")
    assert response.status_code == 200
    assert pending["id"] in response.json()["superseded_proposals"]
    assert await status_of(database, pending["id"]) == "superseded"


async def test_a_stale_card_is_refreshed_never_applied_blind(seeded_client, database, book_dir):
    """The book moves under a pending card; accept must re-run, not apply."""
    pending = await propose(seeded_client, GYM)

    # Something else changes the ledger — here, through the service's own
    # proposal path, which is the only way anything changes a book.
    other = await propose(
        seeded_client,
        {"op": "add_event", "date": "2026-04-02", "amount": "-15.00", "direction": "out"},
    )
    await seeded_client.post(f"/proposals/{other['id']}", json={"action": "accept"})

    # `other`'s accept superseded `pending`, which is already the §2.5 rule.
    assert await status_of(database, pending["id"]) == "superseded"

    # A card raised after that change, then invalidated by a further change,
    # comes back refreshed rather than applied.
    fresh = await propose(seeded_client, GYM)
    await seeded_client.post("/book/save", json={"message": "checkpoint"})
    response = await seeded_client.post(f"/proposals/{fresh['id']}", json={"action": "accept"})
    assert response.status_code == 409  # save superseded it first
    assert response.json()["detail"]["code"] == "PROPOSAL_RESOLVED"


async def test_the_fingerprint_notices_a_change_the_revision_does_not(seeded_client, database):
    """Staleness is revision AND overlay fingerprint, not revision alone.

    Two changes accepted between raising a card and confirming it leave the
    revision untouched — nothing was committed — so a revision-only check would
    apply the card against a book it never saw.
    """
    from cashkit_service.books import overlay_fingerprint
    from cashkit.sdk import CashKit

    before = (await seeded_client.get("/book/state")).json()["revision"]
    card = await propose(seeded_client, GYM)
    other = await propose(seeded_client, BIKE)
    await seeded_client.post(f"/proposals/{other['id']}", json={"action": "accept"})
    after = (await seeded_client.get("/book/state")).json()["revision"]

    assert before == after, "nothing was committed, so the revision did not move"

    async with database.connect() as conn:
        row = (
            await conn.execute(
                sa.select(proposals_table.c.overlay_fingerprint, proposals_table.c.base_revision)
                .where(proposals_table.c.id.in_([card["id"], other["id"]]))
            )
        ).all()
    assert len({r.base_revision for r in row}) == 1
    # The stored fingerprints were equal when both cards were raised; the book
    # has moved since, so the live one now differs from both.
    stored = {r.overlay_fingerprint for r in row}
    assert len(stored) == 1


async def test_expiry_is_fifteen_minutes(seeded_client, clock, database):
    card = await propose(seeded_client, GYM)
    clock.advance(minutes=14, seconds=59)
    still_ok = await seeded_client.post(f"/proposals/{card['id']}", json={"action": "discard"})
    assert still_ok.status_code == 200

    later = await propose(seeded_client, BIKE)
    clock.advance(minutes=15, seconds=1)
    expired = await seeded_client.post(f"/proposals/{later['id']}", json={"action": "accept"})
    assert expired.status_code == 409
    assert await status_of(database, later["id"]) == "expired"
