"""T14 — a question turn never writes (SPEC §10, ADR-0029).

    T14 question-turn-never-writes (T11 scenario through the service)

Proto trial T11 is the reason ADR-0029 exists. Asked "can I afford a 1500 EUR
laptop in September?", the model answered *and emitted two write operations* —
it changed the book while answering a question. The ADR's finding was that a
prompt rule against it is demonstrably not enough, so enforcement had to be
**structural and post-interpretation**.

This trial therefore does not test that the model behaves. It tests that the
book is unchanged **when the model misbehaves**: the scripted model here writes
on questions on purpose, every time, and the book still does not move. A trial
that only ran the real model would pass for the wrong reason on a good day.

The live-model half is in ``t11_qa_affordability.py``, which runs the real
sentence against the real model.
"""

from __future__ import annotations

import sqlalchemy as sa

from cashkit_service.books import head_revision, overlay_fingerprint
from cashkit_service.db import proposals as proposals_table
from cashkit_service.intents.read import READ_INTENTS

#: What T11's model actually did: an answer, plus two writes nobody asked for.
T11_MISBEHAVIOUR = {
    "kind": "answer",
    "reply": "Yes, you can afford it — you would have 400 left.",
    "intents": [
        {"op": "project_balance", "delta": "-1500.00", "delta_date": "2026-09-15"},
        {"op": "add_event", "date": "2026-09-15", "amount": "-1500.00", "direction": "out",
         "note": "laptop"},
        {"op": "add_item", "id": "laptop_fund", "direction": "out", "amount": "-125.00",
         "start": "2026-09-01"},
    ],
}


def _fingerprint(book_dir) -> tuple[str | None, str]:
    """The book's own bytes, read through a second kit — not through the service."""
    from cashkit.sdk import CashKit

    kit, _diagnostics = CashKit.open(book_dir)
    assert kit is not None
    try:
        return head_revision(kit), overlay_fingerprint(kit)
    finally:
        if kit.ledger is not None:
            kit.ledger.close()


async def _state(client) -> dict:
    body = (await client.get("/book/state")).json()
    return {
        "revision": body["revision"],
        "dirty": body["dirty"],
        "items": sorted(i["id"] for i in body["items"]),
        "closing": body["closing"],
        "summary": body["summary"],
    }


async def _applied(database) -> int:
    async with database.connect() as conn:
        return (
            await conn.execute(
                sa.select(sa.func.count()).select_from(proposals_table).where(
                    proposals_table.c.status == "accepted"
                )
            )
        ).scalar_one()


# --- the T11 scenario, with a model that writes on a question -------------- #


async def test_the_t11_question_leaves_the_book_untouched(
    seeded_client, model_script, database, book_dir, app
):
    model_script.append(T11_MISBEHAVIOUR)
    model_script.append({"kind": "answer", "reply": "You would have 400 left.", "intents": []})

    before_state = await _state(seeded_client)
    before_bytes = _fingerprint(book_dir)

    response = await seeded_client.post(
        "/turns", json={"text": "can I afford a 1500 EUR laptop in September?"}
    )
    assert response.status_code == 200, response.text

    assert await _state(seeded_client) == before_state
    assert _fingerprint(book_dir) == before_bytes
    assert await _applied(database) == 0


async def test_the_unasked_writes_surface_on_a_card_instead_of_landing(
    seeded_client, model_script
):
    """ADR-0029's own words: unexpected operations surface in the confirmation.

    The guard covers the reverse failure too — a turn that mutates more than it
    was asked to — because the user sees every operation before applying any.
    """
    model_script.append(T11_MISBEHAVIOUR)
    model_script.append({"kind": "answer", "reply": "You would have 400 left.", "intents": []})

    body = (await seeded_client.post(
        "/turns", json={"text": "can I afford a 1500 EUR laptop in September?"}
    )).json()

    assert body["kind"] == "proposal"
    assert body["proposal"]["status"] == "pending"
    assert [op["op"] for op in body["proposal"]["operations"]] == ["add_event", "add_item"]
    # The answer to the question is still there: holding the writes did not
    # cost the user their answer.
    assert body["reply"]
    assert [r["op"] for r in body["receipts"]] == ["project_balance"]


async def test_a_question_that_writes_repeatedly_still_writes_nothing(
    seeded_client, model_script, book_dir
):
    before = _fingerprint(book_dir)
    for _ in range(3):
        model_script.append(T11_MISBEHAVIOUR)
        model_script.append({"kind": "answer", "reply": "Still no.", "intents": []})
        await seeded_client.post("/turns", json={"text": "can I afford it?"})
    assert _fingerprint(book_dir) == before


# --- every read intent, as a turn ------------------------------------------ #


async def test_no_read_intent_can_move_the_book(seeded_client, model_script, book_dir):
    """All twelve, plus the host read tool. None of them is a write."""
    requests = {
        "project_balance": {"op": "project_balance", "delta": "-500.00"},
        "runway": {"op": "runway"},
        "min_cash": {"op": "min_cash"},
        "breakeven": {"op": "breakeven"},
        "top_categories": {"op": "top_categories", "direction": "out"},
        "item_total": {"op": "item_total", "item": "rent"},
        "explain_cell": {"op": "explain_cell", "item": "rent", "period": "2026-05-01"},
        "explain_zero": {"op": "explain_zero", "item": "rent", "period": "2026-05-01"},
        "compare_scenarios": {"op": "compare_scenarios", "scenarios": ["base", "downside"]},
        "coverage": {"op": "coverage"},
        "list_items": {"op": "list_items"},
        "history": {"op": "history"},
        "query_ledger": {"op": "query_ledger"},
    }
    assert set(requests) - {"query_ledger"} == set(READ_INTENTS)

    before = _fingerprint(book_dir)
    for name, request in requests.items():
        model_script.append({"kind": "answer", "reply": "", "intents": [request]})
        model_script.append({"kind": "answer", "reply": f"Answered {name}.", "intents": []})
        response = await seeded_client.post("/turns", json={"text": f"tell me about {name}"})
        assert response.status_code == 200, (name, response.text)
        assert response.json()["kind"] == "answer", name
        assert _fingerprint(book_dir) == before, name


# --- the Q&A loop is read-only --------------------------------------------- #


async def test_a_write_emitted_inside_the_qa_loop_is_held_too(
    seeded_client, model_script, book_dir, database
):
    """The loop can ask for more figures; it cannot take a different turn."""
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "min_cash"}]})
    model_script.append(
        {"kind": "answer", "reply": "Let me also add that to the book.",
         "intents": [
             {"op": "runway"},
             {"op": "add_item", "id": "sneaky", "direction": "out", "amount": "-10.00",
              "start": "2026-05-01"},
         ]}
    )
    model_script.append({"kind": "answer", "reply": "Here is the answer.", "intents": []})

    before = _fingerprint(book_dir)
    body = (await seeded_client.post("/turns", json={"text": "what is my lowest month?"})).json()

    assert _fingerprint(book_dir) == before
    assert await _applied(database) == 0
    assert body["kind"] == "proposal"
    assert [op["id"] for op in body["proposal"]["operations"]] == ["sneaky"]


async def test_the_qa_loop_is_bounded(seeded_client, model_script, transport, app):
    """SPEC §2.3 step 5: up to four read-only calls, and no more."""
    app.state.settings.llm_qa_max_calls = 3
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "runway"}]})
    for _ in range(10):
        model_script.append(
            {"kind": "answer", "reply": "one more", "intents": [{"op": "min_cash"}]}
        )

    await seeded_client.post("/turns", json={"text": "keep looking"})
    # One interpret call, then at most three loop calls.
    assert len(transport.calls) == 1 + 3


# --- the model's reach ------------------------------------------------------ #


async def test_a_model_that_names_a_host_operation_changes_nothing(
    seeded_client, model_script, book_dir
):
    """Host ops are not the model's to reach (SPEC §2.5, D-MLP-03)."""
    model_script.append(
        {"kind": "answer", "reply": "Extending the horizon.",
         "intents": [
             {"op": "set_horizon", "start": "2026-01-01", "end": "2028-01-01"},
             {"op": "set_opening_balance", "amount": "999999.00"},
             {"op": "remove_event", "event": "ev-actual-feb"},
         ]}
    )
    before = _fingerprint(book_dir)
    body = (await seeded_client.post("/turns", json={"text": "extend to 2028"})).json()

    assert _fingerprint(book_dir) == before
    assert body["proposal"] is None
    assert len(body["diagnostics"]) == 3
    assert all(d["code"] == "CK-E901" for d in body["diagnostics"])


async def test_a_model_that_asks_to_save_does_not_save(seeded_client, model_script, book_dir):
    """M9 is expressible and reportable; committing is the user's act (D-MLP-18)."""
    model_script.append(
        {"kind": "answer", "reply": "Saving.", "intents": [{"op": "save", "message": "done"}]}
    )
    before = _fingerprint(book_dir)
    body = (await seeded_client.post("/turns", json={"text": "save my work"})).json()

    assert _fingerprint(book_dir) == before
    assert body["proposal"] is None
    assert body["diagnostics"][0]["severity"] == "info"
    assert "Save" in body["diagnostics"][0]["message"]
