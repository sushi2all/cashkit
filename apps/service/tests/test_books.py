"""Book lifecycle (SPEC §3 ``POST /books``)."""

from __future__ import annotations

import sqlalchemy as sa

from cashkit_service.db import books


async def test_create_book_gives_it_a_first_revision(auth_client, database):
    response = await auth_client.post(
        "/books",
        json={"horizon_start": "2026-01-01", "horizon_end": "2027-01-01", "opening_balance": "2500.00"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["active_scenario"] == "base"
    # create_book commits nothing; the service gives the empty book a revision
    # so no payload ever ships a null provenance (D-MLP-11).
    assert body["revision"], "a new book must already have a revision"

    async with database.connect() as conn:
        count = (await conn.execute(sa.select(sa.func.count()).select_from(books))).scalar_one()
    assert count == 1


async def test_me_reports_the_book(book_client):
    body = (await book_client.get("/me")).json()
    assert body["has_book"] is True
    assert body["active_scenario"] == "base"


async def test_one_book_per_user(book_client):
    response = await book_client.post(
        "/books",
        json={"horizon_start": "2026-01-01", "horizon_end": "2027-01-01", "opening_balance": "1.00"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "BOOK_EXISTS"


async def test_a_backwards_horizon_is_refused(auth_client):
    response = await auth_client.post(
        "/books",
        json={"horizon_start": "2027-01-01", "horizon_end": "2026-01-01", "opening_balance": "1.00"},
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "BAD_HORIZON"


async def test_opening_balance_is_a_string_never_a_float(auth_client):
    """A JSON number would already be a float before Pydantic saw it."""
    response = await auth_client.post(
        "/books",
        json={"horizon_start": "2026-01-01", "horizon_end": "2027-01-01", "opening_balance": 2500.10},
    )
    assert response.status_code == 422


async def test_creating_a_book_needs_a_session(client):
    response = await client.post(
        "/books",
        json={"horizon_start": "2026-01-01", "horizon_end": "2027-01-01", "opening_balance": "1.00"},
    )
    assert response.status_code == 401


async def test_delete_me_removes_the_book_directory(book_client, books_root):
    assert any(books_root.iterdir())
    assert (await book_client.delete("/me")).status_code == 204
    assert not any(books_root.iterdir())
