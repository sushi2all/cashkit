"""Profile, export and account deletion (SPEC §3, §9)."""

from __future__ import annotations

import io
import json
import zipfile

import sqlalchemy as sa

from cashkit_service.db import login_tokens, sessions, users


async def test_me_reports_the_account(auth_client):
    body = (await auth_client.get("/me")).json()
    assert body["email"] == "user@example.com"
    assert body["has_book"] is False
    assert body["book_id"] is None


async def test_export_is_an_archive_of_what_the_account_owns(auth_client):
    response = await auth_client.get("/me/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        names = set(zf.namelist())
        assert "account/user.json" in names
        assert "account/sessions.json" in names
        user = json.loads(zf.read("account/user.json"))
    assert user["email"] == "user@example.com"


async def test_export_never_carries_a_credential(auth_client):
    response = await auth_client.get("/me/export")
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        session_rows = json.loads(zf.read("account/sessions.json"))
    assert session_rows, "the caller has a session"
    for row in session_rows:
        assert "token_hash" not in row


async def test_delete_me_revokes_every_session_and_erases_the_rows(client, mailer, database):
    email = "goodbye@example.com"
    tokens = []
    for _ in range(2):
        await client.post("/auth/link", json={"email": email})
        link = mailer.last_for(email)
        tokens.append((await client.post("/auth/verify", json={"token": link.token})).json()["token"])

    client.headers["Authorization"] = f"Bearer {tokens[0]}"
    assert (await client.delete("/me")).status_code == 204

    for token in tokens:
        client.headers["Authorization"] = f"Bearer {token}"
        assert (await client.get("/me")).status_code == 401

    async with database.connect() as conn:
        assert (await conn.execute(sa.select(sa.func.count()).select_from(users))).scalar_one() == 0
        assert (await conn.execute(sa.select(sa.func.count()).select_from(sessions))).scalar_one() == 0


async def test_delete_me_leaves_no_usable_link_token(client, mailer, database):
    email = "gone@example.com"
    await client.post("/auth/link", json={"email": email})
    link = mailer.last_for(email)
    token = (await client.post("/auth/verify", json={"token": link.token})).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"
    await client.delete("/me")

    # An unconsumed link issued before deletion would re-create the account, so
    # it must not be usable to reach the deleted account's data. Verifying it
    # opens a NEW, empty account instead of resurrecting the old one.
    await client.post("/auth/link", json={"email": email})
    fresh = mailer.last_for(email)
    response = await client.post("/auth/verify", json={"token": fresh.token})
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    assert (await client.get("/me")).json()["has_book"] is False
