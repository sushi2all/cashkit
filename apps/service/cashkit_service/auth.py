"""Magic-link authentication and sessions (SPEC §3).

Policy, verbatim from the SPEC: link tokens are single-use with a 15 minute
TTL; sessions last 30 days on mobile and 7 on web with sliding renewal; a new
session does not revoke others; ``DELETE /me`` revokes all.

Only hashes are stored. A stolen database gives an attacker no usable token.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from .clock import Clock
from .config import Settings
from .db import login_tokens, sessions, users
from .errors import invalid_link, unauthorized

TOKEN_BYTES = 32


def new_token() -> str:
    """A fresh opaque token. Never stored, only its hash."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """SHA-256 of the token. Constant-length, constant-time to compare."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)


def normalize_email(email: str) -> str:
    """One address, one account. Case and surrounding space never fork one."""
    return email.strip().lower()


@dataclass(frozen=True)
class Principal:
    """The authenticated caller."""

    user_id: uuid.UUID
    email: str
    session_id: uuid.UUID


async def issue_link_token(
    conn: AsyncConnection, *, email: str, clock: Clock, settings: Settings
) -> str:
    """Create a single-use magic-link token and return it for delivery.

    The token is returned to the caller of this function — the mailer — and
    never to an HTTP client.
    """
    token = new_token()
    now = clock.now()
    await conn.execute(
        login_tokens.insert().values(
            id=uuid.uuid4(),
            email=normalize_email(email),
            token_hash=hash_token(token),
            expires_at=now + _dt.timedelta(minutes=settings.link_token_ttl_minutes),
            consumed_at=None,
            created_at=now,
        )
    )
    return token


async def consume_link_token(
    conn: AsyncConnection, *, token: str, clock: Clock
) -> str:
    """Burn a link token and return the address it was issued for.

    Single-use is enforced by the UPDATE's own WHERE clause: two concurrent
    verifications of the same token cannot both match ``consumed_at IS NULL``.
    """
    now = clock.now()
    result = await conn.execute(
        login_tokens.update()
        .where(
            login_tokens.c.token_hash == hash_token(token),
            login_tokens.c.consumed_at.is_(None),
            login_tokens.c.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(login_tokens.c.email)
    )
    row = result.first()
    if row is None:
        raise invalid_link("This link is expired or already used. Ask for a new one.")
    return row.email


async def upsert_user(conn: AsyncConnection, *, email: str, clock: Clock) -> uuid.UUID:
    """Find the account for an address, creating it on first sign-in."""
    email = normalize_email(email)
    row = (await conn.execute(sa.select(users.c.id).where(users.c.email == email))).first()
    if row is not None:
        return row.id
    user_id = uuid.uuid4()
    await conn.execute(
        users.insert().values(id=user_id, email=email, created_at=clock.now())
    )
    return user_id


def session_ttl(platform: str, settings: Settings) -> _dt.timedelta:
    days = (
        settings.session_ttl_days_mobile
        if platform == "mobile"
        else settings.session_ttl_days_web
    )
    return _dt.timedelta(days=days)


async def open_session(
    conn: AsyncConnection,
    *,
    user_id: uuid.UUID,
    platform: str,
    clock: Clock,
    settings: Settings,
) -> tuple[str, _dt.datetime]:
    """Open a session. Existing sessions of the same user are left alone."""
    token = new_token()
    now = clock.now()
    expires_at = now + session_ttl(platform, settings)
    await conn.execute(
        sessions.insert().values(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=hash_token(token),
            platform=platform,
            expires_at=expires_at,
            created_at=now,
            last_seen_at=now,
        )
    )
    return token, expires_at


async def authenticate(
    conn: AsyncConnection, *, token: str, clock: Clock, settings: Settings
) -> Principal:
    """Resolve a bearer token to a principal, renewing the session slidingly."""
    now = clock.now()
    row = (
        await conn.execute(
            sa.select(
                sessions.c.id,
                sessions.c.user_id,
                sessions.c.platform,
                sessions.c.last_seen_at,
                users.c.email,
            )
            .select_from(sessions.join(users, sessions.c.user_id == users.c.id))
            .where(sessions.c.token_hash == hash_token(token), sessions.c.expires_at > now)
        )
    ).first()
    if row is None:
        raise unauthorized()

    # Sliding renewal, throttled: a session that is already fresh is not worth
    # a write on every request.
    since_seen = now - row.last_seen_at
    if since_seen >= _dt.timedelta(minutes=settings.session_renewal_interval_minutes):
        await conn.execute(
            sessions.update()
            .where(sessions.c.id == row.id)
            .values(last_seen_at=now, expires_at=now + session_ttl(row.platform, settings))
        )
    return Principal(user_id=row.user_id, email=row.email, session_id=row.id)


async def revoke_all_sessions(conn: AsyncConnection, *, user_id: uuid.UUID) -> int:
    result = await conn.execute(sessions.delete().where(sessions.c.user_id == user_id))
    return result.rowcount or 0
