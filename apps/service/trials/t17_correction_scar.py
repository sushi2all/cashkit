"""T17 — a correction leaves the scar (SPEC §10, ADR-0012/0013).

    T17 correction scar (original retrievable, note present)

The fact is immutable; the record of it can be wrong. Correcting a record is
itself an event — dated, attributed, auditable — and it never erases what was
recorded before. The scar is required structure, not a UI flourish: the
Actuals screen shows the original struck with the correction linked
(SPEC §6-S7).

Zero model calls.
"""

from __future__ import annotations

from decimal import Decimal

import pytest


async def _events(client, *, include_voided: bool = False) -> list[dict]:
    response = await client.get("/book/events", params={"include_voided": include_voided})
    return response.json()["events"]


async def _apply(client, op, **kw):
    response = await client.post(
        "/book/edits", json={"origin": kw.pop("origin", "cell_edit"), "ops": [op], **kw}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["kind"] == "proposal", body
    accepted = await client.post(
        f"/proposals/{body['proposal']['id']}", json={"action": "accept"}
    )
    return body, accepted


@pytest.fixture
async def corrected(seeded_client):
    """The seeded actual, corrected once, with a mandatory note."""
    original = next(e for e in await _events(seeded_client) if e["status"] == "actual")
    _card, accepted = await _apply(
        seeded_client,
        {
            "op": "correct_actual",
            "event": original["id"],
            "amount": "-143.90",
            "note": "the bank line was read the wrong way round",
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["kind"] == "applied"
    return original


async def test_the_correction_is_recorded_as_its_own_event(seeded_client, corrected):
    live = await _events(seeded_client)
    correction = next(e for e in live if e["corrects"] == corrected["id"])
    assert correction["amount"] == {"exact": "-143.9000", "display": "-143.90"}
    assert correction["status"] == "actual", "a correction inherits the original's status"


async def test_the_note_is_present_and_verbatim(seeded_client, corrected):
    live = await _events(seeded_client)
    correction = next(e for e in live if e["corrects"] == corrected["id"])
    assert correction["note"] == "the bank line was read the wrong way round"


async def test_a_correction_without_a_note_is_refused(seeded_client):
    """ADR-0012: a correction without a stated reason is not auditable."""
    original = next(e for e in await _events(seeded_client) if e["status"] == "actual")
    response = await seeded_client.post(
        "/book/edits",
        json={
            "origin": "cell_edit",
            "ops": [{"op": "correct_actual", "event": original["id"], "amount": "-1.00", "note": ""}],
        },
    )
    assert response.status_code == 422


async def test_the_original_is_retrievable_after_the_correction(seeded_client, corrected):
    """The scar: the original row is tombstoned, never deleted."""
    live = {e["id"] for e in await _events(seeded_client)}
    assert corrected["id"] not in live, "the original leaves the live view"

    with_scar = await _events(seeded_client, include_voided=True)
    original = next(e for e in with_scar if e["id"] == corrected["id"])
    assert original["amount"] == {"exact": "-134.0900", "display": "-134.09"}
    assert original["note"] == "recorded from the bank line"


async def test_the_correction_links_back_to_what_it_corrects(seeded_client, corrected):
    with_scar = await _events(seeded_client, include_voided=True)
    correction = next(e for e in with_scar if e["corrects"] == corrected["id"])
    original = next(e for e in with_scar if e["id"] == corrected["id"])
    # Everything the struck-through row needs is on the wire: the original
    # amount, the correction amount, the link and the reason.
    assert correction["corrects"] == original["id"]
    assert correction["amount"] != original["amount"]
    assert correction["note"]


async def test_only_the_correction_counts_in_the_numbers(seeded_client, corrected):
    """The fact union excludes tombstones and includes correcting rows."""
    live = await _events(seeded_client)
    actual_total = sum(Decimal(e["amount"]["exact"]) for e in live if e["status"] == "actual")
    assert actual_total == Decimal("-143.9000")

    reconciliation = (await seeded_client.get("/book/reconcile")).json()["reconciliation"]
    assert reconciliation["actual_total"]["exact"] == "-143.9000"


async def test_a_correction_goes_through_a_proposal_like_every_write(seeded_client):
    original = next(e for e in await _events(seeded_client) if e["status"] == "actual")
    response = await seeded_client.post(
        "/book/edits",
        json={
            "origin": "cell_edit",
            "ops": [{"op": "correct_actual", "event": original["id"], "amount": "-1.00", "note": "n"}],
        },
    )
    assert response.json()["proposal"]["status"] == "pending"
    # Unconfirmed, the ledger is untouched.
    unchanged = await _events(seeded_client)
    assert {e["id"] for e in unchanged} == {e["id"] for e in await _events(seeded_client)}
    assert next(e for e in unchanged if e["id"] == original["id"])["amount"] == original["amount"]


async def test_a_correction_can_itself_be_corrected(seeded_client, corrected):
    """Append-only means the chain grows; nothing collapses."""
    live = await _events(seeded_client)
    first = next(e for e in live if e["corrects"] == corrected["id"])
    _card, accepted = await _apply(
        seeded_client,
        {"op": "correct_actual", "event": first["id"], "amount": "-140.00", "note": "final figure"},
    )
    assert accepted.json()["kind"] == "applied"

    with_scar = await _events(seeded_client, include_voided=True)
    ids = {e["id"] for e in with_scar}
    assert corrected["id"] in ids and first["id"] in ids, "every step of the chain survives"
    latest = next(e for e in await _events(seeded_client) if e["status"] == "actual")
    assert latest["amount"]["exact"] == "-140.0000"
