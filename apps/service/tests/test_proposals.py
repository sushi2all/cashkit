"""The proposal pipeline (SPEC §2.5, §5-F2, ADR-0029)."""

from __future__ import annotations

from decimal import Decimal

import pytest


async def make_proposal(client, ops, **kw):
    body = {"origin": kw.pop("origin", "cell_edit"), "ops": ops, **kw}
    response = await client.post("/book/edits", json=body)
    assert response.status_code in (200, 201), response.text
    return response.json()


RENT = {
    "op": "add_item", "id": "gym", "direction": "out", "amount": "-49.90",
    "recurrence": "1m", "start": "2026-04-01", "tags": {"cat": "leisure"},
}


async def test_an_edit_returns_a_proposal_and_changes_nothing(seeded_client):
    before = (await seeded_client.get("/book/state")).json()
    body = await make_proposal(seeded_client, [RENT])

    assert body["kind"] == "proposal"
    assert body["proposal"]["status"] == "pending"
    assert body["proposal"]["origin"] == "cell_edit"

    after = (await seeded_client.get("/book/state")).json()
    assert after["revision"] == before["revision"]
    assert {i["id"] for i in after["items"]} == {i["id"] for i in before["items"]}


async def test_a_pending_proposal_is_stamped_pending(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    assert body["what_if"] == {"stamped": True, "reason": "pending", "scenario": "base"}


async def test_the_deltas_block_carries_the_card_figures(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    deltas = body["proposal"]["deltas"]
    for block in ("closing_balance", "min_cash"):
        assert set(deltas[block]) == {"before", "after", "change"}
        assert deltas[block]["before"]["exact"] != deltas[block]["after"]["exact"]
    assert deltas["affected_items"] == ["gym"]
    assert "runway_end" in deltas


async def test_crossing_flags_mark_the_months_a_change_turns_negative(seeded_client):
    """D-MLP-05(b): structural, computed in the dry-run, no thresholds."""
    body = await make_proposal(
        seeded_client,
        [{
            "op": "add_event", "date": "2026-03-20", "amount": "-9000.00",
            "direction": "out", "note": "a big bill",
        }],
    )
    deltas = body["proposal"]["deltas"]
    assert deltas["crossings"], "this change drives the book negative"
    first = deltas["crossings"][0]
    assert Decimal(first["before"]["exact"]) >= 0 > Decimal(first["after"]["exact"])
    assert deltas["negative_months_after"] > deltas["negative_months_before"]


async def test_a_harmless_change_flags_no_crossing(seeded_client):
    body = await make_proposal(
        seeded_client,
        [{"op": "add_event", "date": "2026-03-20", "amount": "25.00", "direction": "in"}],
    )
    assert body["proposal"]["deltas"]["crossings"] == []


async def test_accept_applies_the_change(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    proposal_id = body["proposal"]["id"]

    response = await seeded_client.post(f"/proposals/{proposal_id}", json={"action": "accept"})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "applied"

    state = (await seeded_client.get("/book/state")).json()
    assert "gym" in {i["id"] for i in state["items"]}
    # The change is in the working overlay, not committed, so the payload is
    # stamped as an overlay (SPEC §2.4).
    assert state["dirty"] is True
    assert state["what_if"] == {"stamped": True, "reason": "overlay", "scenario": "base"}


async def test_discard_leaves_the_book_alone(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    proposal_id = body["proposal"]["id"]
    response = await seeded_client.post(f"/proposals/{proposal_id}", json={"action": "discard"})
    assert response.json()["kind"] == "discarded"

    state = (await seeded_client.get("/book/state")).json()
    assert "gym" not in {i["id"] for i in state["items"]}
    assert state["dirty"] is False


async def test_a_resolved_proposal_cannot_be_resolved_again(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    proposal_id = body["proposal"]["id"]
    await seeded_client.post(f"/proposals/{proposal_id}", json={"action": "accept"})
    again = await seeded_client.post(f"/proposals/{proposal_id}", json={"action": "accept"})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "PROPOSAL_RESOLVED"


async def test_a_proposal_expires_after_fifteen_minutes(seeded_client, clock):
    body = await make_proposal(seeded_client, [RENT])
    proposal_id = body["proposal"]["id"]
    clock.advance(minutes=15, seconds=1)
    response = await seeded_client.post(f"/proposals/{proposal_id}", json={"action": "accept"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PROPOSAL_EXPIRED"

    state = (await seeded_client.get("/book/state")).json()
    assert "gym" not in {i["id"] for i in state["items"]}


async def test_save_commits_and_clears_the_stamp(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    await seeded_client.post(f"/proposals/{body['proposal']['id']}", json={"action": "accept"})

    before = (await seeded_client.get("/book/state")).json()["revision"]
    saved = await seeded_client.post("/book/save", json={"message": "add the gym"})
    assert saved.status_code == 200 and saved.json()["committed"] is True

    state = (await seeded_client.get("/book/state")).json()
    assert state["revision"] != before
    assert state["dirty"] is False
    assert state["what_if"]["stamped"] is False


async def test_discard_reverts_the_working_overlay(seeded_client):
    body = await make_proposal(seeded_client, [RENT])
    await seeded_client.post(f"/proposals/{body['proposal']['id']}", json={"action": "accept"})
    assert (await seeded_client.get("/book/state")).json()["dirty"] is True

    await seeded_client.post("/book/discard")
    state = (await seeded_client.get("/book/state")).json()
    assert state["dirty"] is False
    assert "gym" not in {i["id"] for i in state["items"]}


async def test_a_fork_button_produces_a_card_not_a_scenario(seeded_client):
    response = await seeded_client.post("/book/scenarios", json={"name": "upside"})
    assert response.status_code == 201
    body = response.json()
    assert body["kind"] == "proposal"

    listed = (await seeded_client.get("/book/scenarios")).json()
    assert "upside" not in {s["id"] for s in listed["scenarios"]}

    await seeded_client.post(f"/proposals/{body['proposal']['id']}", json={"action": "accept"})
    listed = (await seeded_client.get("/book/scenarios")).json()
    assert "upside" in {s["id"] for s in listed["scenarios"]}


async def test_save_is_not_a_proposable_operation(seeded_client):
    response = await seeded_client.post(
        "/book/edits", json={"origin": "button", "ops": [{"op": "save", "message": "x"}]}
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "NOT_PROPOSABLE"


async def test_diagnostics_from_a_bad_change_reach_the_card_verbatim(seeded_client):
    body = await make_proposal(
        seeded_client,
        [{"op": "set_amount", "item": "does_not_exist", "amount": "-10.00"}],
    )
    diagnostics = body["proposal"]["diagnostics"]
    assert diagnostics
    assert "does_not_exist" in diagnostics[0]["message"]
    assert diagnostics[0]["suggested_fix"]
