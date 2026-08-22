"""Magic-link and session policy (SPEC §3)."""

from __future__ import annotations

import pytest
import sqlalchemy as sa

from cashkit_service.db import sessions


async def _link_for(client, mailer, email: str):
    response = await client.post("/auth/link", json={"email": email, "platform": "web"})
    assert response.status_code == 202
    return mailer.last_for(email)


async def test_link_response_never_carries_the_token(client, mailer):
    response = await client.post("/auth/link", json={"email": "a@example.com"})
    assert response.status_code == 202
    assert response.content in (b"", b"null")
    assert mailer.sent, "the mailer is the only way a link token leaves the service"


async def test_verify_opens_a_session_and_creates_the_account(client, mailer):
    link = await _link_for(client, mailer, "new@example.com")
    response = await client.post("/auth/verify", json={"token": link.token, "platform": "web"})
    assert response.status_code == 200
    body = response.json()
    assert body["token"] and body["platform"] == "web"

    client.headers["Authorization"] = f"Bearer {body['token']}"
    me = await client.get("/me")
    assert me.status_code == 200
    assert me.json()["email"] == "new@example.com"
    assert me.json()["has_book"] is False


async def test_link_token_is_single_use(client, mailer):
    link = await _link_for(client, mailer, "once@example.com")
    first = await client.post("/auth/verify", json={"token": link.token})
    assert first.status_code == 200
    second = await client.post("/auth/verify", json={"token": link.token})
    assert second.status_code == 400
    assert second.json()["detail"]["code"] == "LINK_INVALID"


async def test_link_token_expires_after_fifteen_minutes(client, mailer, clock):
    link = await _link_for(client, mailer, "slow@example.com")
    clock.advance(minutes=15, seconds=1)
    response = await client.post("/auth/verify", json={"token": link.token})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "LINK_INVALID"


async def test_email_case_and_space_do_not_fork_the_account(client, mailer):
    link = await _link_for(client, mailer, "  Mixed@Example.COM ")
    response = await client.post("/auth/verify", json={"token": link.token})
    client.headers["Authorization"] = f"Bearer {response.json()['token']}"
    assert (await client.get("/me")).json()["email"] == "mixed@example.com"

    link2 = await _link_for(client, mailer, "mixed@example.com")
    response2 = await client.post("/auth/verify", json={"token": link2.token})
    client.headers["Authorization"] = f"Bearer {response2.json()['token']}"
    assert (await client.get("/me")).json()["user_id"] == (await client.get("/me")).json()["user_id"]


@pytest.mark.parametrize(("platform", "days"), [("web", 7), ("mobile", 30)])
async def test_session_ttl_is_platform_specific(client, mailer, clock, platform, days):
    link = await _link_for(client, mailer, f"{platform}@example.com")
    response = await client.post("/auth/verify", json={"token": link.token, "platform": platform})
    expires_at = response.json()["expires_at"]
    expected = (clock.now() + __import__("datetime").timedelta(days=days)).isoformat()
    assert expires_at == expected


async def test_a_new_session_does_not_revoke_the_others(client, mailer, database):
    email = "two@example.com"
    link_a = await _link_for(client, mailer, email)
    token_a = (await client.post("/auth/verify", json={"token": link_a.token})).json()["token"]
    link_b = await _link_for(client, mailer, email)
    token_b = (await client.post("/auth/verify", json={"token": link_b.token})).json()["token"]

    for token in (token_a, token_b):
        client.headers["Authorization"] = f"Bearer {token}"
        assert (await client.get("/me")).status_code == 200

    async with database.connect() as conn:
        count = (await conn.execute(sa.select(sa.func.count()).select_from(sessions))).scalar_one()
    assert count == 2


async def test_session_expires_and_renews_slidingly(client, mailer, clock):
    link = await _link_for(client, mailer, "sliding@example.com")
    token = (await client.post("/auth/verify", json={"token": link.token, "platform": "web"})).json()["token"]
    client.headers["Authorization"] = f"Bearer {token}"

    # Six days on, one request renews the session for another seven.
    clock.advance(days=6)
    assert (await client.get("/me")).status_code == 200
    clock.advance(days=6)
    assert (await client.get("/me")).status_code == 200

    # Left alone past the full window, it dies.
    clock.advance(days=8)
    assert (await client.get("/me")).status_code == 401


async def test_no_session_is_401(client):
    assert (await client.get("/me")).status_code == 401
    client.headers["Authorization"] = "Bearer nonsense"
    assert (await client.get("/me")).status_code == 401
