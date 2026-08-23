"""``POST /turns`` — the turn pipeline end to end (SPEC §2.3, §3, §4, §11).

The model is scripted here so the *pipeline* is what is under test: the guard,
the proposal path, the journal, the correlation chain and the stamps. The live
model is exercised by the ported trials in ``apps/service/trials``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import sqlalchemy as sa

from cashkit_service.db import llm_calls, proposals, turns as turns_table

GYM = {
    "op": "add_item", "id": "gym", "direction": "out", "amount": "-49.90",
    "recurrence": "1m", "start": "2026-04-01",
}


async def _items(client, scenario: str | None = None) -> set[str]:
    params = {"scenario": scenario} if scenario else None
    return {i["id"] for i in (await client.get("/book/state", params=params)).json()["items"]}


async def _rows(database, table, **where):
    async with database.connect() as conn:
        statement = sa.select(table)
        for column, value in where.items():
            statement = statement.where(table.c[column] == value)
        return list(await conn.execute(statement.order_by(table.c.created_at)))


# --- an answer turn -------------------------------------------------------- #


async def test_a_read_turn_answers_and_changes_nothing(seeded_client, model_script, database):
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "min_cash"}]})
    model_script.append({"kind": "answer", "reply": "The low point is -13,265.14 in June.",
                         "intents": []})

    before = await _items(seeded_client)
    response = await seeded_client.post("/turns", json={"text": "what is my lowest month?"})
    body = response.json()

    assert response.status_code == 200, response.text
    assert body["kind"] == "answer"
    assert body["reply"] == "The low point is -13,265.14 in June."
    assert body["proposal"] is None
    assert await _items(seeded_client) == before
    assert await _rows(database, proposals) == []


async def test_a_receipt_carries_the_engine_figure_verbatim(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "min_cash"}]})
    model_script.append({"kind": "answer", "reply": "Quoted.", "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "lowest?"})).json()
    receipt = body["receipts"][0]

    assert receipt["op"] == "min_cash"
    # Money in a receipt is the canonical {exact, display} pair (D-MLP-06),
    # so the interface renders a string the service produced.
    assert set(receipt["payload"]["min_cash"]) == {"exact", "display"}
    state = (await seeded_client.get("/book/state")).json()
    assert receipt["payload"]["min_cash"] == state["summary"]["min_cash"]


async def test_the_read_intent_answer_matches_the_read_endpoint(seeded_client, model_script):
    """The Q&A loop quotes the same numbers the API serves. One source."""
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "runway"}]})
    model_script.append({"kind": "answer", "reply": "ok", "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "runway?"})).json()
    payload = body["receipts"][0]["payload"]
    state = (await seeded_client.get("/book/state")).json()

    assert payload["runway_end"] == (
        state["summary"]["runway_end"] if state["summary"]["runway_end"] else None
    )


# --- a change turn --------------------------------------------------------- #


async def test_a_change_turn_returns_a_card_and_applies_nothing(
    seeded_client, model_script, database
):
    model_script.append({"kind": "answer", "reply": "I will add a gym membership.",
                         "intents": [GYM]})

    before = await _items(seeded_client)
    body = (await seeded_client.post(
        "/turns", json={"text": "I joined a gym, 49.90 a month from April"}
    )).json()

    assert body["kind"] == "proposal"
    assert body["proposal"]["origin"] == "turn"
    assert body["proposal"]["status"] == "pending"
    assert body["proposal"]["turn_id"] == body["turn_id"]
    assert body["proposal"]["operations"][0]["id"] == "gym"
    # Nothing landed. The card is the change; the user applies it.
    assert await _items(seeded_client) == before


async def test_applying_the_turn_card_is_what_changes_the_book(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})
    card = (await seeded_client.post("/turns", json={"text": "gym 49.90 monthly from April"})).json()

    applied = await seeded_client.post(
        f"/proposals/{card['proposal']['id']}", json={"action": "accept"}
    )
    assert applied.json()["kind"] == "applied"
    assert "gym" in await _items(seeded_client)


async def test_the_card_carries_the_dry_run_deltas(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})
    card = (await seeded_client.post("/turns", json={"text": "gym"})).json()["proposal"]

    deltas = card["deltas"]
    assert deltas["closing_balance"]["before"] != deltas["closing_balance"]["after"]
    assert deltas["affected_items"] == ["gym"]


async def test_a_turn_naming_a_scenario_proposes_against_it(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "On the fork.", "intents": [GYM]})
    card = (await seeded_client.post(
        "/turns", json={"text": "add a gym", "scenario": "downside"}
    )).json()

    assert card["proposal"]["scenario"] == "downside"
    assert card["scenario"] == "downside"


# --- clarification --------------------------------------------------------- #


async def test_a_clarification_stores_nothing(seeded_client, model_script, database):
    model_script.append({"kind": "clarification", "reply": "Which month did that start?",
                         "intents": []})
    body = (await seeded_client.post("/turns", json={"text": "I pay for a gym"})).json()

    assert body["kind"] == "clarification"
    assert body["clarification"] == "Which month did that start?"
    assert body["proposal"] is None
    assert await _rows(database, proposals) == []


async def test_the_record_actual_flow_without_a_date_is_a_clarification(
    seeded_client, model_script, database
):
    """SPEC §5-F5: ambiguous or missing date → clarification, never a guess."""
    model_script.append(
        {"kind": "answer", "reply": "Recording it.",
         "intents": [{"op": "add_event", "amount": "-42.00", "direction": "out"}]}
    )
    body = (await seeded_client.post(
        "/turns", json={"text": "I paid 42 for the water bill", "context": "actuals_record"}
    )).json()

    assert body["kind"] == "clarification"
    assert "date" in body["clarification"].lower()
    assert await _rows(database, proposals) == []


async def test_the_record_actual_flow_records_an_actual_when_the_date_is_past(
    seeded_client, model_script
):
    """The discriminator is S1's and unchanged; the turn only passes context."""
    model_script.append(
        {"kind": "answer", "reply": "Recording it.",
         "intents": [{"op": "add_event", "date": "2026-03-02", "amount": "-42.00",
                      "direction": "out"}]}
    )
    card = (await seeded_client.post(
        "/turns", json={"text": "I paid 42 on the 2nd", "context": "actuals_record"}
    )).json()
    await seeded_client.post(f"/proposals/{card['proposal']['id']}", json={"action": "accept"})

    events = (await seeded_client.get("/book/events")).json()["events"]
    recorded = [e for e in events if e["amount"]["display"] == "-42.00"]
    assert recorded and recorded[0]["status"] == "actual"


async def test_a_future_date_on_the_record_actual_flow_stays_forecast(
    seeded_client, model_script
):
    model_script.append(
        {"kind": "answer", "reply": "Noting it.",
         "intents": [{"op": "add_event", "date": "2026-11-02", "amount": "-42.00",
                      "direction": "out"}]}
    )
    card = (await seeded_client.post(
        "/turns", json={"text": "I will pay 42 in November", "context": "actuals_record"}
    )).json()
    await seeded_client.post(f"/proposals/{card['proposal']['id']}", json={"action": "accept"})

    events = (await seeded_client.get("/book/events")).json()["events"]
    recorded = [e for e in events if e["amount"]["display"] == "-42.00"]
    assert recorded and recorded[0]["status"] == "forecast"


async def test_the_same_intent_off_the_flow_stays_forecast(seeded_client, model_script):
    model_script.append(
        {"kind": "answer", "reply": "Noting it.",
         "intents": [{"op": "add_event", "date": "2026-03-02", "amount": "-42.00",
                      "direction": "out"}]}
    )
    card = (await seeded_client.post("/turns", json={"text": "I paid 42 on the 2nd"})).json()
    await seeded_client.post(f"/proposals/{card['proposal']['id']}", json={"action": "accept"})

    events = (await seeded_client.get("/book/events")).json()["events"]
    recorded = [e for e in events if e["amount"]["display"] == "-42.00"]
    assert recorded and recorded[0]["status"] == "forecast"


# --- SPEC §4 and §11: the journal and the correlation chain ---------------- #


async def test_every_model_call_lands_one_llm_calls_row(seeded_client, model_script, database):
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "runway"}]})
    model_script.append({"kind": "answer", "reply": "About six months.", "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "runway?"})).json()
    rows = await _rows(database, llm_calls)

    assert len(rows) == 2 == body["llm_calls"]
    assert [r.seq for r in rows] == [0, 1]
    assert [r.purpose for r in rows] == ["interpret", "qa"]
    assert all(str(r.turn_id) == body["turn_id"] for r in rows)
    # SPEC §4: the raw payloads are kept (and purge after 30 days), and the
    # numeric columns are kept for good.
    assert all(r.request and r.response for r in rows)
    assert all(r.prompt_tokens and r.completion_tokens for r in rows)
    assert all(r.cost is not None and r.latency_ms is not None for r in rows)


async def test_the_correlation_chain_is_intact(seeded_client, model_script, database):
    """SPEC §11: request_id → turn_id → llm_calls.seq → proposal_id."""
    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})

    response = await seeded_client.post(
        "/turns", json={"text": "gym"}, headers={"x-request-id": "req-chain-1"}
    )
    body = response.json()

    assert response.headers["x-request-id"] == "req-chain-1"
    assert body["request_id"] == "req-chain-1"

    turn = (await _rows(database, turns_table))[0]
    assert turn.request_id == "req-chain-1"
    assert str(turn.id) == body["turn_id"]

    calls = await _rows(database, llm_calls)
    assert all(call.turn_id == turn.id for call in calls)

    card = (await _rows(database, proposals))[0]
    assert card.turn_id == turn.id
    assert str(card.id) == body["proposal"]["id"]


async def test_the_turn_row_carries_the_spec_aggregates(seeded_client, model_script, database):
    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})
    await seeded_client.post("/turns", json={"text": "gym from April"})

    turn = (await _rows(database, turns_table))[0]
    assert turn.kind == "proposal"
    assert turn.outcome == "proposed"
    assert turn.input_text == "gym from April"
    assert turn.model == "scripted/test-model"
    assert turn.prompt_tokens == 100
    assert turn.completion_tokens == 20
    assert turn.cost == Decimal("0.001")  # one call at the scripted rate
    assert turn.latency_ms is not None
    assert turn.intents[0]["id"] == "gym"


async def test_the_turn_row_records_the_context(seeded_client, model_script):
    model_script.append({"kind": "clarification", "reply": "Which day?", "intents": []})
    await seeded_client.post(
        "/turns", json={"text": "I paid the water bill", "context": "actuals_record"}
    )


# --- the JSON hardening, through the pipeline ------------------------------ #


async def test_unparseable_output_is_re_asked_warmer_and_recorded(
    seeded_client, model_script, database, transport
):
    """Proto T08/T09: a temperature-0 retry reproduces the same broken bytes."""
    model_script.append("this is not JSON")
    model_script.append({"kind": "answer", "reply": "Second time lucky.", "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "hello"})).json()
    assert body["reply"] == "Second time lucky."

    assert [call["temperature"] for call in transport.calls] == [0.0, 0.7]
    rows = await _rows(database, llm_calls)
    assert [r.purpose for r in rows] == ["interpret", "repair"]
    assert rows[0].error


async def test_a_broken_brace_never_reaches_a_retry(seeded_client, model_script, transport):
    """The repair pass fixes it in the transport, so only one call happens."""
    model_script.append('{"kind":"answer","reply":"repaired","intents":[]')
    body = (await seeded_client.post("/turns", json={"text": "hello"})).json()

    assert body["reply"] == "repaired"
    assert len(transport.calls) == 1


async def test_an_unreadable_answer_asks_the_user_to_say_it_again(
    seeded_client, model_script, database
):
    """The provider answered; we could not read it. That is not an outage.

    Telling the user the assistant could not be reached would be false, and the
    interface would repeat it. The turn ends as a clarification in the SPEC
    §5-F1 voice, with the diagnostic attached verbatim.
    """
    model_script.extend(["nope", "still nope", "nope again"])
    response = await seeded_client.post("/turns", json={"text": "hello"})

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "clarification"
    assert "say that again" in body["reply"].lower()
    assert body["diagnostics"][0]["code"] == "CK-E902"

    turn = (await _rows(database, turns_table))[0]
    assert turn.outcome == "clarified"
    assert len(await _rows(database, llm_calls)) == 3


async def test_a_provider_that_cannot_be_reached_fails_loudly(
    seeded_client, model_script, database
):
    """No reply at all is an outage, and it says so."""
    model_script.extend([RuntimeError("connection refused")] * 3)
    response = await seeded_client.post("/turns", json={"text": "hello"})

    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "MODEL_UNAVAILABLE"
    # The journal survives the failure: it is written on its own connection.
    turn = (await _rows(database, turns_table))[0]
    assert turn.outcome == "model_unavailable"
    assert turn.kind == "error"
    assert len(await _rows(database, llm_calls)) == 3


async def test_a_turn_that_fails_any_other_way_is_still_recorded(
    seeded_client, model_script, database, monkeypatch
):
    """A turn nobody can see is a turn nobody can debug — and its spend would
    never count against the SPEC §8 daily budget."""
    from cashkit_service.agent import pipeline

    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})

    def boom(*args, **kwargs):
        raise RuntimeError("the dry-run exploded")

    monkeypatch.setattr(pipeline, "dry_run", boom)
    # The test client re-raises whatever the app raised, which is the point:
    # the request failed outright and the record survived it anyway.
    with pytest.raises(RuntimeError, match="exploded"):
        await seeded_client.post("/turns", json={"text": "gym"})

    turn = (await _rows(database, turns_table))[0]
    assert turn.outcome == "failed"
    assert turn.cost == Decimal("0.001")   # the call it did make still counts


async def test_a_turn_has_a_ceiling_on_model_calls(
    seeded_client, model_script, app, transport, database
):
    """Every loop is bounded on its own; this bounds the whole turn."""
    app.state.settings.llm_max_calls_per_turn = 3
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "runway"}]})
    for _ in range(10):
        model_script.append(
            {"kind": "answer", "reply": "one more", "intents": [{"op": "min_cash"}]}
        )

    body = (await seeded_client.post("/turns", json={"text": "keep looking"})).json()
    assert body["kind"] == "refusal"
    assert len(transport.calls) == 3
    assert (await _rows(database, turns_table))[0].outcome == "call_limit"


# --- the diagnostics repair round (SPEC §2.3 step 4) ----------------------- #


async def test_a_refused_change_gets_one_repair_round(seeded_client, model_script, database):
    """The engine's diagnostic goes back to the model, and it fixes itself."""
    model_script.append(
        {"kind": "answer", "reply": "Raising the rent.",
         "intents": [{"op": "set_amount", "item": "rnt", "amount": "-980.00"}]}
    )
    model_script.append(
        {"kind": "answer", "reply": "Raising the rent.",
         "intents": [{"op": "set_amount", "item": "rent", "amount": "-980.00"}]}
    )
    body = (await seeded_client.post("/turns", json={"text": "rent goes to 980"})).json()

    assert body["kind"] == "proposal"
    assert body["proposal"]["operations"][0]["item"] == "rent"
    rows = await _rows(database, llm_calls)
    assert [r.purpose for r in rows] == ["interpret", "repair"]


async def test_only_one_repair_round_is_spent(seeded_client, model_script, transport):
    """Bounded: the card comes back with the diagnostics rather than looping."""
    broken = {"kind": "answer", "reply": "Trying.",
              "intents": [{"op": "set_amount", "item": "nope", "amount": "-1.00"}]}
    model_script.extend([broken, broken])

    body = (await seeded_client.post("/turns", json={"text": "change nope"})).json()
    assert len(transport.calls) == 2
    assert body["kind"] == "proposal"
    assert any(d["severity"] == "error" for d in body["proposal"]["diagnostics"])


# --- the verification call (SPEC §2.3 step 4, ADR-0030 stage 2) ----------- #


async def test_a_macro_triggers_one_verification_call(seeded_client, model_script, database):
    model_script.append(
        {"kind": "answer", "reply": "Cutting housing by a fifth.",
         "intents": [{"op": "scale_items", "selector": "cat:housing", "factor": "0.8"}]}
    )
    model_script.append({"kind": "answer", "reply": "Confirmed.", "confirmed": True,
                         "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "cut housing 20%"})).json()
    assert body["kind"] == "proposal"
    assert [r.purpose for r in await _rows(database, llm_calls)] == ["interpret", "verify"]


async def test_a_verification_that_corrects_replaces_the_operations(
    seeded_client, model_script, database
):
    """ADR-0030 stage 2: corrective operations become the card, not a second one."""
    model_script.append(
        {"kind": "answer", "reply": "Cutting housing.",
         "intents": [{"op": "scale_items", "selector": "cat:housing", "factor": "0.8"}]}
    )
    model_script.append(
        {"kind": "answer", "reply": "That scaled the wrong lines; using income.",
         "confirmed": False,
         "intents": [{"op": "scale_items", "selector": "cat:income", "factor": "0.8"}]}
    )

    body = (await seeded_client.post("/turns", json={"text": "cut income 20%"})).json()
    assert body["proposal"]["operations"] == [
        {"op": "scale_items", "selector": "cat:income", "factor": "0.8", "scenario": None}
    ]
    assert len(await _rows(database, proposals)) == 1


async def test_a_plain_change_makes_no_verification_call(seeded_client, model_script, database):
    """The triggers are enumerated: an ordinary line does not pay for a call."""
    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})
    await seeded_client.post("/turns", json={"text": "gym"})

    assert [r.purpose for r in await _rows(database, llm_calls)] == ["interpret"]


# --- SPEC §2.4 stamps ------------------------------------------------------ #


async def test_a_card_turn_is_stamped_pending(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "Adding it.", "intents": [GYM]})
    body = (await seeded_client.post("/turns", json={"text": "gym"})).json()

    assert body["what_if"] == {"stamped": True, "reason": "pending", "scenario": "base"}


async def test_a_hypothetical_answer_is_stamped_overlay(seeded_client, model_script):
    """R1's delta runs on a throwaway overlay, so the figure is not base's."""
    model_script.append(
        {"kind": "answer", "reply": "",
         "intents": [{"op": "project_balance", "delta": "-1500.00",
                      "delta_date": "2026-09-15"}]}
    )
    model_script.append({"kind": "answer", "reply": "You would still be short.",
                         "intents": []})

    body = (await seeded_client.post(
        "/turns", json={"text": "can I afford a 1500 laptop in September?"}
    )).json()

    assert body["what_if"]["stamped"] is True
    assert body["what_if"]["reason"] == "overlay"


async def test_a_plain_answer_on_clean_base_is_not_stamped(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "runway"}]})
    model_script.append({"kind": "answer", "reply": "Six months.", "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "runway?"})).json()
    assert body["what_if"] == {"stamped": False, "reason": None, "scenario": None}


async def test_every_turn_payload_carries_provenance(seeded_client, model_script):
    model_script.append({"kind": "answer", "reply": "", "intents": [{"op": "min_cash"}]})
    model_script.append({"kind": "answer", "reply": "Quoted.", "intents": []})

    body = (await seeded_client.post("/turns", json={"text": "lowest?"})).json()
    for key in ("as_of", "scenario", "revision", "engine_version", "what_if", "request_id"):
        assert key in body
    assert body["as_of"] == "2026-03-17"  # host-filled, from the frozen clock


# --- refusals the pipeline owns -------------------------------------------- #


async def test_a_turn_needs_a_book(auth_client, model_script):
    assert (await auth_client.post("/turns", json={"text": "hello"})).status_code == 404


async def test_a_turn_needs_a_session(client):
    assert (await client.post("/turns", json={"text": "hello"})).status_code == 401


async def test_a_service_with_no_model_key_says_so(
    settings, clock, mailer, database, books_root, model_script
):
    from httpx import ASGITransport, AsyncClient

    from cashkit_service.app import create_app
    from cashkit_service.books import BookRuntime

    app = create_app(settings=settings, clock=clock, mailer=mailer, database=database,
                     book_runtime=BookRuntime(books_root), transport=None)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/auth/link", json={"email": "nokey@example.com"})
        token = (await ac.post(
            "/auth/verify", json={"token": mailer.last_for("nokey@example.com").token}
        )).json()["token"]
        ac.headers["Authorization"] = f"Bearer {token}"
        await ac.post("/books", json={"horizon_start": "2026-01-01",
                                      "horizon_end": "2027-01-01",
                                      "opening_balance": "10.00"})
        response = await ac.post("/turns", json={"text": "hello"})
    app.state.books.close_all()

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "MODEL_UNCONFIGURED"
