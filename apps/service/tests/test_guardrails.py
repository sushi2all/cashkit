"""Cost and rate guardrails (SPEC §8), demonstrably enforced.

The gate wording is "cost + rate limits demonstrably enforced", which a config
value cannot satisfy: these tests drive the limits until they trip, and check
that the turn stops **before** the model call, not after paying for it.
"""

from __future__ import annotations

import datetime as _dt
import re
import uuid
from decimal import Decimal

import pytest
import sqlalchemy as sa

from cashkit_service.agent import budget
from cashkit_service.db import import_jobs, turns as turns_table

ANSWER = {"kind": "answer", "reply": "Six months.", "intents": []}


async def _turn(client, text: str = "how long does my money last?"):
    return await client.post("/turns", json={"text": text})


async def _outcomes(database) -> list[str]:
    async with database.connect() as conn:
        rows = await conn.execute(
            sa.select(turns_table.c.outcome).order_by(turns_table.c.created_at)
        )
    return [row.outcome for row in rows]


# --- the hourly rate limit ------------------------------------------------- #


async def test_the_hourly_turn_limit_is_enforced(seeded_client, app, model_script, transport, database):
    app.state.settings.turns_per_hour = 3
    model_script.extend([ANSWER, ANSWER, ANSWER])

    for _ in range(3):
        assert (await _turn(seeded_client)).json()["kind"] == "answer"

    refused = await _turn(seeded_client)
    body = refused.json()

    assert body["kind"] == "refusal"
    assert body["retry_after_seconds"] > 0
    # The refusal happened before the model call: the script still has nothing
    # in it and the transport was never asked a fourth time.
    assert len(transport.calls) == 3
    assert await _outcomes(database) == ["answered", "answered", "answered", "rate_limited"]


async def test_the_limit_lifts_with_the_hour(seeded_client, app, model_script, clock):
    app.state.settings.turns_per_hour = 1
    model_script.append(ANSWER)
    assert (await _turn(seeded_client)).json()["kind"] == "answer"
    assert (await _turn(seeded_client)).json()["kind"] == "refusal"

    clock.advance(hours=1, minutes=1)
    model_script.append(ANSWER)
    assert (await _turn(seeded_client)).json()["kind"] == "answer"


# --- the daily model budget ------------------------------------------------ #


async def test_the_daily_budget_is_enforced(seeded_client, app, model_script, transport, database):
    """SPEC §8: over budget → the turn refuses politely with a retry tomorrow."""
    app.state.settings.daily_model_budget_usd = Decimal("0.0025")
    transport.cost_per_call = Decimal("0.001")
    model_script.extend([ANSWER, ANSWER, ANSWER])

    for _ in range(3):
        assert (await _turn(seeded_client)).json()["kind"] == "answer"

    body = (await _turn(seeded_client)).json()
    assert body["kind"] == "refusal"
    assert "tomorrow" in body["reply"].lower()
    assert len(transport.calls) == 3
    assert (await _outcomes(database))[-1] == "over_budget"


async def test_the_budget_resets_the_next_day(seeded_client, app, model_script, transport, clock):
    app.state.settings.daily_model_budget_usd = Decimal("0.0005")
    transport.cost_per_call = Decimal("0.001")
    model_script.append(ANSWER)
    await _turn(seeded_client)
    assert (await _turn(seeded_client)).json()["kind"] == "refusal"

    clock.advance(days=1)
    model_script.append(ANSWER)
    assert (await _turn(seeded_client)).json()["kind"] == "answer"


async def test_a_refusal_costs_nothing_and_is_still_a_turn(seeded_client, app, database):
    app.state.settings.turns_per_hour = 0
    body = (await _turn(seeded_client)).json()

    assert body["kind"] == "refusal"
    assert body["llm_calls"] == 0
    assert body["turn_id"]
    async with database.connect() as conn:
        row = (await conn.execute(sa.select(turns_table))).one()
    assert row.cost == Decimal("0")
    assert row.kind == "refusal"


async def test_one_users_spend_does_not_limit_another(
    seeded_client, client, mailer, app, model_script
):
    app.state.settings.turns_per_hour = 1
    model_script.append(ANSWER)
    await _turn(seeded_client)
    assert (await _turn(seeded_client)).json()["kind"] == "refusal"

    await client.post("/auth/link", json={"email": "second@example.com"})
    token = (await client.post(
        "/auth/verify", json={"token": mailer.last_for("second@example.com").token}
    )).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    await client.post("/books", json={"horizon_start": "2026-01-01",
                                      "horizon_end": "2027-01-01",
                                      "opening_balance": "100.00"})

    model_script.append(ANSWER)
    assert (await _turn(client)).json()["kind"] == "answer"


# --- the import limit (S5 wires the endpoint; the counter is here) --------- #


async def test_five_imports_a_day_is_enforced(database, clock, settings, book_client):
    book_id = await _book_id(database)
    async with database.connect() as conn:
        assert await budget.check_import(
            conn, book_id=book_id, clock=clock, settings=settings
        ) is None
        for _ in range(settings.imports_per_day):
            await conn.execute(
                import_jobs.insert().values(
                    id=uuid.uuid4(), book_id=book_id, status="done",
                    report={}, created_at=clock.now(),
                )
            )
        refusal = await budget.check_import(
            conn, book_id=book_id, clock=clock, settings=settings
        )
    assert refusal is not None
    assert refusal.outcome == "import_rate_limited"


async def test_yesterdays_imports_do_not_count(database, clock, settings, book_client):
    book_id = await _book_id(database)
    async with database.connect() as conn:
        for _ in range(settings.imports_per_day + 2):
            await conn.execute(
                import_jobs.insert().values(
                    id=uuid.uuid4(), book_id=book_id, status="done", report={},
                    created_at=clock.now() - _dt.timedelta(days=1),
                )
            )
        assert await budget.check_import(
            conn, book_id=book_id, clock=clock, settings=settings
        ) is None


async def _book_id(database) -> uuid.UUID:
    from cashkit_service.db import books

    async with database.connect() as conn:
        return (await conn.execute(sa.select(books.c.id))).scalar_one()


# --- the SPEC §8 defaults and the SPEC §5-F1 voice ------------------------- #


def test_the_documented_defaults_are_the_defaults(settings):
    assert settings.daily_model_budget_usd == Decimal("0.50")
    assert settings.turns_per_hour == 30
    assert settings.imports_per_day == 5


@pytest.mark.parametrize(
    "refusal",
    [
        budget.Refusal("rate_limited", "You have used this hour's 30 turns. "
                                       "Try again in a little while.", 60),
        budget.Refusal("over_budget", "Today's model budget is used up. Ask again "
                                      "tomorrow, or read the book directly in the "
                                      "meantime.", 60),
        budget.Refusal("import_rate_limited", "You have run today's 5 imports. "
                                              "Try again tomorrow.", 60),
    ],
)
def test_refusal_copy_follows_the_voice_rule(refusal):
    """SPEC §5-F1, D-MLP-05(c): two short sentences, no apology, no hedging."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", refusal.reply.strip()) if s]
    assert len(sentences) <= 2, refusal.reply
    lowered = refusal.reply.lower()
    for boilerplate in ("sorry", "apolog", "unfortunately", "i'm afraid", "perhaps", "maybe"):
        assert boilerplate not in lowered, refusal.reply
